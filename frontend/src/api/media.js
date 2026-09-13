/**
 * 媒体资源 API
 */
import apiClient from "./index";

/** 全流程进度 */
export function getPipelineProgress(taskId) {
  return apiClient.get(`/api/media/${taskId}/pipeline`);
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

/** 为单个分镜生成视频 */
export function generateSceneVideo(taskId, sceneNumber, provider) {
  const params = provider ? { provider } : {};
  return apiClient.post(`/api/media/${taskId}/scene/${sceneNumber}/video`, null, { params });
}

/** 重置失败分镜 */
export function retryScene(taskId, sceneNumber) {
  return apiClient.post(`/api/media/${taskId}/scene/${sceneNumber}/retry`);
}
