"""
LLM API 调用封装
支持 Anthropic / DeepSeek 双后端，统一错误处理 + 重试 + 超时。
"""
import asyncio
import json
import logging
import re
import anthropic
from openai import AsyncOpenAI
from app.config import settings
from app.utils.exceptions import LLMAPIError, TokenLimitError, TaskTimeoutError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt 模板 —— 短剧剧本生成链路
# ---------------------------------------------------------------------------

# ── 新 Prompt：1+5 分段生成策略 ──

STORYBOARD_STRUCTURE_PROMPT = """你是一位专业AI短剧分镜师。分析以下小说，输出 JSON：

{
  "global_prefix": "日式动漫风格，{从小说提取的世界观}，{从小说提取的色调氛围}，电影感构图，精致线稿，细腻赛璐璐上色，柔和层次光影，高清晰度，人物五官人设全程统一，表情自然，肢体结构正常，手部比例准确，无水印无文字字幕，{从小说提取的材质质感}，氛围{从小说提取的核心情绪}，画面干净统一，避免脸部崩坏、畸形肢体、扭曲手部",
  "parts": [
    {"start": 1, "end": 5, "theme": "第一部分剧情主题（10字以内）"},
    {"start": 6, "end": 10, "theme": "第二部分剧情主题"},
    {"start": 11, "end": 15, "theme": "第三部分剧情主题"},
    {"start": 16, "end": 20, "theme": "第四部分剧情主题"},
    {"start": 21, "end": 25, "theme": "第五部分剧情主题"}
  ]
}

规则：
1. global_prefix 中只有"日式动漫风格"六字固定，其余所有关键词必须从小说提取
2. 5 个 part 的 theme 按小说自然叙事弧线分配：开篇→冲突→转折→决策→收尾
3. 只输出 JSON，不要任何其他文字"""

STORYBOARD_PART_PROMPT = """你是专业AI短剧分镜师。为小说段落生成恰好5个分镜。

段落：镜头{start_shot}-{end_shot}，主题：{part_theme}
风格：{global_prefix}

输出格式（把xx替换为实际内容，逐行输出恰好5行）：

镜头{start_shot}，镜头景别：xx，拍摄角度：xx，运镜方式：xx，画面主体人物：xx，场景环境：xx，情绪氛围：xx，构图：xx，画质补充：金属冷光，发丝清晰，服饰道具细节完整，抗锯齿高清渲染
镜头{start_shot_plus_1}，镜头景别：xx，拍摄角度：xx，运镜方式：xx，画面主体人物：xx，场景环境：xx，情绪氛围：xx，构图：xx，画质补充：金属冷光，发丝清晰，服饰道具细节完整，抗锯齿高清渲染
镜头{start_shot_plus_2}，镜头景别：xx，拍摄角度：xx，运镜方式：xx，画面主体人物：xx，场景环境：xx，情绪氛围：xx，构图：xx，画质补充：金属冷光，发丝清晰，服饰道具细节完整，抗锯齿高清渲染
镜头{start_shot_plus_3}，镜头景别：xx，拍摄角度：xx，运镜方式：xx，画面主体人物：xx，场景环境：xx，情绪氛围：xx，构图：xx，画质补充：金属冷光，发丝清晰，服饰道具细节完整，抗锯齿高清渲染
镜头{start_shot_plus_4}，镜头景别：xx，拍摄角度：xx，运镜方式：xx，画面主体人物：xx，场景环境：xx，情绪氛围：xx，构图：xx，画质补充：金属冷光，发丝清晰，服饰道具细节完整，抗锯齿高清渲染

规则：恰好5行，每行一个镜头，景别交替变化，情绪氛围两个字，不要输出任何其他文字"""

# ── 单次调用 Prompt（推荐，利用 Doubao 256K 输出窗口）──

