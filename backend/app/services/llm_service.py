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

STORYBOARD_STRUCTURE_PROMPT = """你是一位资深AI短剧分镜师。分析以下小说，按自然叙事弧线拆解结构，输出 JSON：

{
  "global_prefix": "日式动漫风格，{从小说提取的世界观}，{从小说提取的色调氛围}，电影感构图，精致线稿，细腻赛璐璐上色，柔和层次光影，高清晰度，人物五官人设全程统一，表情自然，肢体结构正常，手部比例准确，无水印无文字字幕，{从小说提取的材质质感}，氛围{从小说提取的核心情绪}，画面干净统一，避免脸部崩坏、畸形肢体、扭曲手部",
  "total_shots": 由你根据剧本内容判断的总镜头数,
  "parts": [
    {"start": 1, "end": N, "theme": "第一段剧情主题（10字以内）"},
    {"start": N+1, "end": M, "theme": "第二段剧情主题"},
    ...（段数由剧情自然分段决定，不固定）
  ]
}

规则：
1. global_prefix 中只有"日式动漫风格"六字固定，其余所有关键词必须从小说提取
2. total_shots 根据剧本复杂度、场景数、情节密度自行判断（参考：短篇15-30镜，中篇20-40镜，长篇30-50镜，范围仅供参考，实际由场景数×情节密度决定，以实际需要为准）
3. parts 的段数和每段的镜头数由剧情自然分段决定，不强制固定段数或每段镜数
4. 每段的 start/end 连续衔接不重叠，最后一段的 end 必须等于 total_shots
5. 情绪弧线自然递进，段间有关键剧情事件作为分界
6. 只输出 JSON，不要任何其他文字"""

STORYBOARD_PART_PROMPT = """你是资深AI短剧分镜师。为小说段落生成分镜，镜头数量由段落内容决定，确保所有镜头之间动作连贯、情绪递进、场景统一。

段落：镜头{start_shot}-{end_shot}，主题：{part_theme}
风格：{global_prefix}

输出格式（把xx替换为实际内容，逐行输出，每行一个镜头）：

镜头{start_shot}，镜头景别：xx，拍摄角度：xx，运镜方式：xx，画面主体人物：xx，场景环境：xx，情绪氛围：xx，构图：xx，画质补充：金属冷光，发丝清晰，服饰道具细节完整，抗锯齿高清渲染，转场衔接：xx，镜头时长：N秒
...（行数由段落内容决定，镜头编号从{start_shot}连续递增到{end_shot}）

规则：
1. 镜头数量由段落内容的复杂度自行判断，不求多不贪少，编号从{start_shot}到{end_shot}连续
2. 景别必须交替变化，不得有连续2个镜头景别相同
3. 本段镜头需形成完整的叙事单元：起→承→转→合→收
4. 相邻镜头之间动作必须连贯（如镜头1抬手→镜头2武器出鞘→镜头3劈下），不可中断
5. 同一段落内场景环境保持一致，如剧情需要切换场景则必须用转场衔接（溶解/推入/黑场过渡）标明
6. 第1个镜头的转场衔接标注本段与前段的衔接方式（如硬切/溶解/推入），段首镜头须与前一段的最后一个镜头保持时空逻辑
7. 情绪氛围严格两个字，镜头间情绪需在段落主题内递进
8. 转场衔接必须根据相邻镜头间情绪变化选择，可选：硬切/溶解/推入/拉出/闪白/黑场过渡/叠化/匹配剪辑/模糊过渡
9. 镜头时长：动作激烈/有台词6-8秒，静态过渡4-5秒
10. 不要输出任何其他文字"""

# ── 单次调用 Prompt（推荐，利用 Doubao 256K 输出窗口）──

