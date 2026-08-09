<!--
  任务管理列表页 —— 表格 + 自动轮询 + AI Purple 主题
-->
<template>
  <div class="task-page">
    <div class="page-header">
      <div>
        <h1 class="page-title">📋 任务管理</h1>
        <p class="page-desc">查看所有剧本生成任务的状态与进度</p>
      </div>
      <div class="header-actions">
        <el-button
          :type="polling ? 'warning' : 'primary'"
          plain
          @click="togglePolling"
          size="default"
        >
          {{ polling ? "⏸ 停止轮询" : "▶ 自动刷新" }}
        </el-button>
        <el-button @click="fetchTasks" :loading="loading" size="default">
          🔄 手动刷新
        </el-button>
        <el-button type="primary" @click="$router.push('/')" size="default">
          ＋ 新建任务
        </el-button>
      </div>
    </div>

    <!-- 活跃任务指示器 -->
    <div v-if="activeCount > 0" class="active-bar">
      <span class="active-dot"></span>
      {{ activeCount }} 个任务正在处理中
      <span v-if="polling" class="polling-tag">自动刷新中</span>
    </div>

    <el-card shadow="never" class="table-card">
      <TaskTable
        :tasks="tasks"
        :loading="loading"
        @view-results="goToResults"
        @regenerate="onRegenerate"
      />
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import TaskTable from "../components/TaskTable.vue";
import { getTaskList, regenerateTask } from "../api/task";

const router = useRouter();
const tasks = ref([]);
const loading = ref(false);
const polling = ref(false);
let pollingTimer = null;

const activeCount = computed(
  () => tasks.value.filter((t) => t.status === "pending" || t.status === "running").length
);

async function fetchTasks() {
  loading.value = true;
  try {
    const res = await getTaskList();
    tasks.value = res.data.tasks || [];
  } catch (e) {
    // 全局拦截已处理
  } finally {
    loading.value = false;
  }
}

function hasActiveTasks() {
  return tasks.value.some((t) => t.status === "pending" || t.status === "running");
}

function startPolling() {
  polling.value = true;
  pollingTimer = setInterval(async () => {
    await fetchTasks();
    if (!hasActiveTasks() && polling.value) {
      stopPolling();
    }
  }, 3000);
}

function stopPolling() {
  polling.value = false;
  if (pollingTimer) {
    clearInterval(pollingTimer);
    pollingTimer = null;
  }
}

function togglePolling() {
  if (polling.value) {
    stopPolling();
  } else {
    fetchTasks().then(() => {
      if (hasActiveTasks()) startPolling();
    });
  }
}

function goToResults(taskId) {
  router.push({ path: "/results", query: { taskId } });
}

async function onRegenerate(task) {
  try {
    await ElMessageBox.confirm(
      `将删除「${task.title}」的所有生成结果并重新开始，确定吗？`,
      "确认重新生成",
      { confirmButtonText: "确定", cancelButtonText: "取消", type: "warning" }
    );
    await regenerateTask(task.id);
    ElMessage.success("已重新入队，请等待处理");
    await fetchTasks();
    startPolling();
  } catch (e) {
    if (e !== "cancel") ElMessage.error("重新生成失败");
  }
}

onMounted(() => {
  fetchTasks().then(() => {
    if (hasActiveTasks()) startPolling();
  });
});

onUnmounted(() => stopPolling());
</script>

<style lang="scss" scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: var(--space-md);
}

.header-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

// 活跃任务栏
.active-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  margin-bottom: var(--space-md);
  background: var(--color-primary-bg);
  border-radius: var(--radius-md);
  font-size: 13px;
  font-weight: 500;
  color: var(--color-primary-dark);
}

.active-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-primary);
  animation: pulse-dot 1.5s ease-in-out infinite;
}

@keyframes pulse-dot {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.5; transform: scale(1.3); }
}

.polling-tag {
  margin-left: auto;
  font-size: 11px;
  padding: 2px 10px;
  border-radius: 20px;
  background: var(--color-primary);
  color: #fff;
  font-weight: 600;
}

.table-card {
  :deep(.el-card__body) {
    padding: 0;
  }
}
</style>
