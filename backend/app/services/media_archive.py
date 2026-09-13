"""
媒体归档服务 —— 「重新生成」时把上一轮的视频 / 配音 / 合成成片迁入 media_archives 表

设计要点：
- 活表（media_assets）只保留最新一轮，前端主区看到的永远是当前轮次。
- 归档行的 oss_key 保持原样（OSS 对象不移动、不重传）；**新上传**才带
  `{task_id}/rounds/{N}/` 前缀，因此确定性文件名（scene_001.mp4）不会再覆盖旧对象。
- 没有 OSS 副本的本地文件移到 `media/{task_id}/rounds/{N}/` 下，由后台任务补传，
  补传成功才删除本地文件（失败则保留本地兜底）。
"""
import asyncio
import logging
import os
import shutil
from datetime import datetime

from sqlalchemy import func

from app.config import settings
from app.database import SessionLocal
from app.models.media import MediaAsset
from app.models.media_archive import MediaArchive

logger = logging.getLogger(__name__)

# 参与归档的媒体类型（图片类走原有清理逻辑）
_ARCHIVABLE_TYPES = ("video", "audio", "composite")


def round_oss_key(local_path: str, task_id: str, round_no: int) -> str:
    """
    本地路径 → 带轮次前缀的 OSS object key。

    已在 `rounds/` 下的路径保持原 key（补传时不会重复加前缀）：
      media/t1/videos/scene_001.mp4   → t1/rounds/2/videos/scene_001.mp4
      media/t1/rounds/1/videos/a.mp4  → t1/rounds/1/videos/a.mp4
    """
    rel = os.path.relpath(local_path, settings.media_dir).replace("\\", "/")
    if rel.startswith(f"{task_id}/"):
        rel = rel[len(task_id) + 1:]
    if not rel.startswith("rounds/"):
        rel = f"rounds/{round_no}/{rel}"
    return f"{task_id}/{rel}"


def current_round(task_id: str) -> int:
    """当前轮次（= 已归档轮次 + 1）；归档永远早于新一轮生成"""
    db = SessionLocal()
    try:
        latest = (
            db.query(func.max(MediaArchive.round_no))
            .filter(MediaArchive.task_id == task_id)
            .scalar()
        )
        return (latest or 0) + 1
    finally:
        db.close()


