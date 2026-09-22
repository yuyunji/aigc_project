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

        <!-- 脚本正文：原文直出，@资产名 高亮（命中资产图的名字才高亮，见 mentionRe） -->
        <div v-if="!isEditing(scene)" class="sb-body sb-script">
          <span
            v-for="(seg, i) in highlightSegments(scriptBody(scene))"
            :key="i"
            :class="{ 'at-mention': seg.at }"
          >{{ seg.text }}</span>
        </div>
        <el-input
          v-else
          v-model="drafts[scene.scene_number]"
          type="textarea"
          :autosize="{ minRows: 10, maxRows: 40 }"
          class="sb-editor"
          placeholder="镜头NN：标题（时长：N秒）…（「人物角色核心提示词：」行可用 @角色名 引用资产图）"
        />

        <!-- 完整生成 Prompt：与正文同一段脚本原文，仅在正文限高内滚时提供一处整段可读的位置 -->
        <el-collapse v-if="!isEditing(scene)" class="prompt-collapse">
          <el-collapse-item title="📝 完整生成 Prompt">
            <p class="prompt-text">
              <span
                v-for="(seg, i) in highlightSegments(scriptBody(scene))"
                :key="i"
                :class="{ 'at-mention': seg.at }"
              >{{ seg.text }}</span>
            </p>
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

            <span class="actions-sep"></span>

            <el-select
              :model-value="directorStyle(scene.scene_number)"
              size="small"
              class="director-select"
              @change="onDirectorStyleChange(scene.scene_number, $event)"
            >
              <el-option
                v-for="s in DIRECTOR_STYLES" :key="s.value" :label="s.label" :value="s.value"
              />
            </el-select>
            <el-button
              size="small"
              :type="directorState(scene.scene_number) === 'success' ? 'warning' : 'primary'"
              plain
              :loading="directorState(scene.scene_number) === 'running'"
              :disabled="directorState(scene.scene_number) === 'running'"
              @click="$emit('generate-director', scene.scene_number, directorStyle(scene.scene_number))"
            >
              {{ directorState(scene.scene_number) === 'success' ? '🔄 重新生成导演镜头' : '🎬 生成导演镜头' }}
            </el-button>
          </div>

          <div class="status-row" v-if="getSceneMedia(scene.scene_number).length">
            <el-tag
              v-for="m in getSceneMedia(scene.scene_number)"
              :key="m.id" size="small" :type="statusType(m)" effect="plain" round class="status-tag"
            >{{ typeIcon(m) }} {{ statusLabel(m) }}</el-tag>
          </div>

          <div v-if="directorByScene[scene.scene_number]" class="director-preview">
            <img
              class="director-thumb"
              :src="directorUrl(directorByScene[scene.scene_number])"
              alt="6 宫格分镜导演图"
              title="点击查看大图"
              @click="openDirectorImage(directorByScene[scene.scene_number])"
            />
            <span class="director-hint">6 宫格分镜导演图 · 点击查看大图（可滚轮缩放）</span>
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

    <!--
      大图查看器：全屏浮层，滚轮缩放 / 旋转 / 拖拽。
      不用 window.open —— 那是把原图交给浏览器，要么新开标签要么直接下载，
      在应用里看不出「查看」的效果。teleported 是为了躲开 el-card 的 overflow:hidden。
    -->
    <el-image-viewer
      v-if="viewerUrl"
      :url-list="[viewerUrl]"
      :z-index="3000"
      teleported
      @close="viewerUrl = ''"
    />
  </div>
</template>

<script setup>
import { computed, ref, reactive } from "vue";
import { ElMessage } from "element-plus";
import { updateStoryboard, updateGlobalPrefix } from "../api/task";
import { getMediaUrl } from "../utils/media";

/** 分镜导演图风格选项（value 与后端 DIRECTOR_STYLE_EN 的 key 一一对应） */
const DIRECTOR_STYLES = [
  { value: "guoman3d", label: "国漫3D" },
  { value: "riman2d", label: "日漫2D" },
  { value: "zhenren", label: "真人写实" },
];
const DIRECTOR_STYLE_KEY = "aigc_director_style";

const props = defineProps({
  scenes: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  taskId: { type: String, default: "" },
  mediaAssets: { type: Array, default: () => [] },
  /** 任务资产（角色/场景/道具），用于把脚本里的 @资产名 高亮 */
  assets: { type: Array, default: () => [] },
  /** 任务级全局风格前缀（片头风格首行） */
  globalPrefix: { type: String, default: "" },
});

const emit = defineEmits(["generate-video", "generate-director", "retry", "saved"]);

// 编辑态：场景号 -> 草稿文本（键存在即表示该卡处于编辑态）
const drafts = reactive({});

// 导演图风格：场景号 -> 当前选择（懒初始化读 localStorage）
const directorStyles = reactive({});

function loadDirectorStyle() {
  const saved = localStorage.getItem(DIRECTOR_STYLE_KEY);
  // 校验枚举：存了非法值会让 el-select 显示空白
  return DIRECTOR_STYLES.some((s) => s.value === saved) ? saved : "guoman3d";
}

function directorStyle(sceneNumber) {
  if (directorStyles[sceneNumber] === undefined) {
    directorStyles[sceneNumber] = loadDirectorStyle();
  }
  return directorStyles[sceneNumber];
}