STORYBOARD_SINGLE_PROMPT = """你是一位资深AI短剧分镜师，精通影视镜头语言和短视频叙事节奏。

## 你的核心任务
根据小说原文，自行判断需要多少个镜头才能完整流畅地讲好这个故事，然后逐一生成分镜提示词。

## 镜头数量判断（最重要的第一步，决定了你工作的专业水平）
你必须先回答以下问题，再确定总镜数：
1. 剧本涉及几个不同的场景/地点？
2. 有多少个关键情节转折点？
3. 有哪些必须在画面上呈现的关键动作？
4. 情绪有几个起伏波段？

判断原则：
- 每个场景切换至少需要 1 个环境交代镜头
- 每个情节转折至少需要 1 个反应/动作镜头
- 情绪发生显著变化的时刻至少需要 1 个表情/氛围镜头
- 镜头总数 = 场景数×场景内平均动作数 + 情绪转折点覆盖，没有固定公式，由你根据实际内容评估
- 短小精悍的故事不要硬凑数量，场面宏大的故事不要偷工减料

重要警告：
- 如果你不假思索地全部输出 25 镜（恰好 25），说明你没有认真读取剧本内容，这是不合格的分镜工作
- 不要被之前任何固定数字所锚定，每次分镜都从零开始分析剧本
- 最终的镜数可能是 13、22、31、39 或任何数字 —— 唯一的标准是"刚好讲完这个故事，不拖不赶"

## 输出格式
第一行必须输出镜头总数声明：
TOTAL_SHOTS：N

第二行输出 GLOBAL_PREFIX：
GLOBAL_PREFIX：日式动漫风格，{从小说提取的世界观}，{从小说提取的色调氛围}，电影感构图，精致线稿，细腻赛璐璐上色，柔和层次光影，高清晰度，人物五官人设全程统一，表情自然，肢体结构正常，手部比例准确，无水印无文字字幕，{从小说提取的材质质感}，氛围{从小说提取的情绪}，画面干净统一，避免脸部崩坏、畸形肢体、扭曲手部

然后逐一输出 N 个镜头行，每个镜头的格式如下：
镜头N，镜头景别：值，拍摄角度：值，运镜方式：值，画面主体人物：详细描述，场景环境：详细描述，台词对白：角色名：台词或@无，情绪氛围：两字，构图：值，画质补充：金属冷光，发丝清晰，服饰道具细节完整，抗锯齿高清渲染，转场衔接：值，镜头时长：M秒

示例（镜头数由你决定，这仅仅是格式示意）：
TOTAL_SHOTS：18
GLOBAL_PREFIX：日式动漫风格，末日废墟世界观，灰冷金属色调……
镜头1，镜头景别：远景，拍摄角度：俯拍……
镜头2，镜头景别：近景，拍摄角度：平视……
...（镜头编号从 1 连续递增到 TOTAL_SHOTS 你声明的数字，不可跳号）

最后一行输出：
POST_CONSTRAINT：人物人设全程统一，无崩坏五官、扭曲手部、畸形肢体，无多余文字、logo、水印，{从小说提取的材质关键词}统一，光影色调连贯，符合{小说名称或世界观的描述}，适配短视频分镜，画面叙事干净，氛围感统一

## 镜头连贯性（核心要求）
1. 相邻镜头的场景环境和画面主体人物必须保持空间一致性和时间连续性——同一场景内不可突然跳转到无关地点或人物
2. 情绪氛围需沿叙事弧线自然递进，不能出现情绪断崖或无序跳变
3. 同一场景内的连续镜头，运镜需衔接：如推镜之后接拉远需有叙事理由（强调→疏离），不可无意义跳变
4. 画面主体人物的动作和表情需有连续性：上一个镜头的动作在下一个镜头中应有延续或反应的体现
5. 景别必须交替变化，禁止连续 3 个镜头的景别相同

## 镜头参数规则
6. 情绪氛围严格两个字（如：紧张、压抑、愤怒、悲伤、释然、期待、恐惧、温馨）
7. 台词对白：有台词时写"角色名：台词内容"，无台词时写"@无"
8. 镜头时长 4-8 秒：动作激烈/有台词 6-8 秒，静态画面/过渡 4-5 秒；高潮段镜头偏短，收尾段偏长
9. 画面主体人物和场景环境必须详细描述（每项至少 20 字），包含具体的动作、表情、环境细节
10. 镜头景别：远景/全景/中景/中近/近景/特写/大特写
11. 拍摄角度：平视/俯拍/仰拍/侧拍/低角度仰拍
12. 运镜方式：固定镜头/缓慢推镜/横向平移/轻微晃动/快速切镜/缓慢拉远/缓慢平移/快速推镜/轻微平移
13. 构图：居中构图/三分构图/框架构图/侧偏构图

## 转场衔接（必须服务于叙事）
14. 转场衔接必须根据相邻镜头之间的剧情关系和情绪变化来选定，选项及适用场景：
    · 淡入 —— 全片第一个镜头，开场专用
    · 硬切 —— 同一场景内快速切换，保持紧张节奏
    · 溶解 —— 时间流逝、空间变换、回忆与现实切换
    · 推入 —— 情绪骤然升级，强调关键信息
    · 拉出 —— 情绪疏离、场景结束、人物退出
    · 闪白 —— 回忆闪回、强烈冲击、真相揭露
    · 黑场过渡 —— 章节/场景段落结束，情绪断点
    · 叠化 —— 情绪层层叠加、平行时空/梦境交错
    · 匹配剪辑 —— 上下镜头构图相似但内容/时空变化
    · 模糊过渡 —— 梦境、幻觉、昏迷、意识模糊状态
15. 转场与场景变化匹配：场景不变→硬切或匹配剪辑；场景切换→溶解或推入；情绪断点→黑场过渡；回忆/闪回→闪白或模糊过渡
16. 不同叙事段落之间的转场必须使用有叙事标识的类型（黑场过渡/溶解/推入），让观众感知到剧情阶段推进

## 最终约束
17. 只输出 TOTAL_SHOTS行 + GLOBAL_PREFIX行 + N个镜头行 + POST_CONSTRAINT行，不要任何其他文字
18. 你声明的 TOTAL_SHOTS 必须严格等于下方输出的镜头行数，不一致说明你工作不认真"""

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
                "total_shots": 20,
                "parts": [
                    {"start": 1, "end": 20, "theme": "完整叙事"},
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
        第二步：为单个段落生成分镜提示词，镜数由段落内容决定。

        Returns:
            模板格式的镜头文本（行数由段落决定）
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
        )

        user_message = (
            f"请为小说「{part_theme}」段落生成镜头{start_shot}-{end_shot}：\n\n{combined[:5000]}"
        )

        return await self._call_llm(
            system_prompt,
            user_message,
            max_tokens=8192,
            temperature=0.5,
        )


    async def generate_storyboard_single(self, text_chunks: list[str]) -> str:
        """
        单次调用：基于小说原文直接生成完整分镜 + GLOBAL_PREFIX。
        利用 Doubao 256K 输出窗口，一次调用完成所有镜头，镜数由 LLM 根据剧本判断。

        Returns:
            GLOBAL_PREFIX 行 + 所有镜头行 + POST_CONSTRAINT 行的完整文本
        """
        combined = "\n\n---\n\n".join(text_chunks)
        estimated = self._estimate_tokens(combined)
        max_input = MAX_INPUT_TOKENS_ESTIMATE - 4000
        if estimated > max_input:
            combined = combined[:int(max_input * CHARS_PER_TOKEN_ESTIMATE)]
            logger.info(f"分镜输入截断至 ~{max_input} tokens")

        user_message = (
            f"以下是小说原文，请根据剧本内容自行判断镜头数量，生成分镜提示词：\n\n{combined}"
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
