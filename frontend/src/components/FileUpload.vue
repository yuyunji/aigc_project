<!--
  文件上传组件 —— 拖拽上传 txt，读取内容回填父组件
-->
<template>
  <div class="file-upload">
    <el-upload
      ref="uploadRef"
      drag
      accept=".txt"
      :auto-upload="false"
      :limit="1"
      :on-change="handleFileChange"
      :on-remove="handleRemove"
      :file-list="fileList"
    >
      <div class="upload-inner">
        <div class="upload-icon-wrap">
          <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="var(--color-primary)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="17 8 12 3 7 8"/>
            <line x1="12" y1="3" x2="12" y2="15"/>
          </svg>
        </div>
        <div class="upload-text">
          将 <em>.txt</em> 文件拖拽到此处，或点击上传
        </div>
        <div class="upload-hint">支持 UTF-8 / GBK 编码，最大 10MB</div>
      </div>
    </el-upload>

    <div v-if="uploading" class="upload-loading">
      <span class="loading-spinner"></span>
      <span>正在读取文件内容...</span>
    </div>
  </div>
</template>

<script setup>
import { ref } from "vue";
import { ElMessage } from "element-plus";
import { uploadFile } from "../api/upload";

const emit = defineEmits(["file-loaded"]);
const uploadRef = ref(null);
const uploading = ref(false);
const fileList = ref([]);

async function handleFileChange(file) {
  if (!file.name.toLowerCase().endsWith(".txt")) {
    ElMessage.warning("仅支持 .txt 文件");
    fileList.value = [];
    return;
  }
  uploading.value = true;
  try {
    const res = await uploadFile(file.raw);
    ElMessage.success({
      message: `"${res.data.filename}" 加载成功 (${res.data.encoding})`,
      duration: 2500,
    });
    emit("file-loaded", res.data.content, res.data.filename);
  } catch (e) {
    ElMessage.error("文件上传失败，请检查文件编码");
    fileList.value = [];
  } finally {
    uploading.value = false;
  }
}

function handleRemove() {
  emit("file-loaded", "", "");
}
</script>

<style lang="scss" scoped>
.file-upload {
  margin-bottom: 4px;
}

.upload-inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 8px 0;
}

.upload-icon-wrap {
  opacity: 0.7;
}

.upload-text {
  font-size: 14px;
  color: var(--color-text-secondary);

  em {
    font-style: normal;
    font-weight: 600;
    color: var(--color-primary);
  }
}

.upload-hint {
  font-size: 12px;
  color: var(--color-text-tertiary);
}

.upload-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin-top: 10px;
  padding: 10px;
  background: var(--color-primary-bg);
  border-radius: var(--radius-sm);
  color: var(--color-primary);
  font-size: 13px;
  font-weight: 500;
}

.loading-spinner {
  width: 16px;
  height: 16px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
