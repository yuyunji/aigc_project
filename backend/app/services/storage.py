"""
对象存储服务 —— 阿里云 OSS（私有 Bucket + 签名 URL）

统一封装上传 / 签名 / 删除。未配置 OSS 时优雅降级：所有方法返回 None，
前端回退到本地 /media 静态路径。
"""
import os

from app.config import settings

_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
}


class StorageService:
    def __init__(self):
        self._bucket = None

    def _ensure_bucket(self):
        """惰性初始化 OSS Bucket；未配置时返回 None"""
        if self._bucket is None:
            if not all([
                settings.oss_access_key_id,
                settings.oss_access_key_secret,
                settings.oss_bucket,
                settings.oss_endpoint,
            ]):
                self._bucket = False
            else:
                import oss2
                auth = oss2.Auth(
                    settings.oss_access_key_id, settings.oss_access_key_secret
                )
                self._bucket = oss2.Bucket(
                    auth, settings.oss_endpoint, settings.oss_bucket
                )
        return self._bucket if self._bucket is not False else None

    def upload(self, local_path: str) -> str | None:
        """上传本地文件，返回 object key；未配置/文件不存在返回 None"""
        bucket = self._ensure_bucket()
        if not bucket or not local_path or not os.path.isfile(local_path):
            return None
        key = os.path.relpath(local_path, settings.media_dir).replace("\\", "/")
        ext = os.path.splitext(local_path)[1].lower()
        headers = {
            "Content-Type": _CONTENT_TYPES.get(ext, "application/octet-stream")
        }
        bucket.put_object_from_file(key, local_path, headers=headers)
        return key

    def get_signed_url(self, key: str | None, expire: int | None = None) -> str | None:
        """生成 GET 签名 URL；未配置/无 key 返回 None"""
        bucket = self._ensure_bucket()
        if not bucket or not key:
            return None
        return bucket.sign_url("GET", key, expire or settings.oss_url_expire)

    def delete(self, key: str) -> None:
        """删除对象"""
        bucket = self._ensure_bucket()
        if bucket and key:
            bucket.delete_object(key)


storage = StorageService()
