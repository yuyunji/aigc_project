"""
ComfyUI 本地 MiniMax H3 视频生成服务（图生视频 ref2v）
通过 ComfyUI HTTP API 提交工作流、轮询、下载视频。
对应工作流：MiniMax H3 多参生视频（MiniMaxH3ReferenceToVideo）
支持多张参考图：分镜图 + 角色资产图 + 场景资产图（ref_images.ref_image_0/1/2...）
"""
import asyncio
import json
import logging
import os
import random
import uuid

import httpx

from app.config import settings
from app.utils.exceptions import LLMAPIError

logger = logging.getLogger(__name__)

# 工作流节点 ID（对应 MiniMax H3 多参生视频 UI 工作流）
NODE_LOAD_IMAGE = "137"   # LoadImage：参考图文件名
NODE_PROMPT = "141"       # PrimitiveStringMultiline：提示词
NODE_DURATION = "132"     # PrimitiveFloat：时长（秒）
NODE_NOISE = "129"        # RandomNoise：随机种子
NODE_SAVE_VIDEO = "92"    # SaveVideo：输出视频
MAX_REF_IMAGES = 10       # ref_images 上限（模板 prefix min=0, max=9）

_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


class ComfyUIService:
    """ComfyUI MiniMax H3 ref2v client"""

    def __init__(self):
        self.base_url = settings.comfyui_url.rstrip("/")
        self.poll_interval = settings.comfyui_poll_interval
        self.poll_max = settings.comfyui_poll_max_retries
        self.media_dir = settings.media_dir

    async def generate_video(
        self,
        task_id: str,
        scene_number: int,
        prompt: str,
        image_paths: list[str] | None = None,
        duration: int | None = None,
    ) -> str:
        """分镜图→视频：上传多张参考图→提交→轮询→下载，返回本地 .mp4 路径"""
        image_paths = [p for p in (image_paths or []) if p and os.path.isfile(p)][:MAX_REF_IMAGES]
        if not image_paths:
            raise LLMAPIError(
                f"ComfyUI 图生视频需要参考图，请先生成分镜 {scene_number} 的图片/资产图"
            )

        # 1. 上传所有参考图到 ComfyUI input 目录
        ref_names = []
        for i, p in enumerate(image_paths):
            name = await self._upload_image(task_id, scene_number, p, i)
            ref_names.append(name)
            logger.info(f"[{task_id}] ComfyUI 参考图{i}已上传: {name}")

        # 2. 构建并提交工作流
        workflow = self._build_workflow(
            prompt, ref_names, duration or settings.minimax_video_duration
        )
        prompt_id = await self._submit(workflow, task_id, scene_number)

        # 3. 轮询
        video_file = await self._poll(prompt_id, task_id, scene_number)

        # 4. 下载
        local_path = await self._download(task_id, scene_number, video_file)
        logger.info(f"[{task_id}] ComfyUI 视频已下载: {local_path}")
        return local_path

    # ------------------------------------------------------------------
    # 工作流构建
    # ------------------------------------------------------------------

    def _build_workflow(self, prompt: str, ref_names: list[str], duration: int) -> dict:
        """构建 API 格式工作流，动态生成多张参考图的 LoadImage 节点"""
        workflow = {
            "119": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"}},
            "120": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"}},
            "128": {"class_type": "CLIPLoader", "inputs": {
                "clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
                "type": "minimax", "device": "default",
            }},
            "153": {"class_type": "DiffusionModelLoaderKJ", "inputs": {
                "model_name": "minimaxh3\\minimax_h3_ref2va_pruned_int8_convrot.safetensors",
                "weight_dtype": "default", "compute_dtype": "default",
                "patch_cublaslinear": False, "sage_attention": "auto",
                "enable_fp16_accumulation": True,
            }},
            NODE_PROMPT: {"class_type": "PrimitiveStringMultiline", "inputs": {"value": prompt[:4000]}},
            NODE_DURATION: {"class_type": "PrimitiveFloat", "inputs": {"value": float(duration)}},
            "131": {"class_type": "ComfyMathExpression", "inputs": {
                "values.a": [NODE_DURATION, 0],
                "expression": "max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17",
            }},
            "115": {"class_type": "ResolutionSelector", "inputs": {
                "aspect_ratio": "16:9 (Widescreen)", "megapixels": 0.4, "multiple": 32,
            }},
            "126": {"class_type": "BasicGuider", "inputs": {"model": ["153", 0], "conditioning": ["152", 0]}},
            "123": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
            "124": {"class_type": "BasicScheduler", "inputs": {
                "model": ["153", 0], "scheduler": "simple", "steps": 20, "denoise": 1,
            }},
            NODE_NOISE: {"class_type": "RandomNoise", "inputs": {
                "noise_seed": random.randint(0, 2 ** 63 - 1),
            }},
            "125": {"class_type": "SamplerCustomAdvanced", "inputs": {
                "noise": [NODE_NOISE, 0], "guider": ["126", 0], "sampler": ["123", 0],
                "sigmas": ["124", 0], "latent_image": ["152", 1],
            }},
            "122": {"class_type": "VAEDecode", "inputs": {"samples": ["125", 0], "vae": ["119", 0]}},
            "121": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["125", 0], "vae": ["120", 0]}},
            "130": {"class_type": "CreateVideo", "inputs": {
                "images": ["122", 0], "audio": ["121", 0], "fps": 24, "bit_depth": 8,
            }},
            NODE_SAVE_VIDEO: {"class_type": "SaveVideo", "inputs": {
                "video": ["130", 0], "filename_prefix": "video/MiniMax_H3",
                "format": "auto", "codec": "auto",
            }},
        }

        # 动态生成 LoadImage 节点 + ref_images 连接（ref_image_0/1/2...）
        ref_image_inputs = {}
        for i, name in enumerate(ref_names):
            node_id = NODE_LOAD_IMAGE if i == 0 else f"{NODE_LOAD_IMAGE}_{i}"
            workflow[node_id] = {"class_type": "LoadImage", "inputs": {"image": name}}
            ref_image_inputs[f"ref_images.ref_image_{i}"] = [node_id, 0]

        workflow["152"] = {
            "class_type": "MiniMaxH3ReferenceToVideo",
            "inputs": {
                "clip": ["128", 0], "vae": ["119", 0], "audio_vae": ["120", 0],
                **ref_image_inputs,
                "prompt": [NODE_PROMPT, 0],
                "width": ["115", 0], "height": ["115", 1], "length": ["131", 1],
                "ref_image_size": "match",
            },
        }
        return workflow

    # ------------------------------------------------------------------
    # API 调用
    # ------------------------------------------------------------------

    async def _upload_image(self, task_id: str, scene_number: int, image_path: str, index: int = 0) -> str:
        """上传参考图到 ComfyUI input 目录，返回文件名（唯一名避免跨任务覆盖）"""
        ext = os.path.splitext(image_path)[1].lower() or ".png"
        # 唯一文件名：ref_<task短id>_<scene>_<index>.png
        unique_name = f"ref_{task_id[:8]}_{scene_number:03d}_{index}{ext}"
        content_type = _CONTENT_TYPES.get(ext, "image/png")
        async with httpx.AsyncClient(timeout=120) as client:
            with open(image_path, "rb") as f:
                files = {"image": (unique_name, f, content_type)}
                resp = await client.post(f"{self.base_url}/upload/image", files=files)
            if resp.status_code not in (200, 201):
                raise LLMAPIError(
                    f"ComfyUI 上传参考图失败: {resp.status_code} {resp.text[:300]}"
                )
            data = resp.json()
        name = data.get("name", "")
        if not name:
            raise LLMAPIError("ComfyUI 上传参考图未返回文件名")
        return name

    async def _submit(self, workflow: dict, task_id: str, scene_number: int) -> str:
        """提交工作流，返回 prompt_id"""
        payload = {"prompt": workflow, "client_id": str(uuid.uuid4())}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{self.base_url}/prompt", json=payload)
        if resp.status_code != 200:
            raise LLMAPIError(
                f"ComfyUI 提交失败: {resp.status_code} {resp.text[:500]}"
            )
        data = resp.json()
        if data.get("node_errors"):
            raise LLMAPIError(
                "ComfyUI 工作流节点错误: "
                + json.dumps(data["node_errors"], ensure_ascii=False)[:500]
            )
        prompt_id = data.get("prompt_id", "")
        if not prompt_id:
            raise LLMAPIError("ComfyUI 未返回 prompt_id")
        logger.info(f"[{task_id}] ComfyUI 已提交: {prompt_id} (分镜 {scene_number})")
        return prompt_id

    async def _poll(self, prompt_id: str, task_id: str, scene_number: int) -> dict:
        """轮询直到完成，返回输出视频文件信息"""
        for attempt in range(self.poll_max):
            await asyncio.sleep(self.poll_interval)
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(f"{self.base_url}/history/{prompt_id}")
            if resp.status_code != 200:
                continue
            history = resp.json().get(prompt_id)
            if not history:
                continue

            status = history.get("status", {})
            if status.get("status_str") == "error":
                raise LLMAPIError(
                    f"ComfyUI 生成失败: {json.dumps(status, ensure_ascii=False)[:300]}"
                )

            outputs = history.get("outputs", {})
            if outputs:
                video_file = self._extract_video(outputs)
                if video_file:
                    return video_file
                # 有输出但没视频 → 直接报错，避免轮询到超时
                raise LLMAPIError(
                    f"ComfyUI 输出中未找到视频，节点输出: {list(outputs.keys())}"
                )

            logger.debug(
                f"[{task_id}] ComfyUI 轮询 (分镜{scene_number}, "
                f"{attempt + 1}/{self.poll_max})"
            )

        raise LLMAPIError(
            f"ComfyUI 生成超时（分镜{scene_number}，"
            f"已等待 {self.poll_max * self.poll_interval}s）"
        )

    _VIDEO_EXTS = (".mp4", ".webm", ".mov", ".mkv", ".avi")

    @staticmethod
    def _extract_video(outputs: dict) -> dict | None:
        """从 history outputs 提取视频文件信息（兼容多种结构）"""
        save = outputs.get(NODE_SAVE_VIDEO, {})
        # SaveVideo (VHS) 把视频存在 "images" 字段（.mp4），也可能在 video/videos/gifs
        for key in ("images", "video", "videos", "gifs"):
            items = save.get(key)
            if isinstance(items, list) and items:
                item = items[0]
                if item.get("filename", "").lower().endswith(ComfyUIService._VIDEO_EXTS):
                    return ComfyUIService._video_item(item)
        # 兜底：遍历所有节点输出
        for node_outputs in outputs.values():
            for key in ("images", "video", "videos", "gifs"):
                items = node_outputs.get(key)
                if isinstance(items, list) and items:
                    item = items[0]
                    if item.get("filename", "").lower().endswith(ComfyUIService._VIDEO_EXTS):
                        return ComfyUIService._video_item(item)
        return None

    @staticmethod
    def _video_item(item: dict) -> dict:
        return {
            "filename": item.get("filename", ""),
            "subfolder": item.get("subfolder", ""),
            "type": item.get("type", "output"),
        }

    async def _download(self, task_id: str, scene_number: int, video_file: dict) -> str:
        """下载视频到 media/{task_id}/videos/"""
        output_dir = os.path.join(self.media_dir, task_id, "videos")
        os.makedirs(output_dir, exist_ok=True)
        filename = f"scene_{scene_number:03d}.mp4"
        filepath = os.path.join(output_dir, filename)

        params = {
            "filename": video_file["filename"],
            "subfolder": video_file.get("subfolder", ""),
            "type": video_file.get("type", "output"),
        }
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.get(f"{self.base_url}/view", params=params)
            resp.raise_for_status()
        with open(filepath, "wb") as f:
            f.write(resp.content)
        return filepath


# 全局单例
comfyui_service = ComfyUIService()
