<!--
  统计看板页 —— Bento Grid + AI Purple 主题 + 自定义柱状图
-->
<template>
  <div class="dashboard-page">
    <div class="page-header">
      <div>
        <h1 class="page-title">📊 统计看板</h1>
        <p class="page-desc">剧本生成任务概览</p>
      </div>
      <el-button type="primary" @click="fetchStats" :loading="loading" :icon="undefined">
        🔄 刷新数据
      </el-button>
    </div>

    <!-- Bento Grid 统计卡片 -->
    <div class="stats-bento" v-loading="loading">
      <!-- 总任务数 —— 大卡片 -->
      <el-card shadow="never" class="bento-card bento-total">
        <div class="bento-inner">
          <div class="bento-icon-wrap bento-icon--primary">
            <span>📊</span>
          </div>
          <div class="bento-info">
            <div class="bento-value">{{ stats.total }}</div>
            <div class="bento-label">任务总数</div>
          </div>
        </div>
      </el-card>

      <el-card shadow="never" class="bento-card bento-pending">
        <div class="bento-inner">
          <div class="bento-icon-wrap bento-icon--info">
            <span>⏳</span>
          </div>
          <div class="bento-info">
            <div class="bento-value">{{ stats.pending }}</div>
            <div class="bento-label">待处理</div>
          </div>
        </div>
      </el-card>

      <el-card shadow="never" class="bento-card bento-running">
        <div class="bento-inner">
          <div class="bento-icon-wrap bento-icon--accent">
            <span>🔄</span>
          </div>
          <div class="bento-info">
            <div class="bento-value">{{ stats.running }}</div>
            <div class="bento-label">生成中</div>
          </div>
        </div>
      </el-card>

      <el-card shadow="never" class="bento-card bento-success">
        <div class="bento-inner">
          <div class="bento-icon-wrap bento-icon--success">
            <span>✅</span>
          </div>
          <div class="bento-info">
            <div class="bento-value">{{ stats.success }}</div>
            <div class="bento-label">已完成</div>
          </div>
        </div>
      </el-card>

      <el-card shadow="never" class="bento-card bento-failed">
        <div class="bento-inner">
          <div class="bento-icon-wrap bento-icon--danger">
            <span>❌</span>
          </div>
          <div class="bento-info">
            <div class="bento-value">{{ stats.failed }}</div>
            <div class="bento-label">失败</div>
          </div>
        </div>
      </el-card>

      <!-- 分布图卡片 —— 跨两列 -->
      <el-card shadow="never" class="bento-card bento-chart">
        <template #header>
          <span class="chart-title">📈 任务状态分布</span>
        </template>

        <el-empty v-if="stats.total === 0" description="暂无数据" />

        <div v-else class="bar-chart">
          <div v-for="item in barData" :key="item.label" class="bar-row">
            <span class="bar-label">{{ item.icon }} {{ item.label }}</span>
            <div class="bar-track">
              <div
                class="bar-fill"
                :style="{ width: item.percent + '%', background: item.color }"
              >
                <span v-if="item.percent > 10" class="bar-text">{{ item.value }}</span>
              </div>
              <span v-if="item.percent <= 10" class="bar-text-outside">{{ item.value }}</span>
            </div>
            <span class="bar-percent">{{ item.percent }}%</span>
          </div>
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from "vue";
import { getTaskStats } from "../api/task";

const stats = ref({ total: 0, pending: 0, running: 0, success: 0, failed: 0 });
const loading = ref(false);

const barData = computed(() => {
  const s = stats.value;
  const total = s.total || 1;
  return [
    { label: "待处理", icon: "⏳", value: s.pending, color: "#909399", percent: Math.round((s.pending / total) * 100) },
    { label: "生成中", icon: "🔄", value: s.running, color: "#6366F1", percent: Math.round((s.running / total) * 100) },
    { label: "已完成", icon: "✅", value: s.success, color: "#22C55E", percent: Math.round((s.success / total) * 100) },
    { label: "失败",   icon: "❌", value: s.failed,   color: "#EF4444", percent: Math.round((s.failed / total) * 100) },
  ];
});

async function fetchStats() {
  loading.value = true;
  try {
    const res = await getTaskStats();
    stats.value = res.data;
  } catch (e) {
    // 全局拦截已处理
  } finally {
    loading.value = false;
  }
}

onMounted(() => fetchStats());
</script>

<style lang="scss" scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: var(--space-lg);
}

// Bento Grid
.stats-bento {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--space-md);
}

.bento-card {
  border: 1px solid var(--color-border-light);
  box-shadow: var(--shadow-sm) !important;
  transition: all var(--transition-base);

  &:hover {
    box-shadow: var(--shadow-md) !important;
    transform: translateY(-2px);
  }

  :deep(.el-card__body) {
    padding: 20px;
  }
}

// 总任务卡 —— 跨两列
.bento-total {
  grid-column: span 2;

  .bento-value {
    font-size: 42px;
  }
}

// 图表卡 —— 跨两列
.bento-chart {
  grid-column: span 2;

  :deep(.el-card__header) {
    padding: 14px 20px;
    background: #FAFAFB;
    border-bottom: 1px solid var(--color-border-light);
  }
}

.chart-title {
  font-weight: 600;
  font-size: 14px;
}

// 卡片内部
.bento-inner {
  display: flex;
  align-items: center;
  gap: 18px;
}

.bento-icon-wrap {
  width: 52px;
  height: 52px;
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  flex-shrink: 0;

  &--primary { background: var(--color-primary-bg); }
  &--info { background: var(--color-info-bg); }
  &--accent { background: var(--color-warning-bg); }
  &--success { background: var(--color-success-bg); }
  &--danger { background: var(--color-danger-bg); }
}

.bento-info {
  display: flex;
  flex-direction: column;
}

.bento-value {
  font-size: 30px;
  font-weight: 800;
  color: var(--color-text-primary);
  line-height: 1.1;
  letter-spacing: -0.5px;
}

.bento-label {
  font-size: 13px;
  color: var(--color-text-secondary);
  margin-top: 4px;
  font-weight: 500;
}

// 柱状图
.bar-chart {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 4px 0;
}
.bar-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.bar-label {
  width: 70px;
  flex-shrink: 0;
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text-primary);
}
.bar-track {
  flex: 1;
  height: 30px;
  background: var(--color-border-light);
  border-radius: 6px;
  overflow: hidden;
  position: relative;
}
.bar-fill {
  height: 100%;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  padding-right: 10px;
  transition: width 0.8s cubic-bezier(0.4, 0, 0.2, 1);
  min-width: 2px;
}
.bar-text {
  color: #fff;
  font-size: 12px;
  font-weight: 700;
}
.bar-text-outside {
  position: absolute;
  left: calc(100% + 8px);
  top: 50%;
  transform: translateY(-50%);
  font-size: 12px;
  color: var(--color-text-secondary);
  font-weight: 600;
}
.bar-percent {
  width: 40px;
  flex-shrink: 0;
  text-align: right;
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-secondary);
}

@media (max-width: 768px) {
  .stats-bento {
    grid-template-columns: repeat(2, 1fr);
  }
  .bento-total,
  .bento-chart {
    grid-column: span 2;
  }
}
</style>
