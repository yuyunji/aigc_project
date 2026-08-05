<!--
  媒体预览页 — 全流程进度 + 图片/视频/配音/合成一站预览
-->
<template>
  <div class="media-page">
    <div class="page-hero">
      <h1 class="page-title">🎬 媒体预览</h1>
      <p class="page-desc">查看 AI 生成的图片、视频、配音及最终合成结果</p>
    </div>

    <!-- 任务选择 -->
    <div class="media-toolbar">
      <el-select
        v-model="selectedTaskId"
        placeholder="请选择已完成的任务..."
        @change="onTaskSelect"
        clearable
        size="large"
        :loading="tasksLoading"
        style="width: 400px"
      >
        <el-option
          v-for="t in successTasks"
          :key="t.id"
          :label="t.title"
          :value="t.id"
        />
      </el-select>
    </div>

    <el-empty v-if="!selectedTaskId" description="请选择任务查看媒体资源" />

    <div v-else>
      <!-- 流程进度 -->
      <el-card shadow="never" class="section-card">
        <template #header><span class="section-title">📊 生成进度</span></template>
        <MediaPipeline
          :stages="pipeline?.stages || []"
          :loading="pipelineLoading"
        />
      </el-card>

      <!-- 分镜图片 -->
      <el-card shadow="never" class="section-card">
        <template #header>
          <span class="section-title">🖼️ 分镜图片</span>
          <el-tag v-if="images.length" size="small" effect="plain" style="margin-left:8px">
            {{ images.length }} 张
          </el-tag>
        </template>
        <ImageGallery :images="images" :loading="imagesLoading" />
      </el-card>

      <!-- 图生视频 -->
      <el-card shadow="never" class="section-card">
        <template #header>
          <span class="section-title">🎥 视频片段</span>
          <el-tag v-if="videos.length" size="small" effect="plain" style="margin-left:8px">
            {{ videos.length }} 段
          </el-tag>
        </template>
        <div v-if="videos.length" class="video-grid">
          <VideoPlayer
            v-for="v in videos"
            :key="v.id"
            :src="getMediaUrl(v.file_path)"
            :title="`分镜 ${v.scene_number}`"
            :loading="videosLoading"
          />
        </div>
        <el-empty v-else-if="!videosLoading" description="视频尚未生成" />
      </el-card>

      <!-- 角色配音 -->
      <el-card shadow="never" class="section-card">
        <template #header>
          <span class="section-title">🎙️ 角色配音</span>
          <el-tag v-if="audios.length" size="small" effect="plain" style="margin-left:8px">
            {{ audios.length }} 段
          </el-tag>
        </template>
        <AudioPlayer :audios="audios" :loading="audiosLoading" />
      </el-card>

      <!-- 最终合成 -->
      <el-card shadow="never" class="section-card" v-if="composite">
        <template #header>
          <span class="section-title">🎬 最终合成视频</span>
          <el-tag type="success" size="small" effect="plain" style="margin-left:8px">已完成</el-tag>
        </template>
        <VideoPlayer
          :src="getMediaUrl(composite.file_path)"
          title="最终合成"
          :loading="compositeLoading"
        />
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import ImageGallery from "../components/ImageGallery.vue";
import VideoPlayer from "../components/VideoPlayer.vue";
import AudioPlayer from "../components/AudioPlayer.vue";
import MediaPipeline from "../components/MediaPipeline.vue";
import { getTaskList } from "../api/task";
import {
  getPipelineProgress,
  getImages,
  getVideos,
  getAudio,
  getComposite,
} from "../api/media";

const route = useRoute();
const router = useRouter();

const selectedTaskId = ref("");
const successTasks = ref([]);
const tasksLoading = ref(false);
const pipeline = ref(null);
const pipelineLoading = ref(false);
const images = ref([]);
const imagesLoading = ref(false);
const videos = ref([]);
const videosLoading = ref(false);
const audios = ref([]);
const audiosLoading = ref(false);
const composite = ref(null);
const compositeLoading = ref(false);

async function loadSuccessTasks() {
  tasksLoading.value = true;
  try {
    const res = await getTaskList();
    successTasks.value = (res.data.tasks || []).filter(
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

async function loadAll(taskId) {
  await Promise.allSettled([
    (async () => { pipelineLoading.value = true; try { const r = await getPipelineProgress(taskId); pipeline.value = r.data; } catch(e){} finally { pipelineLoading.value = false; } })(),
    (async () => { imagesLoading.value = true; try { const r = await getImages(taskId); images.value = r.data.assets || []; } catch(e){} finally { imagesLoading.value = false; } })(),
    (async () => { videosLoading.value = true; try { const r = await getVideos(taskId); videos.value = r.data.assets || []; } catch(e){} finally { videosLoading.value = false; } })(),
    (async () => { audiosLoading.value = true; try { const r = await getAudio(taskId); audios.value = r.data.assets || []; } catch(e){} finally { audiosLoading.value = false; } })(),
    (async () => { compositeLoading.value = true; try { const r = await getComposite(taskId); composite.value = r.data; } catch(e){} finally { compositeLoading.value = false; } })(),
  ]);
}

function getMediaUrl(filePath) {
  if (!filePath) return "";
  const parts = filePath.replace(/\\/g, "/").split("/media/");
  return parts.length > 1 ? `/media/${parts[1]}` : filePath;
}

onMounted(() => loadSuccessTasks());
</script>

<style lang="scss" scoped>
.media-page { max-width: 1200px; }
.page-hero { margin-bottom: var(--space-lg); }
.media-toolbar { margin-bottom: var(--space-lg); }
.section-card { margin-bottom: var(--space-lg); }

.section-title {
  font-weight: 600;
  font-size: 14px;
}

.video-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: var(--space-md);
}
</style>
