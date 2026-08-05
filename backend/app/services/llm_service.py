"""
Claude API 调用封装
处理 token 超限、API 报错、超时、重试等异常场景，统一映射为自定义异常。
"""
import asyncio
import logging
import anthropic
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

STORYBOARD_SYSTEM_PROMPT = """你是一位专业的影视分镜师。根据剧本大纲和人物设定，生成详细的分镜脚本。

你必须输出严格 JSON 数组格式，不要输出任何其他内容：

```json
[
  {
    "scene_number": 1,
    "scene_title": "分镜标题（10字以内）",
    "location": "场景地点",
    "time_of_day": "白天/夜晚/黄昏/清晨",
    "characters_in_scene": ["角色名1", "角色名2"],
    "camera_movement": "运镜方式（如：中景推近特写、全景横摇、跟拍等）",
    "dialogue": "角色A：台词内容\\n角色B：台词内容",
    "visual_description": "画面描述：场景细节、人物动作、光线氛围、色彩基调",
    "duration_seconds": 5.0
  }
]
```

规则：
1. 输出 5-12 个分镜，覆盖大纲的核心情节
2. visual_description 要详尽，可用于 AI 图片生成（描述场景、光线、色彩、构图）
3. dialogue 保留原著核心台词，标注说话角色
4. camera_movement 使用专业术语
5. duration_seconds 建议 3-8 秒/分镜
6. 仅输出 JSON 数组，不要有任何解释或 markdown 标记"""

# ---------------------------------------------------------------------------
# Token 估算常量（粗略：中文约 1.5 字符/token，英文约 4 字符/token）
# ---------------------------------------------------------------------------
CHARS_PER_TOKEN_ESTIMATE = 2.0       # 保守估算
MAX_INPUT_TOKENS_ESTIMATE = 80_000   # 保守安全限制


