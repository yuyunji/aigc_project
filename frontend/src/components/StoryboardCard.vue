<!--
  导演镜头脚本卡片流

  每张卡片 = 一个完整的导演镜头脚本（原文渲染，不重新拼装，保证与生成结果一字不差）。
  点「✏️ 编辑」整块进入可编辑状态，保存后由服务端按同一套解析逻辑重算
  景别/角度/运镜/情绪/构图/转场等派生字段，视频生成链路立即生效。

  正文下方另有「完整生成 Prompt」折叠面板，展开是与正文完全一致的脚本原文。

  另有全局风格卡（片头风格首行，注入所有 prompt）独立置顶。

  两代数据兼容：raw_script 为空时回落到 description（旧数据是重新拼装的 Markdown）。
-->
<template>
  <div class="storyboard-cards" v-loading="loading">
    <el-empty v-if="!loading && scenes.length === 0" description="分镜脚本尚未生成" />

    <template v-else>
      <!-- ── 全局风格（片头首行）── -->
      <el-card v-if="globalPrefix || globalEditing" shadow="never" class="sb-card style-card">
        <div class="sb-head">
          <span class="sb-badge style-badge">🎨</span>
          <h3 class="sb-title">全局风格</h3>
          <span class="sb-sep">·</span>
          <span class="sb-sub">注入所有图片 / 视频提示词首段</span>
          <div class="sb-head-actions">
            <el-button v-if="!globalEditing" size="small" plain @click="startEditGlobal">✏️ 编辑</el-button>
            <template v-else>
              <el-button size="small" type="primary" :loading="saving" @click="saveGlobal">保存</el-button>
              <el-button size="small" plain @click="cancelEditGlobal">取消</el-button>
            </template>
          </div>
        </div>
        <div v-if="!globalEditing" class="sb-body sb-script">{{ globalPrefix }}</div>
        <el-input
          v-else
          v-model="globalDraft"
          type="textarea"
          :autosize="{ minRows: 3, maxRows: 12 }"
          class="sb-editor"
          placeholder="画风 + 世界观 + 色调 + 材质质感 + 氛围，并附主要角色外观设定"
        />
      </el-card>

      <!-- ── 逐镜卡片 ── -->
      <el-card
        v-for="scene in scenes"
        :key="scene.id"
        shadow="never"
        class="sb-card"
        :class="{ 'is-editing': isEditing(scene) }"
      >
        <div class="sb-head">
          <span class="sb-badge">{{ String(scene.scene_number).padStart(2, "0") }}</span>
          <h3 class="sb-title">{{ sceneTitle(scene) }}</h3>
          <el-tag v-if="scene.duration_seconds" size="small" effect="plain" round class="sb-duration">
            {{ scene.duration_seconds }}s
          </el-tag>
          <div class="sb-head-actions">
            <el-button v-if="!isEditing(scene)" size="small" plain @click="startEdit(scene)">✏️ 编辑</el-button>
            <template v-else>
              <el-button size="small" type="primary" :loading="saving === scene.scene_number" @click="save(scene)">保存</el-button>
              <el-button size="small" plain @click="cancelEdit(scene)">取消</el-button>
            </template>
          </div>
        </div>

        <!-- 脚本正文：原文直出 -->
        <div v-if="!isEditing(scene)" class="sb-body sb-script">{{ scriptBody(scene) }}</div>
        <el-input
          v-else
          v-model="drafts[scene.scene_number]"
          type="textarea"
          :autosize="{ minRows: 10, maxRows: 40 }"
          class="sb-editor"
          placeholder="镜头NN：标题（时长：N秒）…"
        />

        <!-- 完整生成 Prompt：与正文同一段脚本原文，仅在正文限高内滚时提供一处整段可读的位置 -->
        <el-collapse v-if="!isEditing(scene)" class="prompt-collapse">
          <el-collapse-item title="📝 完整生成 Prompt">
            <p class="prompt-text">{{ scriptBody(scene) }}</p>
          </el-collapse-item>
        </el-collapse>

        <!-- ── 操作区 ── -->
        <div v-if="!isEditing(scene)" class="sb-actions">
          <div class="actions-row">
            <el-button
              size="small"
              :type="videoState(scene.scene_number) === 'success' ? 'warning' : 'success'"
              plain
              :loading="videoState(scene.scene_number) === 'running'"
              :disabled="videoState(scene.scene_number) === 'running'"
              @click="$emit('generate-video', scene.scene_number)"
            >
              {{ videoState(scene.scene_number) === 'success' ? '🔄 重新生成视频' : '🎥 生成视频' }}
            </el-button>
            <el-button
              v-if="mediaState(scene.scene_number, 'any') === 'failed'"
              size="small" type="warning" plain
              @click="$emit('retry', scene.scene_number)"
            >🔄 重试</el-button>
          </div>

          <div class="status-row" v-if="getSceneMedia(scene.scene_number).length">
            <el-tag
              v-for="m in getSceneMedia(scene.scene_number)"
              :key="m.id" size="small" :type="statusType(m)" effect="plain" round class="status-tag"
            >🎥 {{ statusLabel(m) }}</el-tag>
          </div>

          <div v-if="getSceneMedia(scene.scene_number).some(m => m.status === 'failed' && m.error_message)" class="error-row">
            <el-alert
              v-for="m in getSceneMedia(scene.scene_number).filter(x => x.status === 'failed' && x.error_message)"
              :key="m.id" :title="m.error_message" type="error" :closable="false" show-icon class="error-alert"
            />
          </div>
        </div>
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { ref, reactive } from "vue";
import { ElMessage } from "element-plus";
import { updateStoryboard, updateGlobalPrefix } from "../api/task";

