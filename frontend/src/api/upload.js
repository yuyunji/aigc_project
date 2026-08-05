/**
 * 上传相关 API
 */
import apiClient from "./index";

/** 上传 txt 文件 */
export function uploadFile(file) {
  const formData = new FormData();
  formData.append("file", file);
  return apiClient.post("/api/upload", formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
}
