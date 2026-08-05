/**
 * Axios 实例 & 请求/响应拦截器
 */
import axios from "axios";
import { ElMessage } from "element-plus";

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || "/api",
  timeout: 60000,
  headers: { "Content-Type": "application/json" },
});

// 响应拦截器 —— 统一错误处理
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const detail = error.response?.data?.detail || error.message || "请求失败";
    ElMessage.error(detail);
    return Promise.reject(error);
  }
);

export default apiClient;