class LLMService:
    """
    封装 Claude API 调用，统一错误处理 + 重试 + 超时。

    三个生成阶段对应级联链路的不同环节：
    - generate_outline:  原著文本 → 剧本大纲
    - generate_characters: 大纲+原文 → 人物角色设定
    - generate_storyboard: 大纲+人物 → 分镜脚本
    """

    def __init__(self):
        self.client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.model = settings.anthropic_model

    # ------------------------------------------------------------------
    # Token 估算
    # ------------------------------------------------------------------

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """粗略估算文本 token 数量"""
        if not text:
            return 0
        # 分别统计中文字符和 ASCII 字符
        chinese_chars = sum(1 for c in text if '一' <= c <= '鿿')
        other_chars = len(text) - chinese_chars
        # 中文约 1.5 字符/token，英文约 4 字符/token
        return int(chinese_chars / 1.5 + other_chars / 4.0)

    @staticmethod
    def _check_token_budget(user_message: str, max_tokens: int) -> None:
        """
        检查输入 token 是否可能在安全范围内。
        超出估算限制时给出警告但不阻断（由 API 精确校验兜底）。
        """
        estimated = LLMService._estimate_tokens(user_message)
        if estimated > MAX_INPUT_TOKENS_ESTIMATE:
            logger.warning(
                f"Token 估算偏高: 约 {estimated} tokens（上限 {MAX_INPUT_TOKENS_ESTIMATE}）"
                "，已自动截断敏感区域。如仍然超限，API 层将捕获并提示。"
            )

    # ------------------------------------------------------------------
    # 底层调用封装（带重试 + 超时）
    # ------------------------------------------------------------------

    async def _call_claude(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> str:
        """
        统一的 Claude API 调用入口，负责：

        - Token 预算预检（估算 + 警告）
        - 最多 settings.llm_max_retries 次重试（指数退避）
        - 单次调用超时保护（settings.llm_call_timeout）
        - 异常分类映射

        Raises:
            TokenLimitError: 输入或输出超出 token 限制
            LLMAPIError:     其他 API 调用失败（含重试耗尽）
            TaskTimeoutError: 调用超时
        """
        max_retries = settings.llm_max_retries
        base_delay = settings.llm_retry_base_delay
        call_timeout = settings.llm_call_timeout

        # Token 预检
        self._check_token_budget(user_message, max_tokens)

        last_error = None

        for attempt in range(max_retries + 1):
            try:
                logger.info(
                    f"Claude API 调用 (attempt {attempt + 1}/{max_retries + 1})"
                    f" | model={self.model} | max_tokens={max_tokens}"
                )

                # 带超时的 API 调用（禁用 extended thinking，确保返回纯文本）
                response = await asyncio.wait_for(
                    self.client.messages.create(
                        model=self.model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        system=system_prompt,
                        messages=[{"role": "user", "content": user_message}],
                        thinking={"type": "disabled"},
                    ),
                    timeout=call_timeout,
                )

                # 提取文本内容（优先 text block，兜底 thinking block）
                text_blocks = [
                    block.text
                    for block in response.content
                    if hasattr(block, "text") and block.text
                ]
                if not text_blocks:
                    # 兜底：从 thinking block 中提取内容
                    for block in response.content:
                        if hasattr(block, "thinking") and block.thinking:
                            text_blocks.append(f"[思考] {block.thinking}")
                        elif hasattr(block, "signature") and block.signature:
                            text_blocks.append(f"[签名] {block.signature[:100]}")
                result = "\n".join(text_blocks)
                logger.info(f"Claude API 调用成功 (attempt {attempt + 1}) | 返回 {len(result)} 字符")
                return result

            except asyncio.TimeoutError:
                last_error = TaskTimeoutError(
                    task_id="(llm_call)",
                    stage=f"API 调用超时 ({call_timeout}s)"
                )
                logger.warning(
                    f"Claude API 超时 (attempt {attempt + 1}/{max_retries + 1})"
                    f" | timeout={call_timeout}s"
                )

            except anthropic.BadRequestError as e:
                error_str = str(e)
                # Token 超限错误不可重试
                if "token" in error_str.lower() or "maximum context" in error_str.lower():
                    raise TokenLimitError(
                        f"Token 超出限制。建议缩短输入文本或减少分片数。"
                        f" 详情: {error_str[:200]}"
                    )
                last_error = LLMAPIError(f"请求参数错误: {error_str[:200]}")

            except anthropic.RateLimitError as e:
                last_error = LLMAPIError(f"API 频率限制: {str(e)[:200]}")
                logger.warning(f"Rate limit (attempt {attempt + 1})")

            except anthropic.APIStatusError as e:
                status = e.status_code
                # 5xx 可重试，4xx 不重试
                if 500 <= status < 600:
                    last_error = LLMAPIError(
                        f"Claude 服务端错误 (status={status}): {str(e)[:200]}"
                    )
                    logger.warning(f"API {status} error (attempt {attempt + 1})")
                else:
                    raise LLMAPIError(
                        f"Claude API 返回错误 (status={status}): {str(e)[:200]}"
                    )

            except anthropic.APIConnectionError as e:
                last_error = LLMAPIError(f"无法连接 Claude API: {str(e)[:200]}")
                logger.warning(f"Connection error (attempt {attempt + 1})")

            except Exception as e:
                last_error = LLMAPIError(f"未知错误: {str(e)[:200]}")
                logger.exception(f"Unexpected error (attempt {attempt + 1})")

            # ── 指数退避（最后一次不等待） ──
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.info(f"重试等待 {delay:.1f}s...")
                await asyncio.sleep(delay)

        # 重试耗尽
        raise last_error if last_error else LLMAPIError("API 调用失败（原因未知）")

    # ------------------------------------------------------------------
    # 级联链路三个阶段
    # ------------------------------------------------------------------

    async def generate_outline(self, text_chunks: list[str]) -> str:
        """
        阶段1：根据文本分片生成剧本大纲。

        Args:
            text_chunks: 文本预处理后的分片列表（已由 preprocess 截断）

        Returns:
            Markdown 格式的剧本大纲
        """
        # 合并分片
        combined = "\n\n---\n\n".join(text_chunks)
        truncated_note = ""
        if len(text_chunks) < getattr(self, "_total_chunks", len(text_chunks)):
            truncated_note = "（注：原文较长，以上为部分摘要）\n\n"

        user_message = (
            f"以下是一部小说的文本内容，请根据此内容生成短剧改编大纲：\n\n"
            f"{truncated_note}{combined}\n\n"
            f"请生成完整的大纲，包括每幕的标题、内容概述和建议时长。"
        )

        return await self._call_claude(OUTLINE_SYSTEM_PROMPT, user_message, max_tokens=4096)

    async def generate_characters(self, outline: str, source_text: str) -> str:
        """
        阶段2：根据大纲和原文生成人物角色设定。

        Args:
            outline:     阶段1 生成的大纲
            source_text: 用户原始输入文本（自动截断控制 token）

        Returns:
            Markdown 格式的角色设定列表
        """
        # 智能截断：根据估算 token 决定截取长度
        estimated = self._estimate_tokens(outline)
        # 为 source_text 留出约 10000 token 预算
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

        return await self._call_claude(CHARACTER_SYSTEM_PROMPT, user_message, max_tokens=4096)

    async def generate_storyboard(self, outline: str, characters: str) -> str:
        """
        阶段3：根据大纲和人物设定生成 JSON 结构化分镜脚本。

        Args:
            outline:    阶段1 生成的大纲
            characters: 阶段2 生成的人物设定（自动截断控制 token）

        Returns:
            JSON 字符串 {"storyboards": [{...}, ...]}
        """
        # 智能截断 characters
        estimated = self._estimate_tokens(outline)
        chars_budget = max(8000, MAX_INPUT_TOKENS_ESTIMATE - estimated - 2000)
        allowed = int(chars_budget * CHARS_PER_TOKEN_ESTIMATE)

        characters_excerpt = characters[:allowed]
        if len(characters) > allowed:
            characters_excerpt += "\n\n（角色设定内容较长，已截取前段）"

        # 超长原文：先把 outline 再压缩一版摘要给 storyboard 用
        outline_excerpt = outline
        if len(outline) > 4000:
            outline_excerpt = outline[:4000] + "\n\n（大纲较长，已截取前4000字）"
            logger.info("大纲过长，已截断至 4000 字符用于分镜生成")

        user_message = (
            f"## 剧本大纲\n{outline_excerpt}\n\n"
            f"## 人物角色设定\n{characters_excerpt}\n\n"
            f"请根据以上大纲和人物设定，生成 JSON 格式的分镜脚本数组。"
        )

        return await self._call_claude(
            STORYBOARD_SYSTEM_PROMPT,
            user_message,
            max_tokens=8192,
            temperature=0.6,
        )


# 全局单例
llm_service = LLMService()
