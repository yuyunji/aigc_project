<!--
  分镜脚本卡片组件
  以时间线样式展示分镜列表
-->
<template>
  <div class="storyboard-card" v-loading="loading">
    <el-empty v-if="!loading && scenes.length === 0" description="分镜脚本尚未生成" />

    <div v-else class="storyboard-timeline">
      <div
        v-for="scene in scenes"
        :key="scene.id"
        class="timeline-item"
      >
        <div class="timeline-marker">
          <span class="scene-num">{{ scene.scene_number }}</span>
        </div>
        <el-card shadow="hover" class="timeline-card">
          <div class="markdown-body" v-html="renderMarkdown(scene.description)" />
        </el-card>
      </div>
    </div>
  </div>
</template>

<script setup>
import { renderMarkdown } from "../utils/markdown";

defineProps({
  scenes: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
});
</script>

<style lang="scss" scoped>
.storyboard-timeline {
  position: relative;
  padding-left: 40px;

  // 竖线
  &::before {
    content: "";
    position: absolute;
    left: 19px;
    top: 0;
    bottom: 0;
    width: 2px;
    background: linear-gradient(to bottom, var(--primary), var(--primary) 80%, transparent);
  }
}

.timeline-item {
  position: relative;
  margin-bottom: 20px;

  &:last-child { margin-bottom: 0; }
}

.timeline-marker {
  position: absolute;
  left: -40px;
  top: 12px;
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;

  .scene-num {
    width: 32px;
    height: 32px;
    border-radius: 50%;
    background: var(--primary);
    color: #fff;
    font-size: 13px;
    font-weight: 600;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 2px 8px rgba(64, 158, 255, 0.3);
  }
}

.timeline-card {
  transition: transform 0.2s;
  &:hover {
    transform: translateX(4px);
  }
}
</style>
