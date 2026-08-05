<!--
  全流程进度追踪 — 8 阶段时间线
-->
<template>
  <div class="media-pipeline" v-loading="loading">
    <div class="pipeline-stages">
      <div
        v-for="stage in stages"
        :key="stage.stage"
        class="pipeline-stage"
        :class="{
          'is-active': stage.status === 'running',
          'is-done': stage.status === 'success',
          'is-error': stage.status === 'failed',
        }"
      >
        <div class="stage-marker">
          <span v-if="stage.status === 'success'">✓</span>
          <span v-else-if="stage.status === 'running'" class="spinner"></span>
          <span v-else-if="stage.status === 'failed'">✗</span>
          <span v-else>{{ stage.stage }}</span>
        </div>
        <div class="stage-info">
          <div class="stage-label">{{ stage.label }}</div>
          <div class="stage-meta" v-if="stage.assets_count > 0">
            {{ stage.assets_count }} 项
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  stages: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
});
</script>

<style lang="scss" scoped>
.pipeline-stages {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-sm);
}

.pipeline-stage {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  background: var(--color-border-light);
  border-radius: var(--radius-md);
  transition: all var(--transition-base);

  &.is-active {
    background: var(--color-primary-bg);
    border: 1px solid var(--color-primary);
  }
  &.is-done {
    background: var(--color-success-bg);
  }
  &.is-error {
    background: var(--color-danger-bg);
  }
}

.stage-marker {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 700;
  background: var(--color-border);
  color: var(--color-text-secondary);
  flex-shrink: 0;

  .is-active & { background: var(--color-primary); color: #fff; }
  .is-done & { background: var(--color-success); color: #fff; }
  .is-error & { background: var(--color-danger); color: #fff; }
}

.spinner {
  width: 14px;
  height: 14px;
  border: 2px solid #fff;
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.stage-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text-primary);
}

.stage-meta {
  font-size: 11px;
  color: var(--color-text-tertiary);
}

@keyframes spin { to { transform: rotate(360deg); } }
</style>
