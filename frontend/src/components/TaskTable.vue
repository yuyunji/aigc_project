<!--
  任务状态表格 —— 状态标签 + 进度条 + 错误信息 + 操作按钮
-->
<template>
  <el-table :data="tasks" stripe v-loading="loading" empty-text="暂无任务，请先创建任务" class="task-table">
    <!-- ID -->
    <el-table-column label="任务ID" width="130">
      <template #default="{ row }">
        <el-tooltip :content="row.id" placement="top">
          <code class="task-id">{{ row.id.slice(0, 8) }}…</code>
        </el-tooltip>
      </template>
    </el-table-column>

    <!-- 名称 -->
    <el-table-column prop="title" label="任务名称" min-width="160" show-overflow-tooltip />

    <!-- 状态 -->
    <el-table-column label="状态" width="95" align="center">
      <template #default="{ row }">
        <el-tag :type="statusType(row.status)" size="small" effect="plain" round>
          {{ statusLabel(row.status) }}
        </el-tag>
      </template>
    </el-table-column>

    <!-- 进度 -->
    <el-table-column label="进度" width="190">
      <template #default="{ row }">
        <div class="progress-cell">
          <el-progress
            :percentage="row.progress"
            :status="progressStatus(row)"
            :stroke-width="8"
            :text-inside="false"
            :show-text="false"
          />
          <span
            class="progress-text"
            :class="{
              'text-success': row.status === 'success',
              'text-danger': row.status === 'failed',
            }"
          >
            {{ row.progress }}%
          </span>
        </div>
      </template>
    </el-table-column>

    <!-- 错误信息 -->
    <el-table-column label="错误信息" min-width="140" show-overflow-tooltip>
      <template #default="{ row }">
        <span v-if="row.error_message" class="error-msg">{{ row.error_message }}</span>
        <span v-else class="no-error">—</span>
      </template>
    </el-table-column>

    <!-- 来源 -->
    <el-table-column label="来源" width="65" align="center">
      <template #default="{ row }">
        {{ row.source_type === "file" ? "📄" : "📝" }}
      </template>
    </el-table-column>

    <!-- 时间 -->
    <el-table-column label="创建时间" width="165">
      <template #default="{ row }">
        {{ formatTime(row.created_at) }}
      </template>
    </el-table-column>

    <!-- 操作 -->
    <el-table-column label="操作" width="180" align="center" fixed="right">
      <template #default="{ row }">
        <div class="action-btns">
          <el-button
            v-if="row.status === 'success'"
            type="primary"
            size="small"
            link
            @click="$emit('view-results', row.id)"
          >
            查看结果 →
          </el-button>
          <el-button
            v-if="row.status !== 'running'"
            type="warning"
            size="small"
            link
            @click="$emit('regenerate', row)"
          >
            🔄 重新生成
          </el-button>
          <el-button
            v-if="row.status === 'running'"
            type="danger"
            size="small"
            link
            @click="$emit('regenerate', row)"
          >
            ⚠️ 强制重置
          </el-button>
          <el-button
            v-if="row.status !== 'running'"
            type="danger"
            size="small"
            link
            @click="$emit('delete', row)"
          >
            🗑 删除
          </el-button>
        </div>
      </template>
    </el-table-column>
  </el-table>
</template>

<script setup>
defineProps({
  tasks: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
});
defineEmits(["view-results", "regenerate", "delete"]);

function statusType(status) {
  return { pending: "info", running: "", success: "success", failed: "danger" }[status] || "info";
}
function statusLabel(status) {
  return { pending: "待处理", running: "生成中", success: "已完成", failed: "失败" }[status] || status;
}
function progressStatus(row) {
  if (row.status === "failed") return "exception";
  if (row.status === "success") return "success";
  return undefined;
}
function formatTime(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
</script>

<style lang="scss" scoped>
.task-id {
  font-size: 12px;
  color: var(--color-text-tertiary);
  font-family: var(--font-mono);
  cursor: pointer;
}

.progress-cell {
  display: flex;
  align-items: center;
  gap: 10px;

  :deep(.el-progress) {
    flex: 1;
  }
}

.progress-text {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-secondary);
  width: 34px;
  text-align: right;

  &.text-success { color: var(--color-success); }
  &.text-danger { color: var(--color-danger); }
}

.action-btns {
  display: flex !important; gap: 8px; justify-content: center; flex-wrap: wrap;
  :deep(.el-button) { margin-left: 0 !important; }
}
.error-msg {
  color: var(--color-danger);
  font-size: 12px;
}

.no-error,
.no-action {
  color: var(--color-text-tertiary);
}
</style>
