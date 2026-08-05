/**
 * 媒体资源 API
 */
import apiClient from "./index";

/** 全流程进度 */
export function getPipelineProgress(taskId) {
  return apiClient.get(`/api/media/${taskId}/pipeline`);
}

/** 分镜图片列表 */
export function getImages(taskId) {
  return apiClient.get(`/api/media/${taskId}/images`);
}

/** 视频片段列表 */
export function getVideos(taskId) {
  return apiClient.get(`/api/media/${taskId}/videos`);
}

/** 配音列表 */
export function getAudio(taskId) {
  return apiClient.get(`/api/media/${taskId}/audio`);
}

/** 合成视频 */
export function getComposite(taskId) {
  return apiClient.get(`/api/media/${taskId}/composite`);
}
