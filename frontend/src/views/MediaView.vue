<!--
  媒体预览页 — MiniMax-H3 视频生成流程
  管线进度 + 分镜视频片段 + 最终合成
-->
<template>
  <div class="media-page">
    <div class="page-hero">
      <h1 class="page-title">🎬 视频生成</h1>
      <p class="page-desc">分镜→图片→视频→拼接：AI 级联媒体生成</p>
    </div>

    <!-- Provider 选择 + 流程图入口 -->
    <div class="media-toolbar">
      <el-select
        v-model="selectedTaskId"
        placeholder="请选择已完成分镜的任务..."
        @change="onTaskSelect"
        clearable
        size="large"
        :loading="tasksLoading"
        style="width: 320px"
      >
        <el-option
          v-for="t in eligibleTasks"
          :key="t.id"
          :label="t.title"
          :value="t.id"
        />
      </el-select>

      <el-select
        v-model="imageProvider"
        size="large"
        style="width: 180px; margin-left: 12px"
        @change="onProviderChange"
      >
        <el-option label="🖼️ MiniMax image-01" value="minimax" />
        <el-option label="🖼️ GPT-Image-2" value="gpt-image-2" />
      </el-select>

      <el-select
        v-model="videoProvider"
        size="large"
        style="width: 180px; margin-left: 8px"
        @change="onProviderChange"
      >
        <el-option label="🎥 MiniMax-H3" value="minimax-h3" />
      </el-select>

      <el-button
        v-if="selectedTaskId"
        type="primary"
        style="margin-left: 12px"
        @click="triggerGeneration"
        :loading="triggering"
      >
        🎥 批量生成视频
      </el-button>

      <el-button
        v-if="selectedTaskId && imageProvider === 'gpt-image-2'"
        type="warning"
        style="margin-left: 8px"
        @click="triggerFlowchart"
        :loading="flowchartGenerating"
      >
        🎬 生成导演流程图
      </el-button>
    </div>

    <el-empty v-if="!selectedTaskId" description="请选择任务" />

    <div v-else>
      <!-- 流程进度 -->
      <el-card shadow="never" class="section-card">
        <template #header><span class="section-title">📊 生成进度</span></template>
        <MediaPipeline :stages="pipeline?.stages || []" :loading="pipelineLoading" />
      </el-card>

      <!-- 视频片段 (MiniMax-H3) -->
      <el-card shadow="never" class="section-card">
        <template #header>
          <span class="section-title">🎥 分镜视频片段</span>
          <el-tag v-if="videos.length" size="small" effect="plain" style="margin-left:8px">
            {{ videos.filter(v=>v.status==='success').length }}/{{ videos.length }}
          </el-tag>
        </template>
        <div v-if="videos.length" v-loading="videosLoading" class="video-grid">
          <VideoPlayer
            v-for="v in videos"
            :key="v.id"
            :src="getMediaUrl(v.file_path)"
            :title="`分镜 ${v.scene_number} ` + (v.status === 'success' ? '✅' : v.status === 'failed' ? '❌' : '⏳')"
            :loading="false"
          />
        </div>
        <el-empty v-else-if="!videosLoading" description="视频尚未生成，点击「生成视频」按钮" />
      </el-card>

      <!-- 最终合成 -->
      <el-card shadow="never" class="section-card" v-if="composite && composite.status === 'success'">
        <template #header>
          <span class="section-title">🎬 最终合成视频</span>
          <el-tag type="success" size="small" effect="plain" style="margin-left:8px">已完成</el-tag>
        </template>
        <VideoPlayer
          :src="getMediaUrl(composite.file_path)"
          title="完整短剧"
          :loading="compositeLoading"
        />
      </el-card>

      <!-- 导演流程图 -->
      <el-card shadow="never" class="section-card" v-if="flowchart">
        <template #header>
          <span class="section-title">🎬 导演流程图</span>
          <el-tag v-if="flowchart.status === 'success'" type="success" size="small" effect="plain" style="margin-left:8px">已完成</el-tag>
          <el-tag v-else-if="flowchart.status === 'failed'" type="danger" size="small" effect="plain" style="margin-left:8px">失败</el-tag>
          <el-tag v-else type="warning" size="small" effect="plain" style="margin-left:8px">生成中</el-tag>
        </template>
        <div v-if="flowchart.status === 'success'" class="flowchart-preview">
          <img :src="getMediaUrl(flowchart.file_path)" alt="导演流程图" @click="showFlowchartFull = true" />
        </div>
        <el-alert v-if="flowchart.status === 'failed'" :title="flowchart.error_message" type="error" show-icon :closable="false" />
        <div v-if="flowchart.status === 'running'" class="flowchart-loading">
          <el-icon class="is-loading" :size="32"><Loading /></el-icon>
          <p>GPT-Image-2 正在生成导演流程图...</p>
        </div>
      </el-card>

      <!-- 流程图全屏预览 -->
      <el-dialog v-model="showFlowchartFull" title="导演流程图" width="90%" top="2vh">
        <img v-if="flowchart" :src="getMediaUrl(flowchart.file_path)" style="width:100%" alt="导演流程图全屏" />
      </el-dialog>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { Loading } from "@element-plus/icons-vue";
