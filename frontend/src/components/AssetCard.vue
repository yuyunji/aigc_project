<!--
  资产卡片 —— 显示缩略图 + 生成/上传/编辑/删除按钮
-->
<template>
  <div class="inner-card">
    <div class="card-header">
      <span class="card-name">{{ asset.name }}</span>
      <el-tag :type="statusTag(asset.image_status)" size="small">{{ statusLabel(asset.image_status) }}</el-tag>
    </div>
    <div class="card-desc" v-if="asset.description">{{ asset.description }}</div>
    <div class="card-image" v-if="asset.image_path || asset.image_url">
      <img :src="getImgUrl(asset)" :alt="asset.name" @click="previewImg(asset)" />
    </div>
    <div class="card-actions">
      <el-button size="small" type="primary" plain :loading="asset.image_status === 'running'" @click="$emit('generate')">
        🎨 生成图片
      </el-button>
      <el-upload
        :show-file-list="false"
        :before-upload="(f) => { $emit('upload', f); return false; }"
        accept="image/png,image/jpeg,image/webp,image/gif"
      >
        <el-button size="small" plain>📎 上传</el-button>
      </el-upload>
      <el-button size="small" plain @click="$emit('edit')">✏️</el-button>
      <el-button size="small" type="danger" plain @click="$emit('delete')">🗑️</el-button>
    </div>
    <div class="card-error" v-if="asset.error_message">{{ asset.error_message }}</div>
  </div>
</template>

<script setup>
import { getMediaUrl } from "../utils/media";

defineProps({ asset: { type: Object, required: true } });
defineEmits(["generate", "upload", "edit", "delete"]);

function statusTag(s) {
  return s === "success" ? "success" : s === "failed" ? "danger" : s === "running" ? "warning" : "info";
}
function statusLabel(s) {
  return s === "success" ? "已生成" : s === "failed" ? "失败" : s === "running" ? "生成中" : "待生成";
}
function getImgUrl(a) {
  if (a.url) return a.url;
  if (a.image_url) return a.image_url;
  return getMediaUrl(a);
}
function previewImg(a) {
  const url = getImgUrl(a);
  if (url) window.open(url, "_blank");
}
</script>

<style lang="scss" scoped>
.inner-card {
  border: 1px solid var(--color-border-light); border-radius: var(--radius-md);
  padding: 14px; margin-bottom: 12px; background: var(--color-bg-primary);
  transition: box-shadow 0.2s;
  &:hover { box-shadow: var(--shadow-sm); }
}
.card-header {
  display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px;
}
.card-name { font-size: 14px; font-weight: 600; color: var(--color-text-primary); }
.card-desc {
  font-size: 12px; line-height: 1.6; color: var(--color-text-secondary);
  margin-bottom: 10px; max-height: 80px; overflow-y: auto;
}
.card-image {
  margin-bottom: 10px; border-radius: var(--radius-sm); overflow: hidden; cursor: pointer;
  img { width: 100%; aspect-ratio: 16/9; object-fit: cover; display: block; }
}
.card-actions { display: flex; gap: 6px; flex-wrap: wrap; }
.card-actions :deep(.el-upload) { display: inline-block; }
.card-error { margin-top: 8px; font-size: 12px; color: var(--color-danger); }
</style>
