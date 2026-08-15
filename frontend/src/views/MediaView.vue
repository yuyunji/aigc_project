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

    <!-- 任务选择 -->
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
          <el-button
            v-if="videos.filter(v=>v.status==='success').length >= 2"
            type="primary" size="small" style="margin-left:auto"
            :loading="compositing"
            @click="triggerComposite"
          >
            {{ compositing ? '⏳ 拼接中...' : '🎬 视频拼接' }}
          </el-button>
        </template>
        <div v-if="videos.length" v-loading="videosLoading" class="video-grid">
          <VideoPlayer
            v-for="v in videos"
            :key="v.id"
            :src="getMediaUrl(v)"
            :title="`分镜 ${v.scene_number} ` + (v.status === 'success' ? '✅' : v.status === 'failed' ? '❌' : '⏳')"
            :loading="false"
          />
        </div>
        <el-empty v-else-if="!videosLoading" description="视频尚未生成，点击「生成视频」按钮" />
      </el-card>

      <!-- 最终合成 -->
      <el-card shadow="never" class="section-card" v-if="composite">
        <template #header>
          <span class="section-title">🎬 最终合成视频</span>
          <el-tag v-if="composite.status === 'success'" type="success" size="small" effect="plain" style="margin-left:8px">已完成</el-tag>
          <el-tag v-else-if="composite.status === 'failed'" type="danger" size="small" effect="plain" style="margin-left:8px">失败</el-tag>
          <el-tag v-else type="warning" size="small" effect="plain" style="margin-left:8px">拼接中</el-tag>
        </template>
        <VideoPlayer v-if="composite.status === 'success'"
          :src="getMediaUrl(composite)"
          title="完整短剧"
          :loading="compositeLoading"
        />
        <el-alert v-if="composite.status === 'failed'" :title="composite.error_message" type="error" show-icon :closable="false" />
        <div v-if="composite.status === 'running'" style="text-align:center;padding:32px;color:var(--color-text-secondary)">
          ⏳ 正在拼接视频片段，添加转场效果...
        </div>
      </el-card>

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
import { getPipelineProgress, getVideos, getComposite } from "../api/media";
import apiClient from "../api/index";
import { getMediaUrl } from "../utils/media";

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
const compositing = ref(false);

// Provider 选择（从 localStorage 恢复）
const imageProvider = ref(localStorage.getItem("aigc_image_provider") || "minimax");
const videoProvider = ref(localStorage.getItem("aigc_video_provider") || "minimax-h3");

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

async function loadAll(taskId) {
  await Promise.allSettled([
    (async () => { pipelineLoading.value = true; try { const r = await getPipelineProgress(taskId); pipeline.value = r.data; } catch(e){} finally { pipelineLoading.value = false; } })(),
    (async () => { videosLoading.value = true; try { const r = await getVideos(taskId); videos.value = r.data.assets || []; } catch(e){} finally { videosLoading.value = false; } })(),
    (async () => { compositeLoading.value = true; try { const r = await getComposite(taskId); composite.value = r.data; } catch(e){} finally { compositeLoading.value = false; } })(),
  ]);
}

async function triggerComposite() {
  compositing.value = true;
  try {
    await apiClient.post(`/api/media/${selectedTaskId.value}/composite`);
    ElMessage.success("视频拼接已启动，正在添加转场效果...");
    // 轮询合成结果
    let polls = 0;
    const poller = setInterval(async () => {
      await loadAll(selectedTaskId.value);
      polls++;
      if (composite.value?.status === "success" || composite.value?.status === "failed" || polls > 60) {
        clearInterval(poller);
        if (composite.value?.status === "success") ElMessage.success("视频拼接完成！");
        compositing.value = false;
      }
    }, 5000);
  } catch (e) { compositing.value = false; }
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