const props = defineProps({
  scenes: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  taskId: { type: String, default: "" },
  mediaAssets: { type: Array, default: () => [] },
  /** 任务级全局风格前缀（片头风格首行） */
  globalPrefix: { type: String, default: "" },
});

const emit = defineEmits(["generate-video", "retry", "saved"]);

// 编辑态：场景号 -> 草稿文本（同时充当「是否正在编辑」的判定）
const drafts = reactive({});
const saving = ref(null);        // 正在保存的场景号；"global" 表示全局风格卡
const globalEditing = ref(false);
const globalDraft = ref("");

function isEditing(scene) {
  return Object.prototype.hasOwnProperty.call(drafts, scene.scene_number);
}

/** 可编辑原文：优先 raw_script；旧数据回落到 description */
function editableText(scene) {
  return scene.raw_script || scene.description || "";
}

/**
 * 卡片正文 = 去掉标题行的脚本原文。
 * 标题（含时长）已经在卡头单独展示，正文里不再重复。
 */
function scriptBody(scene) {
  const text = editableText(scene);
  return text.replace(/^[ \t#>*]*镜头\s*\d+\s*[：:][^\n]*(\n|$)/, "").trim() || text;
}

function sceneTitle(scene) {
  const t = (scene.scene_title || "").trim();
  return t || `镜头 ${scene.scene_number}`;
}

function startEdit(scene) {
  drafts[scene.scene_number] = editableText(scene);
}

function cancelEdit(scene) {
  delete drafts[scene.scene_number];
}

function startEditGlobal() {
  globalDraft.value = props.globalPrefix || "";
  globalEditing.value = true;
}

function cancelEditGlobal() {
  globalEditing.value = false;
  globalDraft.value = "";
}

async function saveGlobal() {
  const text = globalDraft.value.trim();
  if (!text) { ElMessage.warning("全局风格不能为空"); return; }
  saving.value = "global";
  try {
    await updateGlobalPrefix(props.taskId, text);
    ElMessage.success("全局风格已保存，将用于后续视频 / 图片生成");
    globalEditing.value = false;
    emit("saved");
  } catch (e) { /* 全局拦截器已提示 */ }
  finally { saving.value = null; }
}

async function save(scene) {
  const text = (drafts[scene.scene_number] || "").trim();
  if (!text) { ElMessage.warning("脚本内容不能为空"); return; }

  saving.value = scene.scene_number;
  try {
    const res = await updateStoryboard(props.taskId, scene.scene_number, text);
    const updated = res.data || {};
    delete drafts[scene.scene_number];

    // 机器字段被服务端归一化（组合值/括号注解/非白名单档位）时明确告知，
    // 否则用户会以为自己的改动被吞了
    const changed = ["shot_size", "camera_angle", "camera_movement", "mood", "composition", "transition"]
      .filter((k) => scene[k] && updated[k] && scene[k] !== updated[k]);
    if (changed.length) {
      ElMessage.success(
        `已保存；${changed.map((k) => `${scene[k]} → ${updated[k]}`).join("、")} 已按模板白名单归一化`
      );
    } else {
      ElMessage.success("已保存");
    }
    // 让父组件重新拉取，拿到重解析后的字段
    emit("saved");
  } catch (e) { /* 全局拦截器已提示 */ }
  finally { saving.value = null; }
}

// ── 视频状态 ──

function getSceneMedia(sceneNumber) {
  return (props.mediaAssets || []).filter((m) => m.scene_number === sceneNumber);
}

function videoState(sceneNumber) { return mediaState(sceneNumber, "video"); }

function mediaState(sceneNumber, type) {
  const assets = getSceneMedia(sceneNumber);
  if (type === "any" && assets.some((a) => a.status === "failed")) return "failed";
  const matching = assets.filter((a) => a.asset_type === "video");
  if (matching.some((a) => a.status === "success")) return "success";
  if (matching.some((a) => a.status === "running")) return "running";
  return "idle";
}

function statusType(m) {
  return m.status === "success" ? "success" : m.status === "failed" ? "danger" : "warning";
}
function statusLabel(m) {
  return m.status === "success" ? "已完成" : m.status === "failed" ? "失败" : "生成中";
}
</script>

<style lang="scss" scoped>
.storyboard-cards {
  display: flex;
  flex-direction: column;
  gap: var(--space-md, 16px);
}

.sb-card {
  border-radius: var(--radius-lg, 12px);
  transition: box-shadow 0.2s, border-color 0.2s;

  &:hover { box-shadow: 0 4px 24px rgba(99, 102, 241, 0.12); }
  &.is-editing { border-color: var(--color-primary); box-shadow: 0 0 0 3px var(--color-primary-bg); }

  :deep(.el-card__body) { padding: 18px 22px; }
}

.style-card {
  background: var(--color-bg-secondary);
  :deep(.el-card__body) { padding: 14px 22px; }
}

.sb-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.sb-badge {
  flex: 0 0 auto;
  min-width: 34px;
  height: 34px;
  padding: 0 8px;
  border-radius: 9px;
  background: var(--color-primary);
  color: #fff;
  font-size: 14px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;

  &.style-badge { font-size: 16px; }
}

.sb-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--color-text-primary);
  margin: 0;
}

