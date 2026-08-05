"""
文本预处理服务 —— 分片 & 降维流程模拟
【Demo说明】此处仅做简单文本分片，
商业项目实现七层提取与节点重构算法，本Demo仅做流程模拟标记
"""
import re
import logging
from app.config import settings
from app.utils.exceptions import InputTooLargeError, EmptyChunksError

logger = logging.getLogger(__name__)


class TextProcessor:
    """
    文本分片处理器
    Demo 版本做简单的段落感知分片，商业版此处为七层提取引擎
    """

    @staticmethod
    def validate_input(text: str) -> None:
        """
        校验输入文本合法性，不合法时抛出异常。

        Raises:
            InputTooLargeError: 文本超过最大字符限制
            ValueError: 文本为空或过短
        """
        if not text or not text.strip():
            raise ValueError("输入文本不能为空")

        text_len = len(text)
        if text_len > settings.max_input_chars:
            raise InputTooLargeError(
                actual=text_len,
                limit=settings.max_input_chars
            )
        if text_len < 10:
            raise ValueError("输入文本过短，至少需要 10 个字符")

        logger.info(f"输入校验通过: {text_len} 字符")

    @staticmethod
    def chunk_text(text: str) -> list[str]:
        """
        将长文本按段落边界分片，每片不超过 MAX_CHUNK_SIZE 字符。

        策略：
        1. 先按自然段落（双换行）切分
        2. 短段落合并，直到接近 chunk 上限
        3. 超长单段落按句子切分兜底
        4. 空文本返回空列表（由调用方处理）

        【此处商业项目实现七层提取与节点重构算法，Demo仅做流程模拟】
        """
        max_size = settings.max_chunk_size
        overlap = settings.chunk_overlap

        text = text.strip()
        if not text:
            return []

        # Step 1: 按自然段落切分
        paragraphs = re.split(r"\n\s*\n", text)
        paragraphs = [p.strip() for p in paragraphs if p.strip()]

        if not paragraphs:
            return [text]

        # Step 2: 合并短段落为 chunk
        chunks = []
        current = ""

        for para in paragraphs:
            # 如果单个段落超过上限，按句子切分兜底
            if len(para) > max_size:
                # 先保存当前积累的 chunk
                if current:
                    chunks.append(current.strip())
                    current = ""

                # 按句号/问号/感叹号/换行拆分超长段落
                sentences = re.split(r"(?<=[。！？\n])", para)
                for sent in sentences:
                    if not sent.strip():
                        continue
                    if len(current) + len(sent) <= max_size:
                        current += sent
                    else:
                        if current:
                            chunks.append(current.strip())
                        # 取 overlap 长度的上文作为新 chunk 开头
                        overlap_text = (
                            current[-overlap:] if len(current) > overlap else current
                        )
                        current = overlap_text + sent
                continue

            # 正常段落：尝试合并
            if len(current) + len(para) + 2 <= max_size:
                current = current + "\n\n" + para if current else para
            else:
                chunks.append(current.strip())
                overlap_text = (
                    current[-overlap:] if len(current) > overlap else current
                )
                current = overlap_text + "\n\n" + para

        if current.strip():
            chunks.append(current.strip())

        # 兜底：确保至少返回一个 chunk
        if not chunks and text:
            chunks = [text[:max_size]]

        return chunks

    @staticmethod
    def preprocess(text: str) -> dict:
        """
        文本预处理入口：校验 → 分片 → 元信息提取

        返回结构：
        {
            "chunks": ["chunk1...", "chunk2..."],
            "metadata": {
                "original_length": 12345,
                "chunk_count": 3,
                "chunk_sizes": [8000, 8000, 2345],
                "truncated_for_llm": false,
                "algorithm_note": "..."
            }
        }

        Raises:
            InputTooLargeError: 输入超过限制
            EmptyChunksError: 分片后无有效内容
            ValueError: 输入为空或过短
        """
        # 1. 校验
        TextProcessor.validate_input(text)

        # 2. 分片
        chunks = TextProcessor.chunk_text(text)

        if not chunks:
            raise EmptyChunksError("文本分片后无有效内容，请检查输入格式")

        # 3. 决定送入 LLM 的分片数
        llm_chunk_limit = settings.max_chunks_for_llm
        truncated = len(chunks) > llm_chunk_limit
        effective_chunks = chunks[:llm_chunk_limit]

        logger.info(
            f"文本预处理完成: 原文 {len(text)} 字 → {len(chunks)} 片"
            + (f"（LLM 使用前 {llm_chunk_limit} 片）" if truncated else "")
        )

        return {
            "chunks": effective_chunks,
            "metadata": {
                "original_length": len(text),
                "total_chunks": len(chunks),
                "effective_chunks": len(effective_chunks),
                "chunk_sizes": [len(c) for c in effective_chunks],
                "truncated_for_llm": truncated,
                "algorithm_note": (
                    "【此处商业项目实现七层提取与节点重构算法，Demo仅做流程模拟】"
                    "七层提取包含：语义层、情节层、人物层、情感层、节奏层、"
                    "冲突层、世界观层逐级降维，节点重构将提取特征重组为结构化剧本中间表示。"
                ),
            },
        }
