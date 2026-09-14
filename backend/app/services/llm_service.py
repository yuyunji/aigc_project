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
from app.services.director_storyboard_skill import DIRECTOR_STORYBOARD_SKILL
from app.utils.exceptions import LLMAPIError, TokenLimitError, TaskTimeoutError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt 模板 —— 短剧剧本生成链路
# ---------------------------------------------------------------------------

# 分镜拆解 Prompt 已抽到 app/services/director_storyboard_skill.py（导演镜头模板 skill），
# 此处只保留资产提取 Prompt。

# ── 资产提取 Prompt ──

ASSET_EXTRACTION_PROMPT = """从原著小说文本提取角色/场景/道具，返回JSON。

{"characters":[{"name":"名","description":"外貌(50-150字)","visual_prompt":"English prompt <80 words","portrait_prompt":"English front-face portrait prompt <50 words"}],"scenes":[{"name":"名","description":"空间特征(50-150字)","visual_prompt":"English prompt <80 words","spatial_layout":"空间布局(50-150字，含机位/标志物方位/光源/景别基调)"}],"props":[{"name":"名","description":"外观(30-100字)","visual_prompt":"English prompt <50 words"}]}

要求：
- 只提取有实际戏份、需要跨镜头保持一致的角色（主角与重要配角），忽略只出现一次的路人、群众；
- 场景同理：只提取反复出现的主要场景，忽略一笔带过的地点；
- 道具只提取具有辨识度、会跨镜头复现的物件；
- 每个角色的 description 必须包含明确的「服装」字段（格式如 **服装**：xxx），且服装必须写清「颜色 + 款式 + 材质 + 标志性细节」，这是全片每个镜头服装一致性的唯一真源，务必具体（如「黑色金边劲装，立领，腰束革带，袖口银纹」），不要笼统。
- 每个角色的 portrait_prompt 必须干净背景、正面半身定妆、五官/发型/服装细节明确，且服装措辞与 description 里的「服装」字段保持一致，用于后续角色定妆图「逐字锁定」外观。
- 每个场景的 spatial_layout 必须写明主结构方位、标志物相对位置、光源方向、机位景别基调，用于跨镜头场景一致性。
- 只输出JSON，不要其他文字。"""
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
            "2. DOUBAO_BASE_URL 是否与 Key 类型匹配（三者互不通用）："
            "订阅套餐 /api/plan/v3、标准按量付费 /api/v3、Coding Plan /api/coding/v3\n"
            "3. API Key 是否已在火山引擎控制台重新生成、订阅是否仍在有效期\n"
            "4. API Key 是否有对该模型的访问权限"
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

    级联链路对应方法：
    - generate_storyboard_single: 原著文本 → 导演镜头脚本（模板见 director_storyboard_skill.py）
    - generate_asset_breakdown:   分镜脚本 → 角色/场景/道具资产
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
    # 级联链路
    # ------------------------------------------------------------------

    async def generate_storyboard_single(self, text_chunks: list[str]) -> str:
        """
        单次调用：把小说原文改编成完整「导演镜头脚本」。
        利用大窗口模型一次调用完成所有镜头，镜数由 LLM 根据核心剧情线判断。
        模板与硬约束见 app/services/director_storyboard_skill.py。

        Returns:
            全局风格首行 + 逐镜块（标题时长 / 氛围段 / 分秒画面 / 对白 /
            摄影与视觉要求 / 衔接桥接标注）的完整文本
        """
        combined = "\n\n---\n\n".join(text_chunks)
        estimated = self._estimate_tokens(combined)
        max_input = MAX_INPUT_TOKENS_ESTIMATE - 4000
        if estimated > max_input:
            combined = combined[:int(max_input * CHARS_PER_TOKEN_ESTIMATE)]
            logger.info(f"分镜输入截断至 ~{max_input} tokens")

        user_message = (
            f"以下是小说的一个章节。请以漫剧导演的身份把它改编成导演镜头脚本："
            f"先提炼核心剧情线，再围绕主线决定镜头数量，"
            f"严格按照模板输出：\n\n{combined}"
        )

        return await self._call_llm(
            DIRECTOR_STORYBOARD_SKILL,
            user_message,
            # 导演镜头块（片头定调 + 逐镜分秒画面/对白/摄影要求/衔接桥接 + 导演阐述）
            # 比旧一行式模板长得多，需要更大的输出预算
            max_tokens=32768,
            temperature=0.5,
        )


    async def generate_asset_breakdown(self, source_text: str) -> dict:
        """
        AI 资产提取：从原著源文本（截取片段）中提取角色/场景/道具。

        Args:
            source_text: 原著源文本片段（由 asset_extractor.build_source_excerpt 截取）

        Returns:
            {"characters": [...], "scenes": [...], "props": [...]}

        Raises:
            LLMAPIError: 结果不是合法 JSON（宁可报错重试，也不要静默返回空资产）
        """
        user_message = f"以下是小说原文，请提取角色/场景/道具：\n\n{source_text}"

        result = await self._call_llm(
            ASSET_EXTRACTION_PROMPT,
            user_message,
            # 输入是整段原著，资产数远多于旧的分镜清单，输出预算相应放宽
            max_tokens=8192,
            temperature=0.3,
        )
        text = result.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"资产提取 JSON 解析失败: {text[:200]}")
            raise LLMAPIError("资产提取结果解析失败，请重试")

        if not isinstance(data, dict):
            logger.warning(f"资产提取结果类型异常: {type(data).__name__}")
            raise LLMAPIError("资产提取结果格式异常，请重试")
        return data


# 全局单例
llm_service = LLMService()