def archive_task_media(task_id: str) -> dict | None:
    """
    把任务的视频 / 配音 / 合成成片迁入归档表。

    返回 {"round_no", "archived_count", "rescue": [(archive_id, local_path), ...]}；
    没有可归档行时返回 None（不产生空轮次）。

    本地文件处理：OSS 已有副本 → 删除；无副本 → 移到 `rounds/{N}/` 待后台补传。
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(MediaAsset)
            .filter(
                MediaAsset.task_id == task_id,
                MediaAsset.asset_type.in_(_ARCHIVABLE_TYPES),
            )
            .all()
        )
        if not rows:
            return None

        round_no = current_round(task_id)
        archived_at = datetime.utcnow()

        # ── 1. 处理本地文件，记下每行归档后的路径与补传状态 ──
        decisions: list[tuple[MediaAsset, str | None, str]] = []
        for row in rows:
            src = row.file_path
            has_local = bool(src) and os.path.isfile(src)
            if row.oss_key:
                # OSS 已有副本，本地文件不再需要
                if has_local:
                    _safe_remove(src)
                decisions.append((row, None, "none"))
            elif has_local:
                moved = _move_into_round(src, task_id, round_no)
                if moved:
                    decisions.append((row, moved, "pending"))
                elif moved is None and os.path.isfile(src):
                    # 移动/复制都失败：保留原路径（可能被后续清理删掉），标记失败
                    decisions.append((row, src, "failed"))
                else:
                    decisions.append((row, None, "failed"))
            else:
                decisions.append((row, None, "none"))

        # ── 2. 合成成片的 output/ 残留（final.mp4、subtitles.srt 等）一并挪进本轮 ──
        if any(r.asset_type == "composite" for r in rows):
            _move_dir_into_round(task_id, round_no, "output")

        # ── 3. 写入归档表并删除活表行（同一事务）──
        archives = []
        for row, file_path, rescue_status in decisions:
            archive = MediaArchive(
                task_id=row.task_id,
                asset_type=row.asset_type,
                round_no=round_no,
                storyboard_id=row.storyboard_id,
                scene_number=row.scene_number,
                character_name=row.character_name,
                prompt=row.prompt,
                file_path=file_path,
                file_url=row.file_url,
                oss_key=row.oss_key,
                status=row.status,
                error_message=row.error_message,
                duration=row.duration,
                source_ids=row.source_ids,
                subtitle_srt=row.subtitle_srt,
                rescue_status=rescue_status,
                created_at=row.created_at,
                archived_at=archived_at,
            )
            archives.append(archive)
            db.delete(row)

        db.add_all(archives)
        db.flush()  # 取归档行 id

        rescue = [
            (a.id, a.file_path)
            for a in archives
            if a.rescue_status == "pending" and a.file_path
        ]
        db.commit()

        logger.info(
            f"[{task_id}] 归档第 {round_no} 轮: {len(archives)} 个媒体"
            f"（待补传 {len(rescue)} 个）"
        )
        return {
            "round_no": round_no,
            "archived_count": len(archives),
            "rescue": rescue,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def rescue_archive_uploads(
    task_id: str, round_no: int, rescue_items: list[tuple[str, str]]
) -> None:
    """
    后台补传：把归档行中尚无 OSS 副本的本地文件传到 OSS，成功后删除本地文件。
    失败保持 `failed` 状态并保留本地文件，页面回退到 /media 本地路径播放。
    """
    from app.services.storage import storage

    for archive_id, local_path in rescue_items:
        key = round_oss_key(local_path, task_id, round_no)
        uploaded = None
        try:
            uploaded = await asyncio.to_thread(storage.upload, local_path, key)
        except Exception as e:
            logger.warning(f"[{task_id}] 归档补传失败 {local_path}: {e}")

        db = SessionLocal()
        try:
            archive = (
                db.query(MediaArchive).filter(MediaArchive.id == archive_id).first()
            )
            if not archive:
                continue
            if uploaded:
                archive.oss_key = uploaded
                archive.rescue_status = "done"
                archive.file_path = None
                db.commit()
                _safe_remove(local_path)
            else:
                archive.rescue_status = "failed"
                db.commit()
        finally:
            db.close()

    logger.info(f"[{task_id}] 归档补传结束（第 {round_no} 轮，共 {len(rescue_items)} 个）")


# ----------------------------------------------------------------------
# 文件操作辅助
# ----------------------------------------------------------------------

def _safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError as e:
        logger.warning(f"删除本地文件失败（忽略）: {path} -> {e}")


def _move_into_round(src: str, task_id: str, round_no: int) -> str | None:
    """把文件移到 media/{task_id}/rounds/{N}/ 下的同相对路径，返回新路径；失败返回 None"""
    rel = os.path.relpath(src, settings.media_dir).replace("\\", "/")
    if rel.startswith(f"{task_id}/"):
        rel = rel[len(task_id) + 1:]
    if rel.startswith("rounds/"):
        return src  # 已在归档目录下
    dst = os.path.join(
        settings.media_dir, task_id, "rounds", str(round_no), *rel.split("/")
    )
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        shutil.move(src, dst)  # 同盘 rename，原子
        return dst
    except OSError as e:
        logger.warning(f"移动文件到归档目录失败，改用复制: {src} -> {dst} ({e})")
    try:
        shutil.copy2(src, dst)
        return dst
    except OSError as e:
        logger.error(f"归档文件复制失败，该文件可能丢失: {src} -> {e}")
        return None


def _move_dir_into_round(task_id: str, round_no: int, subdir: str) -> None:
    """把 media/{task_id}/{subdir}/ 下尚未被移走的文件挪进 rounds/{N}/{subdir}/"""
    src_dir = os.path.join(settings.media_dir, task_id, subdir)
    if not os.path.isdir(src_dir):
        return
    for name in os.listdir(src_dir):
        src = os.path.join(src_dir, name)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(
            settings.media_dir, task_id, "rounds", str(round_no), subdir, name
        )
        if os.path.exists(dst):
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            shutil.move(src, dst)
        except OSError as e:
            logger.warning(f"移动归档文件失败（忽略）: {src} -> {e}")
