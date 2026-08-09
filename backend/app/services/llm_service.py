"""
LLM API 调用封装
支持 Anthropic / DeepSeek 双后端，统一错误处理 + 重试 + 超时。
"""
import asyncio
import logging
import anthropic
from openai import AsyncOpenAI
from app.config import settings
from app.utils.exceptions import LLMAPIError, TokenLimitError, TaskTimeoutError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt 模板 —— 短剧剧本生成链路
# ---------------------------------------------------------------------------

OUTLINE_SYSTEM_PROMPT = """你是一位资深的短剧编剧。根据用户提供的原著小说片段，生成一份完整的短剧改编大纲。

要求：
1. 分析原著的故事线，提取适合改编为短剧的核心情节
2. 输出 3-5 幕结构的大纲，每幕包含标题和内容概述
3. 标注每幕的建议时长（短剧单集 1-3 分钟）
4. 明确故事的起承转合和高潮点
5. 输出为 Markdown 格式"""

CHARACTER_SYSTEM_PROMPT = """你是一位专业的影视角色设计师。根据剧本大纲和原著内容，设计剧中主要人物角色。

要求：
1. 为每个角色输出：角色名、年龄、身份、性格特征、外貌描述、背景故事、角色弧线
2. 区分主角、配角、反派
3. 每个角色以 "## 角色名" 开头，下面用结构化描述
4. 输出为 Markdown 格式"""

STORYBOARD_SYSTEM_PROMPT = """你是一位专业的影视分镜师，专为 AI 漫剧制作分镜脚本。根据剧本大纲和人物设定，生成详细的分镜脚本。

你必须输出严格 JSON 数组格式，不要输出任何其他内容：

```json
[
  {
    "scene_number": 1,
    "scene_title": "分镜标题（10字以内）",
    "location": "场景地点",
    "time_of_day": "白天/夜晚/黄昏/清晨",
    "characters_in_scene": ["角色名1", "角色名2"],
    "shot_type": "特写/中景/远景",
    "camera_movement": "运镜方式（如：缓慢推进、轻微横摇、固定机位等）",
    "action_instruction": "角色动作指令（眨眼、发丝飘动、轻微转身、呼吸起伏等轻动态）",
    "dialogue": "角色A：台词内容\\n角色B：台词内容",
    "visual_description": "画面描述：场景细节、人物动作、光线氛围、画面色调（暖黄/冷蓝/暗金/柔粉）",
    "duration_seconds": 5.0
  }
]
```

规则：
1. 输出 5-12 个分镜，覆盖大纲的核心情节
2. shot_type 必须交替使用特写、中景、远景，避免连续出现同类型镜头
3. action_instruction 只设计轻动态动作：眨眼、发丝飘动、轻微转身、呼吸起伏、指尖轻叩、衣袖摆动。禁止大幅度打斗、快速奔跑
4. visual_description 要详尽，包含画面色调（如"暖黄色灯光笼罩""冷蓝色月光洒落""暗金色夕阳""柔和粉色晨曦"），可用于 AI 图片生成
5. dialogue 保留原著核心台词，标注说话角色
6. camera_movement 使用专业术语，以缓慢、轻微的运动为主
7. duration_seconds 建议 5-8 秒/分镜
8. 仅输出 JSON 数组，不要有任何解释或 markdown 标记"""

# ---------------------------------------------------------------------------
# Token 估算常量（粗略：中文约 1.5 字符/token，英文约 4 字符/token）
# ---------------------------------------------------------------------------
CHARS_PER_TOKEN_ESTIMATE = 2.0       # 保守估算
MAX_INPUT_TOKENS_ESTIMATE = 80_000   # 保守安全限制


