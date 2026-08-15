<!--
  分镜图片画廊 — Bento Grid 展示生成的图片
-->
<template>
  <div class="image-gallery" v-loading="loading">
    <el-empty v-if="!loading && images.length === 0" description="分镜图片尚未生成" />

    <div v-else class="gallery-grid">
      <el-card
        v-for="img in images"
        :key="img.id"
        shadow="hover"
        class="gallery-card"
        :class="{ 'is-error': img.status === 'failed' }"
      >
        <div class="card-badge">分镜 {{ img.scene_number }}</div>

        <div v-if="img.status === 'success' && img.file_path" class="card-image">
          <img
            :src="getMediaUrl(img)"
            :alt="`分镜 ${img.scene_number}`"
            loading="lazy"
            @click="previewImage(img)"
          />
        </div>

        <div v-else-if="img.status === 'running'" class="card-loading">
          <span class="loading-spinner"></span>
          <span>生成中...</span>
        </div>

        <div v-else class="card-error">
          <span>⚠️</span>
          <span class="error-text">{{ img.error_message || '生成失败' }}</span>
        </div>

        <div class="card-prompt" v-if="img.prompt">
          {{ img.prompt.slice(0, 100) }}{{ img.prompt.length > 100 ? '...' : '' }}
        </div>
      </el-card>
    </div>

    <!-- 预览弹窗 -->
    <el-dialog v-model="previewVisible" title="图片预览" width="80%" :close-on-click-modal="true">
      <img :src="previewSrc" style="width:100%;border-radius:8px" v-if="previewSrc" />
    </el-dialog>
  </div>
</template>

<script setup>
import { ref } from "vue";
import { getMediaUrl } from "../utils/media";

const props = defineProps({
  images: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
});

const previewVisible = ref(false);
const previewSrc = ref("");

function previewImage(img) {
  previewSrc.value = getMediaUrl(img);
  previewVisible.value = true;
}
</script>

<style lang="scss" scoped>
.gallery-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: var(--space-md);
}

.gallery-card {
  transition: all var(--transition-base);
  :deep(.el-card__body) { padding: 0; }

  &:hover { transform: translateY(-2px); }
  &.is-error { border-color: var(--color-danger); }
}

.card-badge {
  position: absolute;
  top: 8px;
  left: 8px;
  z-index: 1;
  background: var(--color-primary);
  color: #fff;
  padding: 2px 10px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 600;
}

.card-image {
  aspect-ratio: 16 / 9;
  overflow: hidden;
  cursor: pointer;

  img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    transition: transform var(--transition-slow);
  }

  &:hover img { transform: scale(1.05); }
}

.card-loading, .card-error {
  aspect-ratio: 16 / 9;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: var(--color-border-light);
  color: var(--color-text-secondary);
  font-size: 13px;
}

.card-error {
  color: var(--color-danger);
  .error-text { font-size: 12px; text-align: center; padding: 0 12px; }
}

.loading-spinner {
  width: 24px;
  height: 24px;
  border: 3px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.card-prompt {
  padding: 10px 14px;
  font-size: 12px;
  color: var(--color-text-tertiary);
  line-height: 1.5;
  border-top: 1px solid var(--color-border-light);
}

@keyframes spin { to { transform: rotate(360deg); } }
</style>
