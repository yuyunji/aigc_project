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

/** 历史轮次归档（重新生成保留的上一轮视频 / 配音 / 成片） */
export function getMediaArchive(taskId) {
  return apiClient.get(`/api/media/${taskId}/archive`);
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

/** 为单个分镜生成 6 宫格分镜导演图（style: guoman3d / riman2d / zhenren） */
export function generateDirectorImage(taskId, sceneNumber, style) {
  return apiClient.post(`/api/media/${taskId}/scene/${sceneNumber}/director-image`, { style });
}

/** 分镜导演图列表 */
export function getDirectorImages(taskId) {
  return apiClient.get(`/api/media/${taskId}/director-images`);
}
