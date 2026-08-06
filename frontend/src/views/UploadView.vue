<!--
  上传输入页 —— 卡片布局 + AI Purple 主题
-->
<template>
  <div class="upload-page">
    <div class="page-hero">
      <h1 class="page-title">📝 创建新任务</h1>
      <p class="page-desc">粘贴原著文本或上传 txt 文件，启动 AI 剧本生成链路</p>
    </div>

    <div class="upload-layout">
      <!-- 主表单卡片 -->
      <el-card class="upload-card" shadow="never">
        <el-form label-position="top" class="upload-form">
          <!-- 任务名称 -->
          <el-form-item label="任务名称" required>
            <el-input
              v-model="taskName"
              placeholder="例如：斗破苍穹 · 第一章改编"
              maxlength="100"
              show-word-limit
              size="large"
            />
          </el-form-item>

          <!-- 文件上传 -->
          <el-form-item label="📎 上传原著文件（可选）">
            <FileUpload @file-loaded="onFileLoaded" />
          </el-form-item>

          <!-- 文本输入 -->
          <el-form-item label="📄 原著文本内容" required>
            <TextInput
              v-model="sourceText"
              :rows="14"
            />
          </el-form-item>

          <!-- 操作按钮 -->
          <div class="form-actions">
            <el-button
              type="primary"
              size="large"
              :loading="submitting"
              :disabled="!canSubmit"
              class="submit-btn"
              @click="handleSubmit"
            >
              <span v-if="!submitting">🚀 提交生成任务</span>
              <span v-else>正在创建...</span>
            </el-button>
            <el-button size="large" @click="handleClear" :disabled="submitting">
              清空重填
            </el-button>
          </div>
        </el-form>
      </el-card>

      <!-- 侧边栏提示 -->
      <div class="upload-sidebar">
        <el-card shadow="never" class="info-card">
          <template #header>
            <span class="info-header">⚡ 处理链路</span>
          </template>
          <div class="pipeline-steps">
            <div class="pipeline-step">
              <span class="step-dot"></span>
              <span>文本分片预处理</span>
            </div>
            <div class="pipeline-arrow">↓</div>
            <div class="pipeline-step">
              <span class="step-dot"></span>
              <span>生成剧本大纲</span>
            </div>
            <div class="pipeline-arrow">↓</div>
            <div class="pipeline-step">
              <span class="step-dot"></span>
              <span>生成人物角色设定</span>
            </div>
            <div class="pipeline-arrow">↓</div>
            <div class="pipeline-step">
              <span class="step-dot"></span>
              <span>生成分镜脚本 JSON</span>
            </div>
            <div class="pipeline-arrow">↓</div>
            <div class="pipeline-step highlight-step">
              <span class="step-dot highlight-dot"></span>
              <span>MiniMax-H3 文生视频</span>
            </div>
            <div class="pipeline-arrow">↓</div>
            <div class="pipeline-step">
              <span class="step-dot"></span>
              <span>FFmpeg 视频拼接</span>
            </div>
          </div>
        </el-card>

        <el-alert
          title="Demo 说明"
          type="info"
          :closable="false"
          show-icon
          class="info-alert"
        >
          <template #default>
            <p style="margin-top:4px;font-size:12px;line-height:1.6">
              长文本预处理仅做简单分片，商业项目实现七层提取与节点重构算法。
            </p>
          </template>
        </el-alert>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import TextInput from "../components/TextInput.vue";
import FileUpload from "../components/FileUpload.vue";
import { createTask } from "../api/task";

const router = useRouter();
const taskName = ref("");
const sourceText = ref("");
const submitting = ref(false);
const isFromFile = ref(false);

const canSubmit = computed(
  () => taskName.value.trim().length > 0 && sourceText.value.trim().length >= 10
);

function onFileLoaded(content, filename) {
  if (content) {
    sourceText.value = content;
    isFromFile.value = true;
    if (!taskName.value) {
      taskName.value = filename.replace(/\.txt$/i, "") + " · 改编";
    }
  } else {
    // 用户移除了上传的文件
    isFromFile.value = false;
  }
}

async function handleSubmit() {
  if (!canSubmit.value) return;
  submitting.value = true;
  try {
    const res = await createTask({
      title: taskName.value.trim(),
      content: sourceText.value.trim(),
      source_type: isFromFile.value ? "file" : "text",
    });
    ElMessage.success({
      message: `"${res.data.title}" 创建成功，已开始处理`,
      duration: 3000,
    });
    router.push("/tasks");
  } catch (e) {
    // 全局拦截已处理
  } finally {
    submitting.value = false;
  }
}

function handleClear() {
  taskName.value = "";
  sourceText.value = "";
  isFromFile.value = false;
}
</script>

<style lang="scss" scoped>
.upload-page {
  max-width: 1100px;
}

.page-hero {
  margin-bottom: var(--space-lg);
}

// 双栏布局
.upload-layout {
  display: grid;
  grid-template-columns: 1fr 260px;
  gap: var(--space-lg);
  align-items: start;
}

.upload-card {
  border: 1px solid var(--color-border-light);
  box-shadow: var(--shadow-md) !important;

  :deep(.el-card__body) {
    padding: var(--space-xl);
  }
}

.upload-form {
  :deep(.el-form-item__label) {
    font-weight: 600;
    font-size: 13px;
    color: var(--color-text-primary);
  }
}

.form-actions {
  display: flex;
  gap: var(--space-md);
  margin-top: var(--space-lg);
  padding-top: var(--space-lg);
  border-top: 1px solid var(--color-border-light);
}

.submit-btn {
  min-width: 180px;
}

// 侧边栏
.upload-sidebar {
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
  position: sticky;
  top: calc(var(--header-height) + var(--space-lg));
}

.info-card {
  :deep(.el-card__header) {
    padding: 12px 16px;
    background: var(--color-primary-bg);
    border-bottom: 1px solid var(--color-border-light);
  }
}

.info-header {
  font-weight: 600;
  font-size: 13px;
  color: var(--color-primary-dark);
}

.pipeline-steps {
  padding: 4px 0;
}

.pipeline-step {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 0;
  font-size: 13px;
  color: var(--color-text-secondary);
}

.step-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-primary);
  flex-shrink: 0;
  box-shadow: 0 0 6px rgba(99, 102, 241, 0.4);
}

.highlight-step {
  background: var(--color-primary-bg);
  border-radius: var(--radius-sm);
  padding: 6px 10px;
}

.highlight-dot {
  background: var(--color-success) !important;
  box-shadow: 0 0 8px rgba(34, 197, 94, 0.5) !important;
}

.pipeline-arrow {
  text-align: center;
  color: var(--color-border);
  font-size: 12px;
  padding-left: 18px;
}

.info-alert {
  border-radius: var(--radius-md);
}

@media (max-width: 768px) {
  .upload-layout {
    grid-template-columns: 1fr;
  }

  .upload-sidebar {
    position: static;
  }
}
</style>
