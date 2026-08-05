<!--
  视频播放器 — 原生 HTML5 video 包装
-->
<template>
  <div class="video-player" v-loading="loading">
    <el-empty v-if="!loading && !src" description="视频尚未生成" />

    <div v-else class="player-wrapper">
      <video
        ref="videoRef"
        :src="src"
        :poster="poster"
        controls
        class="player-video"
        @play="$emit('play')"
        @pause="$emit('pause')"
        @ended="$emit('ended')"
        @error="$emit('error', $event)"
      >
        您的浏览器不支持 HTML5 视频播放。
      </video>

      <div class="player-info" v-if="title">
        <span>{{ title }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from "vue";

defineProps({
  src: { type: String, default: "" },
  poster: { type: String, default: "" },
  title: { type: String, default: "" },
  loading: { type: Boolean, default: false },
});

defineEmits(["play", "pause", "ended", "error"]);

const videoRef = ref(null);
</script>

<style lang="scss" scoped>
.player-wrapper {
  background: #000;
  border-radius: var(--radius-lg);
  overflow: hidden;
}

.player-video {
  width: 100%;
  display: block;
  max-height: 60vh;
}

.player-info {
  padding: 10px 16px;
  background: #1a1a2e;
  color: #fff;
  font-size: 13px;
  font-weight: 500;
}
</style>