class LLMService:
    """
    封装 LLM API 调用，统一错误处理 + 重试 + 超时。

    支持两种后端：
    - anthropic: Claude API（需代理）
    - deepseek:  DeepSeek API（国内直连，OpenAI 兼容）

    三个生成阶段对应级联链路的不同环节：
    - generate_outline:    原著文本 → 剧本大纲
    - generate_characters: 大纲+原文 → 人物角色设定
    - generate_storyboard: 大纲+人物 → 分镜脚本
    """

    def __init__(self):
        self.provider = settings.llm_provider

        if self.provider == "deepseek":
            self.client = AsyncOpenAI(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
            )
            self.model = settings.deepseek_model
        elif self.provider == "anthropic":
            client_kwargs = {"api_key": settings.anthropic_api_key}
            if settings.anthropic_base_url:
                client_kwargs["base_url"] = settings.anthropic_base_url
            self.client = anthropic.AsyncAnthropic(**client_kwargs)
            self.model = settings.anthropic_model
        else:
            raise ValueError(f"不支持的 LLM provider: {self.provider}，可选 anthropic | deepseek")

        logger.info(f"LLM Service 初始化: provider={self.provider}, model={self.model}")

    # ------------------------------------------------------------------
    # Token 估算
    # ------------------------------------------------------------------

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """粗略估算文本 token 数量"""
        if not text:
            return 0
        chinese_chars = sum(1 for c in text if '一' <= c <= '鿿')
        other_chars = len(text) - chinese_chars
        return int(chinese_chars / 1.5 + other_chars / 4.0)

    @staticmethod
    def _check_token_budget(user_message: str, max_tokens: int) -> None:
        """检查输入 token 是否可能在安全范围内（估算警告，由 API 精确校验兜底）"""
        estimated = LLMService._estimate_tokens(user_message)
        if estimated > MAX_INPUT_TOKENS_ESTIMATE:
            logger.warning(
                f"Token 估算偏高: 约 {estimated} tokens（上限 {MAX_INPUT_TOKENS_ESTIMATE}），"
                "已自动截断敏感区域。如仍然超限，API 层将捕获并提示。"
            )

    # ------------------------------------------------------------------
    # 底层调用封装（带重试 + 超时）
    # ------------------------------------------------------------------

    async def _call_anthropic(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Anthropic 原生 API 调用"""
        client: anthropic.AsyncAnthropic = self.client
        response = await client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        text_blocks = [
            block.text
            for block in response.content
            if hasattr(block, "text") and block.text
        ]
        return "\n".join(text_blocks) if text_blocks else ""

    async def _call_openai_compatible(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        """OpenAI 兼容 API 调用（DeepSeek 等）"""
        client: AsyncOpenAI = self.client
        response = await client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        choice = response.choices[0]
        return choice.message.content or ""

    async def _call_llm(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> str:
        """统一 LLM 调用入口，负责重试、超时、异常映射"""
        max_retries = settings.llm_max_retries
        base_delay = settings.llm_retry_base_delay
        call_timeout = settings.llm_call_timeout

        self._check_token_budget(user_message, max_tokens)

        last_error = None

        for attempt in range(max_retries + 1):
            try:
                logger.info(
                    f"LLM 调用 (attempt {attempt + 1}/{max_retries + 1})"
                    f" | provider={self.provider} | model={self.model} | max_tokens={max_tokens}"
                )

                # 根据 provider 选择调用方式
                if self.provider == "deepseek":
                    call_fn = self._call_openai_compatible
                else:
                    call_fn = self._call_anthropic

                result = await asyncio.wait_for(
                    call_fn(system_prompt, user_message, max_tokens, temperature),
                    timeout=call_timeout,
                )

                logger.info(f"LLM 调用成功 (attempt {attempt + 1}) | 返回 {len(result)} 字符")
                return result

            except asyncio.TimeoutError:
                last_error = TaskTimeoutError(
                    task_id="(llm_call)",
                    stage=f"API 调用超时 ({call_timeout}s)"
                )
                logger.warning(f"LLM 超时 (attempt {attempt + 1})")

            except (anthropic.BadRequestError, Exception) as e:
                error_str = str(e)

                # Anthropic 特定异常 >= 0.49.0
                if isinstance(e, anthropic.BadRequestError) or (
                    hasattr(anthropic, "BadRequestError") and isinstance(e, anthropic.BadRequestError)
                ):
                    if hasattr(anthropic, "BadRequestError"):
                        if "token" in error_str.lower() or "maximum context" in error_str.lower():
                            raise TokenLimitError(
                                f"Token 超出限制。建议缩短输入文本或减少分片数。"
                                f" 详情: {error_str[:200]}"
                            )
                    last_error = LLMAPIError(f"请求参数错误: {error_str[:200]}")
                    raise last_error  # 4xx 不重试

                # Anthropic RateLimitError / APIStatusError
                if hasattr(anthropic, "RateLimitError") and isinstance(e, anthropic.RateLimitError):
                    last_error = LLMAPIError(f"API 频率限制: {error_str[:200]}")
                    logger.warning(f"Rate limit (attempt {attempt + 1})")
                elif hasattr(anthropic, "APIStatusError") and isinstance(e, anthropic.APIStatusError):
                    status = e.status_code
                    if 500 <= status < 600:
                        last_error = LLMAPIError(f"服务端错误 (status={status}): {error_str[:200]}")
                        logger.warning(f"API {status} error (attempt {attempt + 1})")
                    else:
                        raise LLMAPIError(f"API 返回错误 (status={status}): {error_str[:200]}")
                elif hasattr(anthropic, "APIConnectionError") and isinstance(e, anthropic.APIConnectionError):
                    last_error = LLMAPIError(f"无法连接 API: {error_str[:200]}")
                    logger.warning(f"Connection error (attempt {attempt + 1})")
                else:
                    # OpenAI SDK exceptions 或未知错误
                    error_str_lower = error_str.lower()
                    if any(kw in error_str_lower for kw in ["token", "maximum context", "context length", "max_tokens"]):
                        raise TokenLimitError(
                            f"Token 超出限制。建议缩短输入文本或减少分片数。"
                            f" 详情: {error_str[:200]}"
                        )
                    if any(kw in error_str_lower for kw in ["rate_limit", "rate limit", "too many requests"]):
                        last_error = LLMAPIError(f"API 频率限制: {error_str[:200]}")
                        logger.warning(f"Rate limit (attempt {attempt + 1})")
                    elif any(kw in error_str_lower for kw in ["401", "403", "unauthorized", "forbidden"]):
                        raise LLMAPIError(f"API 认证失败: {error_str[:200]}")
                    elif any(kw in error_str_lower for kw in ["500", "502", "503", "504", "server error"]):
                        last_error = LLMAPIError(f"服务端错误: {error_str[:200]}")
                        logger.warning(f"Server error (attempt {attempt + 1})")
                    elif any(kw in error_str_lower for kw in ["connection", "timeout", "refused"]):
                        last_error = LLMAPIError(f"无法连接 API: {error_str[:200]}")
                        logger.warning(f"Connection error (attempt {attempt + 1})")
                    else:
                        last_error = LLMAPIError(f"未知错误: {error_str[:200]}")
                        logger.exception(f"Unexpected error (attempt {attempt + 1})")

            # ── 指数退避（最后一次不等待） ──
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.info(f"重试等待 {delay:.1f}s...")
                await asyncio.sleep(delay)

        raise last_error if last_error else LLMAPIError("API 调用失败（原因未知）")

    # ------------------------------------------------------------------
    # 级联链路三个阶段
    # ------------------------------------------------------------------

    async def generate_outline(self, text_chunks: list[str]) -> str:
        combined = "\n\n---\n\n".join(text_chunks)
        truncated_note = ""
        if len(text_chunks) < getattr(self, "_total_chunks", len(text_chunks)):
            truncated_note = "（注：原文较长，以上为部分摘要）\n\n"

        user_message = (
            f"以下是一部小说的文本内容，请根据此内容生成短剧改编大纲：\n\n"
            f"{truncated_note}{combined}\n\n"
            f"请生成完整的大纲，包括每幕的标题、内容概述和建议时长。"
        )
        return await self._call_llm(OUTLINE_SYSTEM_PROMPT, user_message, max_tokens=4096)

    async def generate_characters(self, outline: str, source_text: str) -> str:
        estimated = self._estimate_tokens(outline)
        source_budget = max(10000, MAX_INPUT_TOKENS_ESTIMATE - estimated - 2000)
        source_chars = int(source_budget * CHARS_PER_TOKEN_ESTIMATE)

        source_excerpt = source_text[:source_chars]
        if len(source_text) > source_chars:
            source_excerpt += f"\n\n（原文共 {len(source_text)} 字符，已智能截取前 {source_chars} 字符）"

        user_message = (
            f"## 剧本大纲\n{outline}\n\n"
            f"## 原著文本（摘要）\n{source_excerpt}\n\n"
            f"请根据以上大纲和原著信息，设计剧中主要人物角色。"
        )
        return await self._call_llm(CHARACTER_SYSTEM_PROMPT, user_message, max_tokens=4096)

    async def generate_storyboard(self, outline: str, characters: str) -> str:
        estimated = self._estimate_tokens(outline)
        chars_budget = max(8000, MAX_INPUT_TOKENS_ESTIMATE - estimated - 2000)
        allowed = int(chars_budget * CHARS_PER_TOKEN_ESTIMATE)

        characters_excerpt = characters[:allowed]
        if len(characters) > allowed:
            characters_excerpt += "\n\n（角色设定内容较长，已截取前段）"

        outline_excerpt = outline
        if len(outline) > 4000:
            outline_excerpt = outline[:4000] + "\n\n（大纲较长，已截取前4000字）"
            logger.info("大纲过长，已截断至 4000 字符用于分镜生成")

        user_message = (
            f"## 剧本大纲\n{outline_excerpt}\n\n"
            f"## 人物角色设定\n{characters_excerpt}\n\n"
            f"请根据以上大纲和人物设定，生成 JSON 格式的分镜脚本数组。"
        )
        return await self._call_llm(
            STORYBOARD_SYSTEM_PROMPT,
            user_message,
            max_tokens=8192,
            temperature=0.6,
        )


# 全局单例
llm_service = LLMService()