STORYBOARD_SINGLE_PROMPT = """你是专业AI短剧分镜师。根据小说原文，生成恰好25个分镜提示词。

## 输出格式（先输出GLOBAL_PREFIX行，再输出25个镜头行，最后输出POST_CONSTRAINT行）

GLOBAL_PREFIX：日式动漫风格，{从小说提取的世界观}，{从小说提取的色调氛围}，电影感构图，精致线稿，细腻赛璐璐上色，柔和层次光影，高清晰度，人物五官人设全程统一，表情自然，肢体结构正常，手部比例准确，无水印无文字字幕，{从小说提取的材质质感}，氛围{从小说提取的情绪}，画面干净统一，避免脸部崩坏、畸形肢体、扭曲手部

镜头1，镜头景别：值，拍摄角度：值，运镜方式：值，画面主体人物：详细描述，场景环境：详细描述，台词对白：角色名：台词或@无，情绪氛围：两字，构图：值，画质补充：金属冷光，发丝清晰，服饰道具细节完整，抗锯齿高清渲染，转场衔接：值，镜头时长：N秒
镜头2，镜头景别：值，拍摄角度：值，运镜方式：值，画面主体人物：详细描述，场景环境：详细描述，台词对白：角色名：台词或@无，情绪氛围：两字，构图：值，画质补充：金属冷光，发丝清晰，服饰道具细节完整，抗锯齿高清渲染，转场衔接：值，镜头时长：N秒
镜头3，镜头景别：值，拍摄角度：值，运镜方式：值，画面主体人物：详细描述，场景环境：详细描述，台词对白：角色名：台词内容或@无对白，情绪氛围：两字，构图：值，画质补充：金属冷光，发丝清晰，服饰道具细节完整，抗锯齿高清渲染，转场衔接：值
...（以此类推直到镜头25）

## 强制规则
1. 恰好25个镜头，编号1-25连续，多一个少一个都算失败
2. 相邻镜头景别必须交替变化，禁止连续3个同类型
3. 情绪氛围严格两个字
4. 台词对白：有台词时写"角色名：台词内容"（如"韩萧：这里是哪里..."），无台词时写"@无"
5. 镜头时长：根据该镜头的动作量和剧情重要性给出秒数（4-8秒），动作激烈/有台词用6-8秒，静态画面/过渡用4-5秒
6. 画面主体人物和场景环境必须详细描述（每项至少20字）
7. 镜头景别可选：远景/全景/中景/中近/近景/特写/大特写，相邻镜头必须交替变化
8. 拍摄角度可选：平视/俯拍/仰拍/侧拍/低角度仰拍
9. 运镜方式可选：固定镜头/缓慢推镜/横向平移/轻微晃动/快速切镜/缓慢拉远/缓慢平移/快速推镜/轻微平移
10. 构图可选：居中构图/三分构图/框架构图/侧偏构图
11. 转场衔接可选：淡入（开场）/硬切（快节奏）/溶解（时间流逝）/推入（强调）/拉出（疏离）/闪白（回忆/冲击）/黑场过渡（章节结束）/叠化（情绪叠加）/匹配剪辑（相似构图衔接）/模糊过渡（梦境/恍惚）。根据相邻镜头的情绪变化自然选择，开篇镜头用淡入，结尾镜头用黑场过渡或拉出
12. 25个镜头行之后，最后一行输出POST_CONSTRAINT行，格式如下：
POST_CONSTRAINT：人物人设全程统一，无崩坏五官、扭曲手部、畸形肢体，无多余文字、logo、水印，{从小说提取的材质关键词}统一，光影色调连贯，符合{小说名称或世界观的描述}，适配短视频分镜，画面叙事干净，氛围感统一
13. 只输出GLOBAL_PREFIX行+25个镜头行+POST_CONSTRAINT行，不要其他任何文字"""

# ── 资产提取 Prompt ──

ASSET_EXTRACTION_PROMPT = """从分镜脚本提取角色/场景/道具，返回JSON。

{"characters":[{"name":"名","description":"外貌(50-150字)","visual_prompt":"English prompt <80 words"}],"scenes":[{"name":"名","description":"空间特征(50-150字)","visual_prompt":"English prompt <80 words"}],"props":[{"name":"名","description":"外观(30-100字)","visual_prompt":"English prompt <50 words"}]}

只输出JSON，不要其他文字。"""

# ── 旧版 Prompt（保留定义，流水线已不再使用）──────────────

OUTLINE_SYSTEM_PROMPT = """你是一位资深的短剧编剧。根据用户提供的原著小说片段，生成一份完整的短剧改编大纲。"""

CHARACTER_SYSTEM_PROMPT = """你是一位专业的影视角色设计师。根据剧本大纲和原著内容，设计剧中主要人物角色。"""

# ---------------------------------------------------------------------------
# Token 估算常量（粗略：中文约 1.5 字符/token，英文约 4 字符/token）
# ---------------------------------------------------------------------------
CHARS_PER_TOKEN_ESTIMATE = 2.0       # 保守估算
MAX_INPUT_TOKENS_ESTIMATE = 80_000   # 保守安全限制


