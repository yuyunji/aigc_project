/**
 * 任务相关 API
 */
import apiClient from "./index";

/** 创建生成任务 */
export function createTask(data) {
  return apiClient.post("/api/tasks", data);
}

/** 获取任务列表 */
export function getTaskList() {
  return apiClient.get("/api/tasks");
}

/** 获取单个任务状态 */
export function getTask(taskId) {
  return apiClient.get(`/api/tasks/${taskId}`);
}

/** 获取任务统计 */
export function getTaskStats() {
  return apiClient.get("/api/tasks/stats");
}

/** 重新生成任务 */
export function regenerateTask(taskId) {
  return apiClient.post(`/api/tasks/${taskId}/regenerate`);
}

/** 获取任务大纲 */
export function getOutline(taskId) {
  return apiClient.get(`/api/results/${taskId}/outline`);
}

/** 获取任务人物角色 */
export function getCharacters(taskId) {
  return apiClient.get(`/api/results/${taskId}/characters`);
}

/** 获取任务分镜脚本 */
export function getStoryboards(taskId) {
  return apiClient.get(`/api/results/${taskId}/storyboards`);
}
