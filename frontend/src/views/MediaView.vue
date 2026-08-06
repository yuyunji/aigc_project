<!--
  媒体预览页 — MiniMax-H3 视频生成流程
  管线进度 + 分镜视频片段 + 最终合成
-->
<template>
  <div class="media-page">
    <div class="page-hero">
      <h1 class="page-title">🎬 视频生成</h1>
      <p class="page-desc">MiniMax-H3 文生视频：分镜→视频（含音频）→ FFmpeg 拼接</p>
    </div>

    <!-- 任务选择 -->
    <div class="media-toolbar">
      <el-select
        v-model="selectedTaskId"
        placeholder="请选择已完成分镜的任务..."
        @change="onTaskSelect"
        clearable
        size="large"
        :loading="tasksLoading"
        style="width: 400px"
      >
        <el-option
          v-for="t in eligibleTasks"
          :key="t.id"
          :label="t.title"
          :value="t.id"
        />
      </el-select>

      <el-button
        v-if="selectedTaskId"
        type="primary"
        style="margin-left:12px"
        @click="triggerGeneration"
        :loading="triggering"
      >
        🎥 生成视频
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
          title="完整短剧 · MiniMax-H3"
          :loading="compositeLoading"
        />
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import VideoPlayer from "../components/VideoPlayer.vue";
import MediaPipeline from "../components/MediaPipeline.vue";
import { getTaskList } from "../api/task";
import { getPipelineProgress, getVideos, getComposite } from "../api/media";
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

async function loadAll(taskId) {
  await Promise.allSettled([
    (async () => { pipelineLoading.value = true; try { const r = await getPipelineProgress(taskId); pipeline.value = r.data; } catch(e){} finally { pipelineLoading.value = false; } })(),
    (async () => { videosLoading.value = true; try { const r = await getVideos(taskId); videos.value = r.data.assets || []; } catch(e){} finally { videosLoading.value = false; } })(),
    (async () => { compositeLoading.value = true; try { const r = await getComposite(taskId); composite.value = r.data; } catch(e){} finally { compositeLoading.value = false; } })(),
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
</style>