# ── OpenAI 兼容 API 错误 → 用户友好中文提示 ──

def _map_openai_error(error_str: str) -> str | None:
    """
    将 OpenAI 兼容 API（DeepSeek / Doubao 等）的原始错误信息
    映射为用户可读的中文提示。返回 None 表示无法映射，由兜底逻辑处理。
    """
    s = error_str.lower()

    # ── 额度 / 余额不足 ──
    if any(kw in s for kw in [
        "insufficient_balance", "insufficient balance", "balance not enough",
        "quota exceeded", "quota_exceeded", "quota limit", "quota exceeded.",
        "run out of quota", "out of quota", "no quota", "quota is exhausted",
        "insufficient quota", "free quota", "trial quota", "daily quota",
        "resource exhausted", "resource_exhausted",
        "account balance", "balance is", "not enough balance",
        "额度不足", "余额不足", "免费额度已用完", "配额已用完",
        "计费", "欠费", "arrearage",
    ]):
        return (
            "模型额度已用完。请检查账号余额和配额：\n"
            "1. 登录火山引擎控制台 → 费用中心 → 查看余额和用量\n"
            "2. Coding Plan Lite 每日有免费调用次数限制，可能已到达上限\n"
            "3. 如需更多额度，请在控制台充值或升级套餐"
        )

    # ── 模型未开通 ──
    if any(kw in s for kw in [
        "modelnotopen", "model not open", "not activated",
        "has not activated the model", "please activate",
    ]):
        return (
            "模型尚未开通。请前往火山引擎 Ark 控制台开通该模型：\n"
            "https://console.volcengine.com/ark/region:ark+cn-beijing/endpoint\n"
            "→ 创建推理接入点 → 选择 doubao-seed-2-1-turbo-260628 → 确认"
        )

    # ── 模型不存在 / 接入点错误 ──
    if any(kw in s for kw in [
        "invalidendpointormodel", "model not found",
        "does not exist", "no such model",
    ]):
        return (
            "模型名称或接入点不存在。请检查配置中的模型名是否正确：\n"
            "当前模型: doubao-seed-2-1-turbo-260628\n"
            "如使用接入点 ID，格式应为 ep-xxxxxxxxxxxx"
        )

    # ── 认证失败 ──
    if any(kw in s for kw in [
        "401", "unauthorized", "authentication", "invalid api key",
        "invalid key", "api key not valid", "access denied",
        "forbidden", "403",
    ]):
        return (
            "API Key 无效或已过期。请检查：\n"
            "1. .env 中 DOUBAO_API_KEY 是否正确\n"
            "2. API Key 是否已在火山引擎控制台重新生成\n"
            "3. API Key 是否有对该模型的访问权限"
        )

    # ── 请求频率限制 ──
    if any(kw in s for kw in [
        "rate_limit", "rate limit", "too many requests", "429",
        "throttling", "request limit",
    ]):
        return "API 调用频率过高，请稍后重试（建议间隔 3-5 秒）"

    # ── 输入过长 ──
    if any(kw in s for kw in [
        "context length", "max_tokens", "token limit",
        "too long", "input length",
    ]):
        return "输入文本过长，超出模型上下文限制。请缩短小说内容或减少分片数后重试"

    # ── 服务端错误（可重试） ──
    if any(kw in s for kw in ["500", "502", "503", "504", "internal", "server"]):
        return "AI 服务暂时不可用，请稍后重试。如持续出现请联系火山引擎技术支持"

    # ── 网络错误 ──
    if any(kw in s for kw in [
        "connection", "timeout", "refused", "network",
        "dns", "resolve", "unreachable",
    ]):
        return "无法连接到 AI 服务，请检查网络连接和 Base URL 配置是否正确"

    return None  # 无法映射，由兜底逻辑处理


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
        elif self.provider == "doubao":
            self.client = AsyncOpenAI(
                api_key=settings.doubao_api_key,
                base_url=settings.doubao_base_url,
            )
            self.model = settings.doubao_model
        elif self.provider == "anthropic":
            client_kwargs = {"api_key": settings.anthropic_api_key}
            if settings.anthropic_base_url:
                client_kwargs["base_url"] = settings.anthropic_base_url
            self.client = anthropic.AsyncAnthropic(**client_kwargs)
            self.model = settings.anthropic_model
        else:
            raise ValueError(f"不支持的 LLM provider: {self.provider}，可选 anthropic | deepseek | doubao")

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
                if self.provider in ("deepseek", "doubao"):
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
                error_str_lower = error_str.lower()

                # ── 先处理 Anthropic 特定异常 ──
                if self.provider == "anthropic":
                    if isinstance(e, anthropic.BadRequestError) if hasattr(anthropic, "BadRequestError") else False:
                        if "token" in error_str_lower or "maximum context" in error_str_lower:
                            raise TokenLimitError(
                                f"输入文本过长，超出模型 Token 上限。请缩短输入后重试。"
                                f" 详情: {error_str[:200]}"
                            )
                        last_error = LLMAPIError(f"请求参数错误: {error_str[:200]}")
                        raise last_error
                    if hasattr(anthropic, "RateLimitError") and isinstance(e, anthropic.RateLimitError):
                        last_error = LLMAPIError("API 调用频率过高，请稍后重试")
                        logger.warning(f"Rate limit (attempt {attempt + 1})")
                        continue
                    if hasattr(anthropic, "APIStatusError") and isinstance(e, anthropic.APIStatusError):
                        status = e.status_code
                        if 500 <= status < 600:
                            last_error = LLMAPIError(f"AI 服务暂时不可用（{status}），正在重试...")
                            logger.warning(f"API {status} (attempt {attempt + 1})")
                            continue
                        else:
                            raise LLMAPIError(f"API 返回错误 (status={status}): {error_str[:200]}")
                    if hasattr(anthropic, "APIConnectionError") and isinstance(e, anthropic.APIConnectionError):
                        last_error = LLMAPIError("无法连接到 AI 服务，请检查网络后重试")
                        logger.warning(f"Connection error (attempt {attempt + 1})")
                        continue
                    # Anthropic 兜底
                    last_error = LLMAPIError(f"AI 服务错误: {error_str[:200]}")
                    logger.exception(f"Unexpected Anthropic error (attempt {attempt + 1})")
                    continue

                # ── OpenAI 兼容 API（DeepSeek / Doubao）统一错误映射 ──
                friendly = _map_openai_error(error_str)
                if friendly:
                    raise LLMAPIError(friendly)  # 不可重试的错误，直接抛出

                # ── 通用兜底 ──
                if any(kw in error_str_lower for kw in ["token", "maximum context", "context length"]):
                    raise TokenLimitError(
                        f"输入文本过长，超出模型 Token 上限。请缩短输入后重试。"
                    )
                if any(kw in error_str_lower for kw in ["500", "502", "503", "504", "server error", "internal"]):
                    last_error = LLMAPIError("AI 服务暂时不可用，正在重试...")
                    logger.warning(f"Server error (attempt {attempt + 1})")
                elif any(kw in error_str_lower for kw in ["connection", "timeout", "refused", "network"]):
                    last_error = LLMAPIError("无法连接到 AI 服务，请检查网络后重试")
                    logger.warning(f"Connection error (attempt {attempt + 1})")
                elif any(kw in error_str_lower for kw in ["rate_limit", "too many requests"]):
                    last_error = LLMAPIError("API 调用频率过高，请稍后重试")
                    logger.warning(f"Rate limit (attempt {attempt + 1})")
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
        """旧版方法，流水线已不再调用。保留以兼容旧接口。"""
        combined = "\n\n---\n\n".join(text_chunks)
        return await self._call_llm(OUTLINE_SYSTEM_PROMPT, f"原著文本：\n\n{combined}", max_tokens=4096)

    async def generate_characters(self, outline: str, source_text: str) -> str:
        """旧版方法，流水线已不再调用。保留以兼容旧接口。"""
        return await self._call_llm(
            CHARACTER_SYSTEM_PROMPT,
            f"## 大纲\n{outline[:2000]}\n\n## 原文\n{source_text[:2000]}",
            max_tokens=4096,
        )

    async def generate_storyboard(self, text_chunks: list[str]) -> str:
        """旧版单次调用，已废弃。保留兼容旧接口。"""
        return ""

    async def generate_storyboard_structure(self, text_chunks: list[str]) -> dict:
        """
        第一步：分析小说，提取全局风格前缀 + 5 个段落主题。

        Returns:
            {"global_prefix": str, "parts": [{"start":1,"end":5,"theme":"..."}, ...]}
        """
        combined = "\n\n---\n\n".join(text_chunks)
        estimated = self._estimate_tokens(combined)
        max_input = MAX_INPUT_TOKENS_ESTIMATE - 2000
        if estimated > max_input:
            combined = combined[:int(max_input * CHARS_PER_TOKEN_ESTIMATE)]
            logger.info(f"结构分析输入截断至 ~{max_input} tokens")

        result = await self._call_llm(
            STORYBOARD_STRUCTURE_PROMPT,
            f"请分析以下小说，提取全局风格前缀和5个段落主题：\n\n{combined}",
            max_tokens=2048,
            temperature=0.4,
        )
        # 清理 markdown code block
        text = result.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"结构分析 JSON 解析失败，使用兜底: {text[:200]}")
            return {
                "global_prefix": "日式动漫风格，电影感构图，精致线稿，细腻赛璐璐上色，柔和层次光影，高清晰度，人物五官人设全程统一，无水印无文字字幕，画面干净统一",
                "parts": [
                    {"start": 1, "end": 5, "theme": "故事开篇"},
                    {"start": 6, "end": 10, "theme": "冲突触发"},
                    {"start": 11, "end": 15, "theme": "转折揭露"},
                    {"start": 16, "end": 20, "theme": "认清局势"},
                    {"start": 21, "end": 25, "theme": "剧情收尾"},
                ],
            }

    async def generate_storyboard_part(
        self,
        text_chunks: list[str],
        start_shot: int,
        end_shot: int,
        part_theme: str,
        global_prefix: str,
    ) -> str:
        """
        第二步：为单个段落生成恰好 5 个分镜提示词。

        Returns:
            5 行模板格式的镜头文本
        """
        combined = "\n\n---\n\n".join(text_chunks)
        estimated = self._estimate_tokens(combined)
        max_input = MAX_INPUT_TOKENS_ESTIMATE - 2000
        if estimated > max_input:
            combined = combined[:int(max_input * CHARS_PER_TOKEN_ESTIMATE)]

        system_prompt = STORYBOARD_PART_PROMPT.format(
            start_shot=start_shot,
            end_shot=end_shot,
            part_theme=part_theme,
            global_prefix=global_prefix[:600],
            start_shot_plus_1=start_shot + 1,
            start_shot_plus_2=start_shot + 2,
            start_shot_plus_3=start_shot + 3,
            start_shot_plus_4=start_shot + 4,
        )

        user_message = (
            f"请为小说「{part_theme}」段落生成镜头{start_shot}-{end_shot}（恰好5镜）：\n\n{combined[:5000]}"
        )

        return await self._call_llm(
            system_prompt,
            user_message,
            max_tokens=8192,
            temperature=0.5,
        )


    async def generate_storyboard_single(self, text_chunks: list[str]) -> str:
        """
        单次调用：基于小说原文直接生成完整的 25 镜 + GLOBAL_PREFIX。
        利用 Doubao 256K 输出窗口，一次调用完成所有镜头。

        Returns:
            GLOBAL_PREFIX 行 + 25 个镜头行的完整文本
        """
        combined = "\n\n---\n\n".join(text_chunks)
        estimated = self._estimate_tokens(combined)
        max_input = MAX_INPUT_TOKENS_ESTIMATE - 4000
        if estimated > max_input:
            combined = combined[:int(max_input * CHARS_PER_TOKEN_ESTIMATE)]
            logger.info(f"分镜输入截断至 ~{max_input} tokens")

        user_message = (
            f"以下是小说原文，请生成恰好25个分镜提示词：\n\n{combined}"
        )

        return await self._call_llm(
            STORYBOARD_SINGLE_PROMPT,
            user_message,
            max_tokens=16384,
            temperature=0.5,
        )


    async def generate_asset_breakdown(self, storyboards_text: str) -> dict:
        """
        AI 资产提取：从分镜脚本中提取角色/场景/道具。

        Args:
            storyboards_text: 拼接后的分镜脚本文本

        Returns:
            {"characters": [...], "scenes": [...], "props": [...]}
        """
        user_message = f"提取角色/场景/道具：\n\n{storyboards_text[:8000]}"

        result = await self._call_llm(
            ASSET_EXTRACTION_PROMPT,
            user_message,
            max_tokens=4096,
            temperature=0.3,
        )
        text = result.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"资产提取 JSON 解析失败: {text[:200]}")
            return {"characters": [], "scenes": [], "props": []}


# 全局单例
llm_service = LLMService()
