// 媒体 URL 解析：优先用后端返回的 OSS 签名 URL，否则回退到本地 /media 相对路径
export function getMediaUrl(item) {
  if (!item) return "";
  if (item.url) return item.url;
  const p = item.file_path || item.image_path || "";
  if (!p) return "";
  const parts = p.replace(/\\/g, "/").split("/media/");
  return parts.length > 1 ? `/media/${parts[1]}` : p;
}
