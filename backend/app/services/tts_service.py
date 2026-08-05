"""
Volcengine TTS 角色配音服务
同步 REST API：文本 → MP3 音频
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.request
import urllib.error
from datetime import datetime
import httpx
from app.config import settings
from app.utils.exceptions import LLMAPIError

logger = logging.getLogger(__name__)

TTS_URL = "https://openspeech.bytedance.com/api/v1/tts"


class VolcengineTTSService:
    """火山引擎 TTS 语音合成客户端"""

    def __init__(self):
        self.access_key = settings.volc_access_key
        self.secret_key = settings.volc_secret_key
        self.default_voice = settings.volc_tts_voice_type
        self.encoding = settings.volc_tts_encoding
        self.media_dir = settings.media_dir

    async def synthesize(
        self,
        task_id: str,
        character_name: str,
        text: str,
        voice_type: str | None = None,
    ) -> str:
        """
        将文本合成为语音文件。

        Args:
            task_id:         任务 ID
            character_name:  角色名（用于文件命名）
            text:            台词文本
            voice_type:      音色 ID（默认 BV700_streaming）

        Returns:
            本地 MP3 文件路径
        """
        if not self.access_key or not self.secret_key:
            raise LLMAPIError(
                "Volcengine TTS 凭证未配置，"
                "请在 .env 中设置 VOLC_ACCESS_KEY 和 VOLC_SECRET_KEY"
            )

        voice = voice_type or self.default_voice

        logger.info(f"[{task_id}] TTS 合成: 角色={character_name}, 文本长度={len(text)}")

        # 构建请求并在线程池中执行（TTS 是同步 HTTP）
        result = await asyncio.to_thread(
            self._call_tts_api, text, voice, character_name
        )

        # 保存文件
        output_dir = os.path.join(self.media_dir, task_id, "audio")
        os.makedirs(output_dir, exist_ok=True)

        safe_name = "".join(c for c in character_name if c.isalnum() or c in "._- ")
        filename = f"{safe_name}.{self.encoding}"
        filepath = os.path.join(output_dir, filename)

        with open(filepath, "wb") as f:
            f.write(result)

        logger.info(f"[{task_id}] TTS 音频已保存: {filepath}")
        return filepath

    def _call_tts_api(self, text: str, voice_type: str, character_name: str) -> bytes:
        """同步调用火山引擎 TTS API，使用 HMAC-SHA256 签名"""
        payload = json.dumps({
            "app": {"appid": "demo_aigc", "token": "placeholder", "cluster": "volcano_tts"},
            "user": {"uid": "demo_user"},
            "audio": {
                "voice_type": voice_type,
                "encoding": self.encoding,
                "speed_ratio": 1.0,
            },
            "request": {
                "reqid": str(int(time.time() * 1000)),
                "text": text,
                "text_type": "plain",
                "operation": "query",
            },
        })

        # 火山引擎 HMAC-SHA256 签名
        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        date = timestamp[:8]
        service = "openspeech"
        region = "cn-north-1"
        host = "openspeech.bytedance.com"

        # 构建签名
        def _sign(key: bytes, msg: str) -> bytes:
            return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

        def _get_signature_key(secret_key: str, date: str, region: str, service: str) -> bytes:
            k_date = _sign(("VOLC" + secret_key).encode("utf-8"), date)
            k_region = _sign(k_date, region)
            k_service = _sign(k_region, service)
            return _sign(k_service, "request")

        content_type = "application/json"
        body_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

        canonical_uri = "/api/v1/tts"
        canonical_querystring = ""
        canonical_headers = (
            f"content-type:{content_type}\n"
            f"host:{host}\n"
            f"x-content-sha256:{body_hash}\n"
            f"x-date:{timestamp}\n"
        )
        signed_headers = "content-type;host;x-content-sha256;x-date"

        canonical_request = (
            "POST\n"
            f"{canonical_uri}\n"
            f"{canonical_querystring}\n"
            f"{canonical_headers}\n"
            f"{signed_headers}\n"
            f"{body_hash}"
        )

        algorithm = "HMAC-SHA256"
        credential_scope = f"{date}/{region}/{service}/request"
        string_to_sign = (
            f"{algorithm}\n"
            f"{timestamp}\n"
            f"{credential_scope}\n"
            f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
        )

        signing_key = _get_signature_key(self.secret_key, date, region, service)
        signature = hmac.new(
            signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        auth_header = (
            f"{algorithm} Credential={self.access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

        headers = {
            "Content-Type": content_type,
            "Host": host,
            "X-Date": timestamp,
            "X-Content-Sha256": body_hash,
            "Authorization": auth_header,
        }

        try:
            req = urllib.request.Request(
                f"https://{host}{canonical_uri}",
                data=payload.encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            raise LLMAPIError(
                f"Volcengine TTS 失败 (status={e.code}): {error_body[:200]}"
            )
        except Exception as e:
            raise LLMAPIError(f"Volcengine TTS 调用异常: {str(e)[:200]}")


# 全局单例
tts_service = VolcengineTTSService()
