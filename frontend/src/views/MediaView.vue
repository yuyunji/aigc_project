<!--
  媒体预览页 — MiniMax-H3 视频生成流程
  管线进度 + 分镜视频片段 + 最终合成
-->
<template>
  <div class="media-page">
    <div class="page-hero">
      <h1 class="page-title">🎬 视频生成</h1>
      <p class="page-desc">分镜→视频→拼接：AI 级联媒体生成</p>
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

      <!-- 历史版本（「重新生成」时归档的上一轮产物） -->
      <el-card v-if="archiveRounds.length" shadow="never" class="section-card">
        <template #header>
          <span class="section-title">🗂 历史版本</span>
          <el-tag size="small" effect="plain" style="margin-left:8px">
            {{ archiveRounds.length }} 轮
          </el-tag>
          <span class="archive-hint">重新生成后保留的上一轮视频与配音</span>
        </template>
        <el-collapse accordion>
          <el-collapse-item
            v-for="r in archiveRounds"
            :key="r.round_no"
            :name="r.round_no"
          >
            <template #title>
              <span class="archive-round-title">
                第 {{ r.round_no }} 轮 · {{ formatTime(r.archived_at) }} · {{ r.item_count }} 个文件
                <el-tag
                  v-if="hasPending(r)"
                  size="small" type="warning" effect="plain" style="margin-left:8px"
                >云端备份中</el-tag>
                <el-tag
                  v-if="hasRescueFailed(r)"
                  size="small" type="info" effect="plain" style="margin-left:8px"
                >云端备份失败（本地可播放）</el-tag>
              </span>
            </template>
            <div class="video-grid">
              <template v-for="it in r.items">
                <div v-if="!getMediaUrl(it)" :key="it.id" class="archive-audio">
                  <span class="archive-audio-label">
                    {{ it.asset_type === "composite" ? "🎬 完整成片" : `🎞 分镜 ${it.scene_number}` }}
                    · 第 {{ r.round_no }} 轮
                  </span>
                  <span class="archive-missing">文件已不可用（本地副本与云端备份均缺失）</span>
                </div>
                <VideoPlayer
                  v-else-if="it.asset_type === 'video'"
                  :key="it.id"
                  :src="getMediaUrl(it)"
                  :title="`分镜 ${it.scene_number} · 第 ${r.round_no} 轮`"
                  :loading="false"
                />
                <VideoPlayer
                  v-else-if="it.asset_type === 'composite'"
                  :key="it.id"
                  :src="getMediaUrl(it)"
                  :title="`完整成片 · 第 ${r.round_no} 轮`"
                  :loading="false"
                />
                <div v-else :key="it.id" class="archive-audio">
                  <span class="archive-audio-label">🔊 {{ it.character_name || "配音" }}</span>
                  <audio :src="getMediaUrl(it)" controls />
                </div>
              </template>
            </div>
          </el-collapse-item>
        </el-collapse>
      </el-card>

    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { Loading } from "@element-plus/icons-vue";
import VideoPlayer from "../components/VideoPlayer.vue";
import MediaPipeline from "../components/MediaPipeline.vue";
import { getTaskList } from "../api/task";
import { getPipelineProgress, getVideos, getComposite, getMediaArchive } from "../api/media";
import apiClient from "../api/index";
import { getMediaUrl } from "../utils/media";
import { subscribeTaskEvents } from "../utils/stream";

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
const archiveRounds = ref([]);
const archiveLoading = ref(false);

/** 归档轮次里是否还有待补传 OSS 的文件 */
function hasPending(round) {
  return round.items.some((it) => it.rescue_status === "pending");
}

/** 归档轮次里是否有云端补传失败的文件（本地副本仍在，页面回退 /media 播放） */
function hasRescueFailed(round) {
  return round.items.some((it) => it.rescue_status === "failed");
}

function formatTime(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "-";
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// Provider 选择（从 localStorage 恢复）
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
      setupTaskEvents(queryId);
      router.replace({ path: "/media" });
    }
  } catch (e) { /* global handler */ }
  finally { tasksLoading.value = false; }
}

async function onTaskSelect(taskId) {
  if (!taskId) return;
  await loadAll(taskId);
  setupTaskEvents(taskId);
}

async function triggerGeneration() {
  if (!selectedTaskId.value) return;
  triggering.value = true;
  try {
    await apiClient.post(`/api/media/${selectedTaskId.value}/generate`);
    ElMessage.success("视频生成已启动，请等待...");
  } catch (e) { /* global handler */ }
  finally { triggering.value = false; }
}

function onProviderChange() {
  localStorage.setItem("aigc_video_provider", videoProvider.value);
}

async function loadAll(taskId) {
  await Promise.allSettled([
    (async () => { pipelineLoading.value = true; try { const r = await getPipelineProgress(taskId); pipeline.value = r.data; } catch(e){} finally { pipelineLoading.value = false; } })(),
    (async () => { videosLoading.value = true; try { const r = await getVideos(taskId); videos.value = r.data.assets || []; } catch(e){} finally { videosLoading.value = false; } })(),
    (async () => { compositeLoading.value = true; try { const r = await getComposite(taskId); composite.value = r.data; } catch(e){} finally { compositeLoading.value = false; } })(),
    (async () => { archiveLoading.value = true; try { const r = await getMediaArchive(taskId); archiveRounds.value = r.data.rounds || []; } catch(e){} finally { archiveLoading.value = false; } })(),
  ]);
}

async function triggerComposite() {
  compositing.value = true;
  try {
    await apiClient.post(`/api/media/${selectedTaskId.value}/composite`);
    ElMessage.success("视频拼接已启动，正在添加转场效果...");
  } catch (e) { compositing.value = false; }
}

let taskEvents = null;

function setupTaskEvents(taskId) {
  if (taskEvents) { taskEvents.close(); taskEvents = null; }
  if (!taskId) return;
  taskEvents = subscribeTaskEvents(taskId, {
    onMedia(data) {
      if (data.asset_type === "composite") {
        composite.value = {
          ...(composite.value || {}),
          id: data.asset_id, status: data.status,
          error_message: data.error_message, file_path: data.file_path, url: data.url,
        };
        if (data.status === "success") { ElMessage.success("视频拼接完成！"); compositing.value = false; }
        if (data.status === "failed") { ElMessage.error(`拼接失败: ${data.error_message || "未知错误"}`); compositing.value = false; }
      } else if (data.asset_type === "video") {
        const idx = videos.value.findIndex(v => v.id === data.asset_id);
        const item = {
          id: data.asset_id, task_id: data.task_id, asset_type: data.asset_type,
          scene_number: data.scene_number, status: data.status,
          error_message: data.error_message, file_path: data.file_path, url: data.url,
        };
        if (idx >= 0) videos.value[idx] = { ...videos.value[idx], ...item };
        else videos.value.push(item);
      }
    },
    onTask(data) {
      const t = eligibleTasks.value.find(x => x.id === data.task_id);
      if (t) { t.status = data.status; t.progress = data.progress; }
    },
  });
}

onMounted(() => loadEligibleTasks());
onUnmounted(() => {
  if (taskEvents) { taskEvents.close(); taskEvents = null; }
});
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

.archive-hint {
  margin-left: 8px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.archive-round-title {
  display: inline-flex;
  align-items: center;
  font-weight: 600;
  font-size: 13px;
}

.archive-audio {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: var(--space-md);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md, 8px);
}

.archive-audio-label { font-size: 13px; font-weight: 600; }
.archive-missing { font-size: 12px; color: var(--color-text-secondary); }


</style>
