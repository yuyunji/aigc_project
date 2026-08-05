"""
文件上传接口
POST /api/upload — 上传 txt 文件，返回文件内容供前端回填文本框
"""
import os
from fastapi import APIRouter, UploadFile, File, HTTPException

from app.config import settings

router = APIRouter(tags=["上传"])

# 允许的文件扩展名
ALLOWED_EXTENSIONS = {".txt"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """
    上传 txt 文本文件，返回文件内容。

    校验：
    - 仅允许 .txt 扩展名
    - 最大 10MB
    - 编码尝试 utf-8 → gbk 兜底

    返回：
    {
        "filename": "xxx.txt",
        "content": "文件文本内容...",
        "size": 12345,
        "encoding": "utf-8"
    }
    """
    # ── 扩展名校验 ──
    _, ext = os.path.splitext(file.filename or "")
    if ext.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型 '{ext}'，仅允许 {', '.join(ALLOWED_EXTENSIONS)}"
        )

    # ── 读取文件内容 ──
    raw_bytes = await file.read()

    # 大小校验
    if len(raw_bytes) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"文件过大 ({len(raw_bytes)} bytes)，最大允许 {MAX_FILE_SIZE} bytes"
        )

    # ── 编码检测与解码 ──
    content, encoding = _decode_text(raw_bytes)

    if content is None:
        raise HTTPException(
            status_code=400,
            detail="无法识别文件编码，请使用 UTF-8 或 GBK 编码的 txt 文件"
        )

    return {
        "filename": file.filename,
        "content": content,
        "size": len(raw_bytes),
        "encoding": encoding,
    }


def _decode_text(raw: bytes) -> tuple[str | None, str | None]:
    """尝试多种编码解码文本内容"""
    for encoding in ["utf-8", "utf-8-sig", "gbk", "gb2312", "latin-1"]:
        try:
            return raw.decode(encoding), encoding
        except (UnicodeDecodeError, UnicodeError):
            continue
    return None, None
