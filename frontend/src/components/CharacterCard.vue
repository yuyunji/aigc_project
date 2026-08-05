<!--
  人物角色卡片组件
  以卡片网格展示所有角色
-->
<template>
  <div class="character-card" v-loading="loading">
    <el-empty v-if="!loading && characters.length === 0" description="人物角色尚未生成" />

    <div v-else class="character-grid">
      <el-card
        v-for="char in characters"
        :key="char.id"
        shadow="hover"
        class="character-item"
      >
        <template #header>
          <div class="card-header">
            <span class="char-icon">🎭</span>
            <span class="char-name">{{ char.name }}</span>
          </div>
        </template>
        <div class="markdown-body" v-html="renderMarkdown(char.description)" />
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { renderMarkdown } from "../utils/markdown";

defineProps({
  characters: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
});
</script>

<style lang="scss" scoped>
.character-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
}

.character-item {
  transition: transform 0.2s;
  &:hover {
    transform: translateY(-2px);
  }
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}

.char-name {
  font-size: 16px;
  color: #1a1a2e;
}
</style>