function onDirectorStyleChange(sceneNumber, value) {
  directorStyles[sceneNumber] = value;
  localStorage.setItem(DIRECTOR_STYLE_KEY, value);
}
const saving = ref(null);        // 正在保存的场景号；"global" 表示全局风格卡
const globalEditing = ref(false);
const globalDraft = ref("");

/**
 * 必须「读一次属性」来判定：Vue 3 的 reactive 代理没有 getOwnPropertyDescriptor 拦截器，
 * Object.prototype.hasOwnProperty 走的是 [[GetOwnProperty]]，不经过 get 代理、不收集依赖，
 * 于是 drafts 变了模板也不会重渲染——点「编辑」界面毫无反应，按钮像失灵。
 */
function isEditing(scene) {
  return drafts[scene.scene_number] !== undefined;
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

/**
 * @提及的匹配式：只认「名字命中任务资产」的 @，与后端 TaskManager._at_mentions 同口径——
 * 高亮因此表示「这个名字真的能解析到资产图」，而不是任意 @ 开头的一串字。
 * 名字按长度降序做交替，长名优先（资产同时有「韩」与「韩萧」时 @韩萧 不会被切成 @韩 + 萧）。
 */
const mentionRe = computed(() => {
  const names = (props.assets || [])
    .map((a) => (a.name || "").trim())
    .filter(Boolean)
    .sort((a, b) => b.length - a.length)
    .map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return names.length ? new RegExp(`@(?:${names.join("|")})`, "g") : null;
});

/** 把正文切成 [{text, at}] 片段：只做文本切分，不拼 HTML */
function highlightSegments(text) {
  const re = mentionRe.value;
  if (!re || !text) return [{ text, at: false }];

  const segs = [];
  let last = 0;
  let m;
  re.lastIndex = 0;   // 复用同一个 global 正则，每次重置游标
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) segs.push({ text: text.slice(last, m.index), at: false });
    segs.push({ text: m[0], at: true });
    last = m.index + m[0].length;
  }
  if (last < text.length) segs.push({ text: text.slice(last), at: false });
  return segs;
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

// ── 媒体状态 ──

function getSceneMedia(sceneNumber) {
  return (props.mediaAssets || []).filter((m) => m.scene_number === sceneNumber);
}

function videoState(sceneNumber) { return mediaState(sceneNumber, "video"); }
function directorState(sceneNumber) { return mediaState(sceneNumber, "director_image"); }

function mediaState(sceneNumber, type) {
  const assets = getSceneMedia(sceneNumber);
  if (type === "any" && assets.some((a) => a.status === "failed")) return "failed";
  const matching = assets.filter((a) => a.asset_type === type);
  if (matching.some((a) => a.status === "success")) return "success";
  if (matching.some((a) => a.status === "running")) return "running";
  return "idle";
}

/** 场景号 -> 该镜成功的导演图（用于缩略图展示，一次遍历避免模板里反复 filter） */
const directorByScene = computed(() => {
  const map = {};
  for (const m of props.mediaAssets || []) {
    if (m.asset_type === "director_image" && m.status === "success") {
      map[m.scene_number] = m;
    }
  }
  return map;
});

function directorUrl(m) {
  const base = getMediaUrl(m);
  if (!base) return "";
  // OSS 签名 URL 每次生成都不同（无缓存问题），且额外拼 query 可能破坏签名；
  // 本地 /media 路径恒定，必须拼 id 破缓存，否则重新生成后浏览器仍显示旧图
  return m.url ? base : `${base}?v=${m.id}`;
}

// 当前查看大图的 URL；非空即打开 el-image-viewer 浮层
const viewerUrl = ref("");

function openDirectorImage(m) {
  const url = directorUrl(m);
  if (url) viewerUrl.value = url;
}

function statusType(m) {
  return m.status === "success" ? "success" : m.status === "failed" ? "danger" : "warning";
}
function statusLabel(m) {
  return m.status === "success" ? "已完成" : m.status === "failed" ? "失败" : "生成中";
}
function typeIcon(m) {
  return m.asset_type === "director_image" ? "📋" : "🎥";
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

/* @资产名：命中资产的高亮，提示这处引用会带出对应资产图 */
.at-mention {
  color: var(--color-primary);
  font-weight: 600;
  background: var(--color-primary-bg);
  border-radius: 4px;
  padding: 0 2px;
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
.actions-row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.actions-sep {
  width: 1px;
  align-self: stretch;
  min-height: 24px;
  background: var(--color-border-light);
  margin: 0 2px;
}
.director-select { width: 108px; }
.status-row { margin-top: 8px; display: flex; gap: 6px; flex-wrap: wrap; }
.status-tag { font-size: 11px; }

/* 6 宫格导演图预览 */
.director-preview {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
}
.director-thumb {
  display: block;
  width: 100%;
  /* 6 宫格每格才 1/6 宽，缩略图太小看不出内容，给宽一些 */
  max-width: 520px;
  border: 1px solid var(--color-border-light);
  border-radius: 8px;
  cursor: zoom-in;
  transition: box-shadow 0.2s;
  &:hover { box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15); }
}
.director-hint { font-size: 11px; color: var(--color-text-tertiary); }
.error-row { margin-top: 8px; }
.error-alert {
  margin-top: 4px;
  :deep(.el-alert__title) { font-size: 12px; }
}
</style>
