<!--
  结果预览页 —— Bento Grid 卡片 + AI Purple 主题
-->
<template>
  <div class="result-page">
    <div class="page-hero">
      <h1 class="page-title">📖 结果预览</h1>
      <p class="page-desc">选择一个已完成的任务，查看 AI 生成结果</p>
    </div>

    <!-- 任务选择器 -->
    <div class="result-toolbar">
      <el-select
        v-model="selectedTaskId"
        placeholder="请选择已完成的任务..."
        @change="onTaskSelect"
        clearable
        size="large"
        :loading="tasksLoading"
        class="task-select"
      >
        <el-option
          v-for="t in completedTasks"
          :key="t.id"
          :label="t.title"
          :value="t.id"
        >
          <div class="select-option">
            <span>{{ t.title }}</span>
            <span class="select-date">{{ formatTime(t.created_at) }}</span>
          </div>
        </el-option>
        <template #empty>
          <div class="select-empty">暂无已完成的任务</div>
        </template>
      </el-select>
    </div>

    <!-- 空状态 -->
    <div v-if="!selectedTaskId" class="result-placeholder">
      <div class="placeholder-card">
        <span class="placeholder-icon">📂</span>
        <p>请从上方下拉框选择一个已完成的任务</p>
      </div>
    </div>

    <!-- 结果展示 —— Bento Grid 三卡片布局 -->
    <div v-else class="result-grid">
      <!-- 大纲卡片 -->
      <el-card shadow="never" class="result-card card-outline">
        <template #header>
          <div class="card-header">
            <span class="card-icon">📋</span>
            <span>剧本大纲</span>
            <el-tag v-if="!outlineLoading && outline" size="small" type="success" effect="plain">已生成</el-tag>
          </div>
        </template>
        <div v-loading="outlineLoading" class="card-body">
          <el-empty v-if="!outlineLoading && !outline" description="大纲尚未生成" />
          <div v-else-if="outline" class="markdown-body" v-html="renderMarkdown(outline.content)" />
        </div>
      </el-card>

      <!-- 人物卡片 -->
      <el-card shadow="never" class="result-card card-characters">
        <template #header>
          <div class="card-header">
            <span class="card-icon">🎭</span>
            <span>人物角色</span>
            <el-tag v-if="!charactersLoading && characters.length" size="small" type="success" effect="plain">
              {{ characters.length }} 个角色
            </el-tag>
          </div>
        </template>
        <div v-loading="charactersLoading" class="card-body">
          <el-empty v-if="!charactersLoading && characters.length === 0" description="人物角色尚未生成" />
          <div v-else class="character-grid">
            <div v-for="char in characters" :key="char.id" class="character-item">
              <div class="char-header">
                <span class="char-icon">🎭</span>
                <span class="char-name">{{ char.name }}</span>
              </div>
              <div class="markdown-body" v-html="renderMarkdown(char.description)" />
            </div>
          </div>
        </div>
      </el-card>

      <!-- 分镜卡片 -->
      <el-card shadow="never" class="result-card card-storyboard">
        <template #header>
          <div class="card-header">
            <span class="card-icon">🎬</span>
            <span>分镜脚本</span>
            <el-tag v-if="!storyboardsLoading && storyboards.length" size="small" type="success" effect="plain">
              {{ storyboards.length }} 个分镜
            </el-tag>
          </div>
        </template>
        <div v-loading="storyboardsLoading" class="card-body">
          <el-empty v-if="!storyboardsLoading && storyboards.length === 0" description="分镜脚本尚未生成" />
          <div v-else class="storyboard-timeline">
            <div v-for="scene in storyboards" :key="scene.id" class="timeline-item">
              <div class="timeline-marker">
                <span>{{ scene.scene_number }}</span>
              </div>
              <div class="markdown-body" v-html="renderMarkdown(scene.description)" />
            </div>
          </div>
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { getTaskList, getOutline, getCharacters, getStoryboards } from "../api/task";
import { renderMarkdown } from "../utils/markdown";

const route = useRoute();
const router = useRouter();

const selectedTaskId = ref("");
const completedTasks = ref([]);
const tasksLoading = ref(false);
const outline = ref(null);
const characters = ref([]);
const storyboards = ref([]);
const outlineLoading = ref(false);
const charactersLoading = ref(false);
const storyboardsLoading = ref(false);

