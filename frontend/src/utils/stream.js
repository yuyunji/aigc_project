/**
 * SSE 状态推送订阅封装（基于 EventSource）
 * handlers: { onTask, onMedia, onAsset, onError }
 * 返回 EventSource 实例，调用方负责在组件卸载时 close()
 */
export function subscribeTaskEvents(taskId, handlers) {
  const es = new EventSource(`/api/tasks/${taskId}/events`);
  _bind(es, handlers);
  return es;
}

export function subscribeGlobalEvents(handlers) {
  const es = new EventSource(`/api/events`);
  _bind(es, handlers);
  return es;
}

function _bind(es, handlers) {
  if (handlers.onTask) es.addEventListener("task", (e) => handlers.onTask(JSON.parse(e.data)));
  if (handlers.onMedia) es.addEventListener("media", (e) => handlers.onMedia(JSON.parse(e.data)));
  if (handlers.onAsset) es.addEventListener("asset", (e) => handlers.onAsset(JSON.parse(e.data)));
  if (handlers.onError) es.onerror = handlers.onError;
}
