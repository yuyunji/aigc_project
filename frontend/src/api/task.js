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

/** 删除任务 */
export function deleteTask(taskId) {
  return apiClient.delete(`/api/tasks/${taskId}`);
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

/** 保存某镜编辑后的导演脚本原文（服务端按同一套解析逻辑重算派生字段） */
export function updateStoryboard(taskId, sceneNumber, rawScript) {
  return apiClient.put(`/api/results/${taskId}/storyboards/${sceneNumber}`, {
    raw_script: rawScript,
  });
}

/** 保存编辑后的全局风格前缀（片头风格首行，注入所有 prompt） */
export function updateGlobalPrefix(taskId, globalPrefix) {
  return apiClient.put(`/api/results/${taskId}/global-prefix`, {
    global_prefix: globalPrefix,
  });
}