async function loadCompletedTasks() {
  tasksLoading.value = true;
  try {
    const res = await getTaskList();
    completedTasks.value = (res.data.tasks || []).filter((t) => t.status === "success");
    const queryId = route.query.taskId;
    if (queryId && completedTasks.value.some((t) => t.id === queryId)) {
      selectedTaskId.value = queryId;
      await loadResults(queryId);
      router.replace({ path: "/results" });
    }
  } catch (e) {
    // 全局拦截已处理
  } finally {
    tasksLoading.value = false;
  }
}

async function onTaskSelect(taskId) {
  if (!taskId) {
    outline.value = null;
    characters.value = [];
    storyboards.value = [];
    return;
  }
  await loadResults(taskId);
}

async function loadResults(taskId) {
  await Promise.allSettled([
    (async () => {
      outlineLoading.value = true;
      try { const res = await getOutline(taskId); outline.value = res.data; } catch (e) {}
      finally { outlineLoading.value = false; }
    })(),
    (async () => {
      charactersLoading.value = true;
      try { const res = await getCharacters(taskId); characters.value = res.data || []; } catch (e) {}
      finally { charactersLoading.value = false; }
    })(),
    (async () => {
      storyboardsLoading.value = true;
      try { const res = await getStoryboards(taskId); storyboards.value = res.data || []; } catch (e) {}
      finally { storyboardsLoading.value = false; }
    })(),
  ]);
}

function formatTime(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

onMounted(() => loadCompletedTasks());
</script>

<style lang="scss" scoped>
.result-page {
  max-width: 1200px;
}

.page-hero {
  margin-bottom: var(--space-lg);
}

.result-toolbar {
  margin-bottom: var(--space-lg);
}

.task-select {
  width: 440px;
}

.select-option {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.select-date {
  font-size: 12px;
  color: var(--color-text-tertiary);
}
.select-empty {
  padding: 16px;
  text-align: center;
  color: var(--color-text-tertiary);
}

// 占位卡片
.result-placeholder {
  display: flex;
  justify-content: center;
  padding: 64px 0;
}
.placeholder-card {
  text-align: center;
  color: var(--color-text-tertiary);
  .placeholder-icon { font-size: 48px; display: block; margin-bottom: 12px; }
  p { font-size: 14px; }
}

// Bento Grid 三卡片布局
.result-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-lg);
  align-items: start;
}

.result-card {
  border: 1px solid var(--color-border-light);
  box-shadow: var(--shadow-sm) !important;
  transition: box-shadow var(--transition-base), transform var(--transition-base);

  &:hover {
    box-shadow: var(--shadow-md) !important;
    transform: translateY(-2px);
  }

  :deep(.el-card__header) {
    padding: 14px 18px;
    background: #FAFAFB;
    border-bottom: 1px solid var(--color-border-light);
  }

  :deep(.el-card__body) {
    padding: 18px;
    max-height: 65vh;
    overflow-y: auto;
  }
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  font-size: 14px;

  .card-icon { font-size: 16px; }
  .el-tag { margin-left: auto; }
}

.card-body {
  min-height: 120px;
}

// 角色小网格
.character-grid {
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}
.character-item {
  padding: 14px;
  background: #FAFAFB;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-light);
}
.char-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
  font-weight: 600;
  font-size: 14px;
  color: var(--color-text-primary);
  .char-icon { font-size: 16px; }
}

// 分镜时间线
.storyboard-timeline {
  position: relative;
  padding-left: 28px;

  &::before {
    content: "";
    position: absolute;
    left: 11px;
    top: 0;
    bottom: 0;
    width: 2px;
    background: linear-gradient(to bottom, var(--color-primary), var(--color-primary-light) 80%, transparent);
  }
}
.timeline-item {
  position: relative;
  margin-bottom: 20px;
  &:last-child { margin-bottom: 0; }
}
.timeline-marker {
  position: absolute;
  left: -28px;
  top: 2px;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--color-primary);
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 0 0 4px var(--color-primary-bg);
}

@media (max-width: 992px) {
  .result-grid {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 640px) {
  .task-select {
    width: 100%;
  }
}
</style>
