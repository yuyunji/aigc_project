<!--
  音频播放器 — HTML5 audio + 角色标签
-->
<template>
  <div class="audio-player">
    <div class="audio-card" v-for="audio in audios" :key="audio.id">
      <div class="audio-icon">🎙️</div>
      <div class="audio-info">
        <div class="audio-character">{{ audio.character_name || '角色配音' }}</div>
        <audio controls :src="getMediaUrl(audio)" class="audio-element">
          浏览器不支持音频播放。
        </audio>
      </div>
      <el-tag
        :type="audio.status === 'success' ? 'success' : audio.status === 'failed' ? 'danger' : 'warning'"
        size="small"
        effect="plain"
        round
      >
        {{ audio.status === 'success' ? '已生成' : audio.status === 'failed' ? '失败' : '生成中' }}
      </el-tag>
    </div>

    <el-empty v-if="!loading && audios.length === 0" description="配音尚未生成" />
  </div>
</template>

<script setup>
import { getMediaUrl } from "../utils/media";

defineProps({
  audios: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
});
</script>

<style lang="scss" scoped>
.audio-player {
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}

.audio-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 18px;
  background: #FAFAFB;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-light);
}

.audio-icon {
  font-size: 28px;
  flex-shrink: 0;
}

.audio-info {
  flex: 1;
  min-width: 0;
}

.audio-character {
  font-weight: 600;
  font-size: 14px;
  margin-bottom: 6px;
  color: var(--color-text-primary);
}

.audio-element {
  width: 100%;
  height: 36px;
}
</style>