.sb-sep { color: var(--color-text-tertiary); }
.sb-sub { font-size: 12px; color: var(--color-text-tertiary); }
.sb-head-actions { margin-left: auto; display: flex; gap: 8px; }

/* 脚本正文：保留原文换行与缩进 */
.sb-body {
  font-size: 13px;
  line-height: 1.85;
  color: var(--color-text-primary);
}

/* 不限高、不内滚：整段脚本直接铺开，长镜头也一次读完 */
.sb-script {
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--color-bg-secondary);
  border-left: 3px solid var(--color-primary-light);
  border-radius: 0 8px 8px 0;
  padding: 12px 16px;
}

.sb-editor {
  :deep(.el-textarea__inner) {
    font-family: inherit;
    font-size: 13px;
    line-height: 1.8;
    white-space: pre-wrap;
  }
}

.prompt-collapse {
  margin-top: 12px;
  :deep(.el-collapse-item__header) { font-size: 12px; color: var(--color-text-tertiary); border-bottom: none; }
  :deep(.el-collapse-item__wrap) { border-bottom: none; }
}

.prompt-text {
  font-size: 12px;
  line-height: 1.6;
  color: var(--color-text-secondary);
  background: var(--color-bg-secondary);
  padding: 10px;
  border-radius: 6px;
  word-break: break-all;
  white-space: pre-wrap;
}

.sb-actions { margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--color-border-light); }
.actions-row { display: flex; gap: 8px; flex-wrap: wrap; }
.status-row { margin-top: 8px; display: flex; gap: 6px; flex-wrap: wrap; }
.status-tag { font-size: 11px; }
.error-row { margin-top: 8px; }
.error-alert {
  margin-top: 4px;
  :deep(.el-alert__title) { font-size: 12px; }
}
</style>
