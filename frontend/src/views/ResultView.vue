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

      <!-- 内容 Tabs -->
      <el-card shadow="never" class="content-card">
        <el-tabs v-model="activeTab" type="card">
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
              @generate-video="onGenerateVideo"
              @generate-image="onGenerateImage"
              @retry="onRetryScene"
            />
          </el-tab-pane>

          <el-tab-pane name="assets">
            <template #label>
              <span>🎒 资产拆解</span>
              <el-tag v-if="assets.length" size="small" effect="plain" style="margin-left:6px">{{ assets.length }}</el-tag>
            </template>
            <AssetBreakdownTab
              :assets="assets"
              :loading="assetsLoading"
              :extracting="extracting"
              @extract="onExtractAssets"
              @generate-image="onGenerateAssetImage"
              @upload-image="onUploadAssetImage"
              @delete-asset="onDeleteAsset"
              @create-asset="onCreateAsset"
              @update-asset="onUpdateAsset"
            />
          </el-tab-pane>
        </el-tabs>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { getTaskList, getStoryboards } from "../api/task";
import { getVideos, getImages, generateSceneVideo, generateSceneImage, retryScene } from "../api/media";
import { extractAssets, getAssets, createAsset, updateAsset, deleteAsset, generateAssetImage, uploadAssetImage } from "../api/asset";
import StoryboardCard from "../components/StoryboardCard.vue";
import AssetBreakdownTab from "../components/AssetBreakdownTab.vue";

const route = useRoute();
const router = useRouter();

const selectedTaskId = ref("");
const completedTasks = ref([]);
const tasksLoading = ref(false);
const storyboards = ref([]);
const mediaAssets = ref([]);
const storyboardsLoading = ref(false);
const activeTab = ref("storyboards");

// 资产拆解
const assets = ref([]);
const assetsLoading = ref(false);
const extracting = ref(false);

// Pipeline 进度计算
const pipelineStages = computed(() => {
  const p = completedTasks.value.find(t => t.id === selectedTaskId.value);
  if (!p) return [];
  return [
    { label: "分片预处理", done: p.progress >= 20, active: p.progress >= 10 && p.progress < 20 },
    { label: "AI分镜师 分镜拆解", done: p.progress >= 78, active: p.progress >= 25 && p.progress < 78 },
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
  if (!taskId) { storyboards.value = []; assets.value = []; return; }
  await loadResults(taskId);
}

async function loadResults(taskId) {
  await Promise.allSettled([
    (async () => { storyboardsLoading.value = true; try { const r = await getStoryboards(taskId); storyboards.value = r.data || []; } catch(e){} finally { storyboardsLoading.value = false; } })(),
    (async () => { try { const [i, v] = await Promise.all([getImages(taskId), getVideos(taskId)]); mediaAssets.value = [...(i.data.assets||[]), ...(v.data.assets||[])]; } catch(e){} })(),
    (async () => { await loadAssets(taskId); })(),
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


// ── 资产拆解 ──

async function loadAssets(taskId) {
  assetsLoading.value = true;
  try {
    const r = await getAssets(taskId);
    assets.value = r.data.assets || [];
  } catch (e) { /* empty */ }
  finally { assetsLoading.value = false; }
}

async function onExtractAssets() {
  extracting.value = true;
  try {
    const r = await extractAssets(selectedTaskId.value);
    ElMessage.success(`AI 提取完成：${r.data.extracted} 个资产`);
    await loadAssets(selectedTaskId.value);
  } catch (e) { /* global handler */ }
  finally { extracting.value = false; }
}

async function onGenerateAssetImage(assetId) {
  try {
    await generateAssetImage(selectedTaskId.value, assetId);
    ElMessage.success("图片生成已启动");
    pollAssetImage(assetId, 0);
  } catch (e) { /* global handler */ }
}

async function silentRefreshAssets() {
  try {
    const r = await getAssets(selectedTaskId.value);
    if (r.data && r.data.assets) {
      // 无感更新：直接替换数据，不触发 loading 状态
      assets.value = r.data.assets;
    }
  } catch (e) { /* silent */ }
}

function pollAssetImage(assetId, count) {
  if (count >= 30) return;
  setTimeout(async () => {
    await silentRefreshAssets();
    const found = assets.value.find(a => a.id === assetId);
    if (found && found.image_status === "success") {
      ElMessage.success(`${found.name} 图片生成完成`);
      return;
    }
    if (found && found.image_status === "failed") {
      ElMessage.error(`${found.name} 生成失败: ${found.error_message || "未知错误"}`);
      return;
    }
    pollAssetImage(assetId, count + 1);
  }, 8000);
}

async function onUploadAssetImage(assetId, file) {
  try {
    await uploadAssetImage(selectedTaskId.value, assetId, file);
    ElMessage.success("图片上传成功");
    await loadAssets(selectedTaskId.value);
  } catch (e) { /* global handler */ }
}

async function onDeleteAsset(assetId) {
  try {
    await deleteAsset(selectedTaskId.value, assetId);
    ElMessage.success("资产已删除");
    await loadAssets(selectedTaskId.value);
  } catch (e) { /* global handler */ }
}

async function onCreateAsset(data) {
  try {
    await createAsset(selectedTaskId.value, data);
    ElMessage.success("资产已添加");
    await loadAssets(selectedTaskId.value);
  } catch (e) { /* global handler */ }
}

async function onUpdateAsset(assetId, data) {
  try {
    await updateAsset(selectedTaskId.value, assetId, data);
    ElMessage.success("资产已更新");
    await loadAssets(selectedTaskId.value);
  } catch (e) { /* global handler */ }
}

onMounted(() => {
  loadCompletedTasks();
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && selectedTaskId.value) {
      loadResults(selectedTaskId.value).then(() => {
        if (mediaAssets.value.some(m => m.status === "running")) pollUntilDone();
      });
    }
  });
});
onUnmounted(() => {
  document.removeEventListener("visibilitychange", () => {});
});
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

.storyboard-toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: var(--space-lg);
}

.toolbar-hint {
  font-size: 12px;
  color: var(--color-text-tertiary);
  margin-left: 8px;
  font-weight: 400;
}

@media (max-width: 640px) { .task-select { width: 100%; } }

</style>