import VideoPlayer from "../components/VideoPlayer.vue";
import MediaPipeline from "../components/MediaPipeline.vue";
import { getTaskList } from "../api/task";
import { getPipelineProgress, getVideos, getComposite, getFlowchart, generateFlowchart } from "../api/media";
import apiClient from "../api/index";

const route = useRoute();
const router = useRouter();

const selectedTaskId = ref("");
const eligibleTasks = ref([]);
const tasksLoading = ref(false);
const triggering = ref(false);
const pipeline = ref(null);
const pipelineLoading = ref(false);
const videos = ref([]);
const videosLoading = ref(false);
const composite = ref(null);
const compositeLoading = ref(false);

// Provider 选择（从 localStorage 恢复）
const imageProvider = ref(localStorage.getItem("aigc_image_provider") || "minimax");
const videoProvider = ref(localStorage.getItem("aigc_video_provider") || "minimax-h3");

// 流程图
const flowchart = ref(null);
const flowchartGenerating = ref(false);
const showFlowchartFull = ref(false);

async function loadEligibleTasks() {
  tasksLoading.value = true;
  try {
    const res = await getTaskList();
    eligibleTasks.value = (res.data.tasks || []).filter(
      (t) => t.status === "success" || t.progress >= 78
    );
    const queryId = route.query.taskId;
    if (queryId) {
      selectedTaskId.value = queryId;
      await loadAll(queryId);
      router.replace({ path: "/media" });
    }
  } catch (e) { /* global handler */ }
  finally { tasksLoading.value = false; }
}

async function onTaskSelect(taskId) {
  if (!taskId) return;
  await loadAll(taskId);
}

async function triggerGeneration() {
  if (!selectedTaskId.value) return;
  triggering.value = true;
  try {
    await apiClient.post(`/api/media/${selectedTaskId.value}/generate`);
    ElMessage.success("视频生成已启动，请等待...");
    // 轮询进度
    let polls = 0;
    const poller = setInterval(async () => {
      await loadAll(selectedTaskId.value);
      polls++;
      const vids = videos.value.filter((v) => v.status === "success");
      if (vids.length > 0 || polls > 60) clearInterval(poller);
    }, 5000);
  } catch (e) { /* global handler */ }
  finally { triggering.value = false; }
}

function onProviderChange() {
  localStorage.setItem("aigc_image_provider", imageProvider.value);
  localStorage.setItem("aigc_video_provider", videoProvider.value);
}

async function triggerFlowchart() {
  if (!selectedTaskId.value) return;
  flowchartGenerating.value = true;
  try {
    await generateFlowchart(selectedTaskId.value);
    ElMessage.success("导演流程图生成已启动，请等待...");
    let polls = 0;
    const poller = setInterval(async () => {
      await loadFlowchart(selectedTaskId.value);
      polls++;
      if (flowchart.value?.status === "success" || flowchart.value?.status === "failed" || polls > 30) {
        clearInterval(poller);
        if (flowchart.value?.status === "success") ElMessage.success("导演流程图已生成！");
      }
    }, 5000);
  } catch (e) { /* global handler */ }
  finally { flowchartGenerating.value = false; }
}

async function loadFlowchart(taskId) {
  try {
    const r = await getFlowchart(taskId);
    flowchart.value = r.data;
  } catch (e) { /* ignore */ }
}

async function loadAll(taskId) {
  await Promise.allSettled([
    (async () => { pipelineLoading.value = true; try { const r = await getPipelineProgress(taskId); pipeline.value = r.data; } catch(e){} finally { pipelineLoading.value = false; } })(),
    (async () => { videosLoading.value = true; try { const r = await getVideos(taskId); videos.value = r.data.assets || []; } catch(e){} finally { videosLoading.value = false; } })(),
    (async () => { compositeLoading.value = true; try { const r = await getComposite(taskId); composite.value = r.data; } catch(e){} finally { compositeLoading.value = false; } })(),
    (async () => { try { const r = await getFlowchart(taskId); flowchart.value = r.data; } catch(e){} })(),
  ]);
}

function getMediaUrl(filePath) {
  if (!filePath) return "";
  const parts = filePath.replace(/\\/g, "/").split("/media/");
  return parts.length > 1 ? `/media/${parts[1]}` : filePath;
}

onMounted(() => loadEligibleTasks());
</script>

<style lang="scss" scoped>
.media-page { max-width: 1200px; }
.page-hero { margin-bottom: var(--space-lg); }
.media-toolbar { margin-bottom: var(--space-lg); display: flex; align-items: center; }
.section-card { margin-bottom: var(--space-lg); }
.section-title { font-weight: 600; font-size: 14px; }

.video-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: var(--space-md);
}

.flowchart-preview {
  border-radius: var(--radius-md);
  overflow: hidden;
  cursor: pointer;
  transition: transform 0.2s;

  &:hover { transform: scale(1.01); }

  img { width: 100%; display: block; }
}

.flowchart-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 32px;
  color: var(--color-text-secondary);
}
</style>
