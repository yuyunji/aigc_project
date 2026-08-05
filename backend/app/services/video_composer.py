"""
FFmpeg 视频合成服务
拼接视频片段 + 配音音频 + 字幕 → 最终 MP4
"""
import asyncio
import logging
import os
import json
from app.config import settings
from app.utils.exceptions import LLMAPIError

logger = logging.getLogger(__name__)


class VideoComposer:
    """FFmpeg 视频合成器（本地 subprocess）"""

    def __init__(self):
        self.ffmpeg = settings.ffmpeg_path
        self.media_dir = settings.media_dir

    async def composite(
        self,
        task_id: str,
        video_paths: list[str],
        audio_paths: list[str],
        subtitle_texts: list[dict],
    ) -> str:
        """
        合成最终视频。

        Args:
            task_id:         任务 ID
            video_paths:     视频片段路径列表（按分镜顺序）
            audio_paths:     配音音频路径列表
            subtitle_texts:  字幕列表 [{"scene": 1, "text": "...", "start": 0, "end": 5}, ...]

        Returns:
            最终 MP4 文件路径
        """
        output_dir = os.path.join(self.media_dir, task_id, "output")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "final.mp4")

        logger.info(
            f"[{task_id}] 开始视频合成: "
            f"{len(video_paths)} 个视频, {len(audio_paths)} 个音频"
        )

        # ── Step 1: 生成文件列表（用于 concat） ──
        concat_list_path = os.path.join(output_dir, "concat_list.txt")
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for vp in video_paths:
                # FFmpeg concat 需要相对或转义路径
                abs_path = os.path.abspath(vp).replace("\\", "/")
                f.write(f"file '{abs_path}'\n")

        # ── Step 2: 生成 SRT 字幕文件 ──
        srt_path = os.path.join(output_dir, "subtitles.srt")
        self._write_srt(subtitle_texts, srt_path)

        # ── Step 3: 拼接视频片段 ──
        concat_output = os.path.join(output_dir, "concat_video.mp4")
        concat_cmd = [
            self.ffmpeg, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_list_path,
            "-c", "copy",
            concat_output,
        ]
        await self._run_ffmpeg(concat_cmd, task_id, "视频拼接")

        # ── Step 4: 合并音频（如有多个） ──
        audio_input = None
        if audio_paths:
            if len(audio_paths) == 1:
                audio_input = audio_paths[0]
            else:
                # 多个音频用 concat filter 合并
                audio_concat_path = os.path.join(output_dir, "concat_audio.mp3")
                audio_list_path = os.path.join(output_dir, "audio_list.txt")
                with open(audio_list_path, "w", encoding="utf-8") as f:
                    for ap in audio_paths:
                        abs_path = os.path.abspath(ap).replace("\\", "/")
                        f.write(f"file '{abs_path}'\n")

                audio_concat_cmd = [
                    self.ffmpeg, "-y",
                    "-f", "concat",
                    "-safe", "0",
                    "-i", audio_list_path,
                    "-c", "copy",
                    audio_concat_path,
                ]
                await self._run_ffmpeg(audio_concat_cmd, task_id, "音频合并")
                audio_input = audio_concat_path

        # ── Step 5: 合成最终视频（视频 + 音频 + 字幕） ──
        final_cmd = [self.ffmpeg, "-y", "-i", concat_output]

        if audio_input:
            final_cmd += ["-i", audio_input]

        # 字幕滤镜（需要转义路径中的冒号和反斜杠）
        srt_abs = os.path.abspath(srt_path).replace("\\", "/").replace(":", "\\:")
        subtitle_filter = f"subtitles='{srt_abs}':force_style='FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2'"

        final_cmd += [
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-vf", subtitle_filter,
        ]

        if audio_input:
            final_cmd += ["-c:a", "aac", "-b:a", "128k", "-shortest"]

        final_cmd.append(output_path)
        await self._run_ffmpeg(final_cmd, task_id, "最终合成")

        logger.info(f"[{task_id}] 视频合成完成: {output_path}")
        return output_path

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _write_srt(subtitle_texts: list[dict], srt_path: str) -> None:
        """生成 SRT 字幕文件"""
        with open(srt_path, "w", encoding="utf-8") as f:
            for i, sub in enumerate(subtitle_texts, 1):
                start = VideoComposer._format_srt_time(sub.get("start", 0))
                end = VideoComposer._format_srt_time(sub.get("end", 0))
                text = sub.get("text", "")
                f.write(f"{i}\n{start} --> {end}\n{text}\n\n")

    @staticmethod
    def _format_srt_time(seconds: float) -> str:
        """秒数 → SRT 时间格式 HH:MM:SS,mmm"""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds - int(seconds)) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    async def _run_ffmpeg(self, cmd: list[str], task_id: str, stage: str) -> None:
        """执行 FFmpeg 命令"""
        logger.debug(f"[{task_id}] FFmpeg {stage}: {' '.join(cmd[:6])}...")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace")[-500:]
            raise LLMAPIError(f"FFmpeg {stage} 失败 (code={proc.returncode}): {error_msg}")

        logger.info(f"[{task_id}] FFmpeg {stage} 完成")


# 全局单例
video_composer = VideoComposer()
