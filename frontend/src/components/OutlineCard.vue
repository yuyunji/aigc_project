<!--
  大纲卡片组件
  展示剧本大纲内容，简单 Markdown 渲染
-->
<template>
  <div class="outline-card" v-loading="loading">
    <el-empty v-if="!loading && !content" description="大纲尚未生成" />

    <el-card v-else shadow="hover">
      <template #header>
        <div class="card-header">
          <span>📋 剧本大纲</span>
        </div>
      </template>
      <div class="markdown-body" v-html="renderedMarkdown" />
    </el-card>
  </div>
</template>

<script setup>
import { computed } from "vue";
import { renderMarkdown } from "../utils/markdown";

const props = defineProps({
  content: { type: String, default: "" },
  loading: { type: Boolean, default: false },
});

const renderedMarkdown = computed(() => renderMarkdown(props.content));
</script>

<style lang="scss" scoped>
.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}
</style>
