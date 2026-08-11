<!--
  资产拆解 Tab — 角色 / 场景 / 道具三列卡片 + 图片生成 / 上传
-->
<template>
  <div class="asset-tab" v-loading="loading">
    <!-- 顶部操作栏 -->
    <div class="asset-toolbar">
      <el-button type="primary" :loading="extracting" @click="$emit('extract')">
        🤖 AI 自动提取
      </el-button>
      <el-button plain @click="openAdd()">
        ➕ 手动添加
      </el-button>
      <span class="toolbar-hint" v-if="assets.length">
        共 {{ assets.length }} 个资产（角色 {{ characterCount }} · 场景 {{ sceneCount }} · 道具 {{ propCount }}）
      </span>
    </div>

    <el-empty v-if="!loading && assets.length === 0" description="暂无资产，请点击「AI 自动提取」或「手动添加」" />

    <!-- 三列布局 -->
    <div v-else class="asset-columns">
      <div class="asset-column">
        <div class="column-header">👤 角色 ({{ characterCount }})</div>
        <AssetCard
          v-for="a in characterAssets" :key="a.id" :asset="a"
          @generate="$emit('generate-image', a.id)"
          @upload="(f) => $emit('upload-image', a.id, f)"
          @edit="openEdit(a)"
          @delete="$emit('delete-asset', a.id)"
        />
      </div>
      <div class="asset-column">
        <div class="column-header">📍 场景 ({{ sceneCount }})</div>
        <AssetCard
          v-for="a in sceneAssets" :key="a.id" :asset="a"
          @generate="$emit('generate-image', a.id)"
          @upload="(f) => $emit('upload-image', a.id, f)"
          @edit="openEdit(a)"
          @delete="$emit('delete-asset', a.id)"
        />
      </div>
      <div class="asset-column">
        <div class="column-header">🔧 道具 ({{ propCount }})</div>
        <AssetCard
          v-for="a in propAssets" :key="a.id" :asset="a"
          @generate="$emit('generate-image', a.id)"
          @upload="(f) => $emit('upload-image', a.id, f)"
          @edit="openEdit(a)"
          @delete="$emit('delete-asset', a.id)"
        />
      </div>
    </div>

    <!-- 手动添加 / 编辑对话框 -->
    <el-dialog v-model="showDialog" :title="editing ? '编辑资产' : '手动添加资产'" width="520px">
      <el-form label-position="top">
        <el-form-item label="分类">
          <el-select v-model="form.category" style="width:100%">
            <el-option label="👤 角色" value="character" />
            <el-option label="📍 场景" value="scene" />
            <el-option label="🔧 道具" value="prop" />
          </el-select>
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="form.name" maxlength="200" placeholder="资产名称" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="4" maxlength="5000" placeholder="外观、材质、用途等描述" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showDialog = false">取消</el-button>
        <el-button type="primary" :disabled="!form.name || !form.category" @click="submitForm">
          {{ editing ? '保存' : '添加' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, watch } from "vue";
import AssetCard from "./AssetCard.vue";

const props = defineProps({
  assets: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  extracting: { type: Boolean, default: false },
});

const emit = defineEmits([
  "extract", "generate-image", "upload-image", "delete-asset",
  "create-asset", "update-asset",
]);

const showDialog = ref(false);
const editing = ref(false);
const editId = ref(null);
const form = ref({ category: "character", name: "", description: "" });

const characterCount = computed(() => props.assets.filter((a) => a.category === "character").length);
const sceneCount = computed(() => props.assets.filter((a) => a.category === "scene").length);
const propCount = computed(() => props.assets.filter((a) => a.category === "prop").length);
const characterAssets = computed(() => props.assets.filter((a) => a.category === "character"));
const sceneAssets = computed(() => props.assets.filter((a) => a.category === "scene"));
const propAssets = computed(() => props.assets.filter((a) => a.category === "prop"));

function openAdd() {
  editing.value = false;
  editId.value = null;
  form.value = { category: "character", name: "", description: "" };
  showDialog.value = true;
}

function openEdit(a) {
  editing.value = true;
  editId.value = a.id;
  form.value = { category: a.category, name: a.name, description: a.description || "" };
  showDialog.value = true;
}

function submitForm() {
  if (editing.value) {
    emit("update-asset", editId.value, { ...form.value });
  } else {
    emit("create-asset", { ...form.value });
  }
  showDialog.value = false;
}
</script>

<style lang="scss" scoped>
.asset-toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: var(--space-lg); }
.toolbar-hint { font-size: 13px; color: var(--color-text-tertiary); margin-left: 8px; }
.asset-columns { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--space-md); }
.column-header {
  font-size: 15px; font-weight: 700; color: var(--color-text-primary);
  padding: 10px 0; border-bottom: 2px solid var(--color-primary); margin-bottom: var(--space-md);
}
@media (max-width: 900px) {
  .asset-columns { grid-template-columns: 1fr; }
}
</style>
