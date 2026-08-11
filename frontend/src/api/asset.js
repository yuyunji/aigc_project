/**
 * 资产拆解 API
 */
import apiClient from "./index";

// AI 自动提取资产（超时 30 分钟，Coding Plan Lite 免费版响应慢）
export function extractAssets(taskId) {
  return apiClient.post(`/api/tasks/${taskId}/assets/extract`, null, {
    timeout: 1800000,
  });
}

// 获取资产列表
export function getAssets(taskId, category) {
  const params = category ? { category } : {};
  return apiClient.get(`/api/tasks/${taskId}/assets`, { params });
}

// 手动添加资产
export function createAsset(taskId, data) {
  return apiClient.post(`/api/tasks/${taskId}/assets`, data);
}

// 编辑资产
export function updateAsset(taskId, assetId, data) {
  return apiClient.put(`/api/tasks/${taskId}/assets/${assetId}`, data);
}

// 删除资产
export function deleteAsset(taskId, assetId) {
  return apiClient.delete(`/api/tasks/${taskId}/assets/${assetId}`);
}

// 生成资产参考图
export function generateAssetImage(taskId, assetId) {
  return apiClient.post(`/api/tasks/${taskId}/assets/${assetId}/generate-image`);
}

// 上传资产图片
export function uploadAssetImage(taskId, assetId, file) {
  const formData = new FormData();
  formData.append("file", file);
  return apiClient.post(`/api/tasks/${taskId}/assets/${assetId}/upload-image`, formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
}
