<!--
  结果预览页 — Tab 切换：大纲 / 角色 / 分镜（全宽布局）
  参考 Yihen-Drama / Koma 平台设计
-->
<template>
  <div class="result-page">
    <div class="page-hero">
      <h1 class="page-title">📖 结果预览</h1>
      <p class="page-desc">选择一个已完成的任务，查看 AI 生成结果</p>
    </div>

    <!-- 任务选择 -->
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
        <el-option v-for="t in completedTasks" :key="t.id" :label="t.title" :value="t.id" />
      </el-select>
    </div>

    <!-- 空状态 -->
    <div v-if="!selectedTaskId" class="result-placeholder">
      <el-empty description="请从上方下拉框选择一个已完成的任务" />
    </div>

    <div v-else>
      <!-- Pipeline 进度条 -->
      <el-card shadow="never" class="pipeline-bar">
        <div class="pipe-steps">
          <div v-for="s in pipelineStages" :key="s.label" class="pipe-step" :class="{ done: s.done, active: s.active }">
            <span class="pipe-dot">{{ s.done ? '✓' : s.active ? '●' : '○' }}</span>
            <span class="pipe-label">{{ s.label }}</span>
          </div>
        </div>
      </el-card>

      <!-- Tab 切换 -->
      <el-card shadow="never" class="content-card">
        <el-tabs v-model="activeTab" type="card" class="result-tabs">
          <el-tab-pane name="outline">
            <template #label><span>📋 剧本大纲</span></template>
            <OutlineTab :content="outline?.content" :loading="outlineLoading" />
          </el-tab-pane>

          <el-tab-pane name="characters">
            <template #label>
              <span>🎭 人物角色</span>
              <el-tag v-if="characters.length" size="small" effect="plain" style="margin-left:6px">{{ characters.length }}</el-tag>
            </template>
            <CharacterTab :characters="characters" :loading="charactersLoading" />
          </el-tab-pane>

          <el-tab-pane name="storyboards">
            <template #label>
              <span>🎬 分镜脚本</span>
              <el-tag v-if="storyboards.length" size="small" effect="plain" style="margin-left:6px">{{ storyboards.length }}</el-tag>
            </template>
            <StoryboardCard
              :scenes="storyboards"
              :loading="storyboardsLoading"
              :taskId="selectedTaskId"
              :mediaAssets="mediaAssets"
              @generate-image="onGenerateImage"
              @generate-video="onGenerateVideo"
              @retry="onRetryScene"
            />
          </el-tab-pane>
        </el-tabs>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { getTaskList, getOutline, getCharacters, getStoryboards } from "../api/task";
import { getVideos, getImages, generateSceneImage, generateSceneVideo, retryScene } from "../api/media";
import OutlineTab from "../components/OutlineTab.vue";
import CharacterTab from "../components/CharacterTab.vue";
import StoryboardCard from "../components/StoryboardCard.vue";

const route = useRoute();
const router = useRouter();

const selectedTaskId = ref("");
const completedTasks = ref([]);
const tasksLoading = ref(false);
const outline = ref(null);
const characters = ref([]);
const storyboards = ref([]);
const mediaAssets = ref([]);
const outlineLoading = ref(false);
const charactersLoading = ref(false);
const storyboardsLoading = ref(false);
const activeTab = ref("outline");

// Pipeline 进度计算
const pipelineStages = computed(() => {
  const p = completedTasks.value.find(t => t.id === selectedTaskId.value);
  if (!p) return [];
  return [
    { label: "分片预处理", done: p.progress >= 20, active: p.progress >= 10 && p.progress < 20 },
    { label: "剧本大纲", done: p.progress >= 45, active: p.progress >= 25 && p.progress < 45 },
    { label: "人物角色", done: p.progress >= 70, active: p.progress >= 50 && p.progress < 70 },
    { label: "分镜脚本", done: p.progress >= 78, active: p.progress >= 70 && p.progress < 78 },
  ];
});

async function loadCompletedTasks() {
  tasksLoading.value = true;
  try {
    const res = await getTaskList();
    completedTasks.value = (res.data.tasks || []).filter(t => t.status === "success");
    const queryId = route.query.taskId;
    if (queryId && completedTasks.value.some(t => t.id === queryId)) {
      selectedTaskId.value = queryId;
      await loadResults(queryId);
      router.replace({ path: "/results" });
    }
  } catch (e) {}
  finally { tasksLoading.value = false; }
}

async function onTaskSelect(taskId) {
  if (!taskId) { outline.value = null; characters.value = []; storyboards.value = []; return; }
  await loadResults(taskId);
}

async function loadResults(taskId) {
  await Promise.allSettled([
    (async () => { outlineLoading.value = true; try { const r = await getOutline(taskId); outline.value = r.data; } catch(e){} finally { outlineLoading.value = false; } })(),
    (async () => { charactersLoading.value = true; try { const r = await getCharacters(taskId); characters.value = r.data || []; } catch(e){} finally { charactersLoading.value = false; } })(),
    (async () => { storyboardsLoading.value = true; try { const r = await getStoryboards(taskId); storyboards.value = r.data || []; } catch(e){} finally { storyboardsLoading.value = false; } })(),
    (async () => { try { const [i, v] = await Promise.all([getImages(taskId), getVideos(taskId)]); mediaAssets.value = [...(i.data.assets||[]), ...(v.data.assets||[])]; } catch(e){} })(),
  ]);
}

function pollUntilDone(pollCount = 0) {
  const MAX = 40; // 最多轮询 40 次（约 2 分钟）
  if (pollCount >= MAX) return;
  setTimeout(async () => {
    await loadResults(selectedTaskId.value);
    const stillRunning = mediaAssets.value.some(m => m.status === 'running');
    if (stillRunning) pollUntilDone(pollCount + 1);
  }, 3000);
}

async function onGenerateImage(sn) {
  const provider = localStorage.getItem("aigc_image_provider") || "minimax";
  try { await generateSceneImage(selectedTaskId.value, sn, provider); ElMessage.success(`分镜${sn} 图片生成已启动`); pollUntilDone(); } catch(e){}
}
async function onGenerateVideo(sn) {
  const provider = localStorage.getItem("aigc_video_provider") || "minimax-h3";
  try { await generateSceneVideo(selectedTaskId.value, sn, provider); ElMessage.success(`分镜${sn} 视频生成已启动`); pollUntilDone(); } catch(e){}
}
async function onRetryScene(sn) { try { await retryScene(selectedTaskId.value, sn); ElMessage.success(`分镜${sn} 已重置`); await loadResults(selectedTaskId.value); } catch(e){} }

onMounted(() => loadCompletedTasks());
</script>

<style lang="scss" scoped>
.result-page { max-width: 1200px; }
.page-hero { margin-bottom: var(--space-lg); }
.result-toolbar { margin-bottom: var(--space-lg); }
.task-select { width: 440px; }
.result-placeholder { padding: 64px 0; text-align: center; }

/* Pipeline 进度条 */
.pipeline-bar {
  margin-bottom: var(--space-md);
  :deep(.el-card__body) { padding: 14px 24px; }
}
.pipe-steps {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.pipe-step {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--color-text-tertiary);
  &.done { color: var(--color-success); font-weight: 600; }
  &.active { color: var(--color-primary); font-weight: 600; }
}
.pipe-dot { font-size: 11px; }

/* Tab 内容 */
.content-card {
  :deep(.el-card__body) { padding: var(--space-lg); }
  :deep(.el-tabs__header) { margin-bottom: var(--space-lg); }
}

@media (max-width: 640px) { .task-select { width: 100%; } }
</style>
