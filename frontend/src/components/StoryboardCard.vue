<!--
  分镜脚本卡片 — 结构化渲染 + 按分镜操作按钮（图片/视频/重试）
-->
<template>
  <div class="storyboard-card" v-loading="loading">
    <el-empty v-if="!loading && scenes.length === 0" description="分镜脚本尚未生成" />

    <div v-else class="storyboard-timeline">
      <div v-for="scene in scenes" :key="scene.id" class="timeline-item">
        <div class="timeline-marker">
          <span class="scene-num">{{ scene.scene_number }}</span>
        </div>

        <el-card shadow="hover" class="timeline-card">
          <!-- 标题行 -->
          <div class="scene-header">
            <h3 class="scene-title">{{ scene.scene_title || '镜头 ' + scene.scene_number }}</h3>
            <el-tag size="small" effect="plain" round v-if="scene.duration_seconds">
              {{ scene.duration_seconds }}s
            </el-tag>
          </div>

          <!-- 模板格式字段：镜头景别 / 拍摄角度 / 运镜 / 构图 / 情绪 -->
          <div class="scene-meta">
            <span v-if="scene.shot_size" class="meta-item"><span class="meta-icon">🎞️</span>{{ scene.shot_size }}</span>
            <span v-if="scene.camera_angle" class="meta-item"><span class="meta-icon">📐</span>{{ scene.camera_angle }}</span>
            <span v-if="scene.camera_movement" class="meta-item"><span class="meta-icon">🎥</span>{{ scene.camera_movement }}</span>
            <span v-if="scene.composition" class="meta-item"><span class="meta-icon">🖼️</span>{{ scene.composition }}</span>
            <span v-if="scene.mood" class="meta-item mood-tag"><span class="meta-icon">🎭</span>{{ scene.mood }}</span>
            <span v-if="scene.transition" class="meta-item transition-tag"><span class="meta-icon">🎬</span>{{ scene.transition }}</span>
          </div>

          <!-- 模板格式主体内容 -->
          <div v-if="scene.subject" class="scene-subject">
            <div class="section-label">👤 画面主体人物</div>
            <p>{{ scene.subject }}</p>
          </div>

          <div v-if="scene.environment" class="scene-environment">
            <div class="section-label">📍 场景环境</div>
            <p>{{ scene.environment }}</p>
          </div>

          <!-- 旧版兼容：出场角色 / 画面描述 / 台词 -->
          <div v-if="scene.characters_in_scene" class="scene-characters">
            <span class="char-label">👥 出场：</span>
            <el-tag v-for="char in splitChars(scene.characters_in_scene)" :key="char" size="small" effect="plain" class="char-tag">{{ char }}</el-tag>
          </div>

          <div v-if="scene.visual_description && !scene.subject" class="scene-visual">
            <div class="section-label">🖼️ 画面描述</div>
            <p>{{ scene.visual_description }}</p>
          </div>

          <div v-if="scene.dialogue_text && scene.dialogue_text !== '@无' && scene.dialogue_text !== '@无对白'" class="scene-dialogue">
            <div class="section-label">💬 台词对白</div>
            <div class="dialogue-line is-speaker">{{ scene.dialogue_text }}</div>
          </div>

          <!-- 旧版台词兜底 -->
          <div v-if="!scene.dialogue_text && scene.dialogue" class="scene-dialogue">
            <div class="section-label">💬 台词</div>
            <div v-for="(line, i) in splitLines(scene.dialogue)" :key="i" class="dialogue-line" :class="{ 'is-speaker': isSpeakerLine(line) }">{{ line }}</div>
          </div>

          <!-- 完整 prompt 折叠 -->
          <el-collapse v-if="scene.image_prompt" class="prompt-collapse">
            <el-collapse-item title="📝 完整生成 Prompt">
              <p class="prompt-text">{{ scene.image_prompt }}</p>
            </el-collapse-item>
          </el-collapse>

          <!-- 生成的分镜图片 -->
          <div v-if="getSceneImage(scene.scene_number)" class="scene-image">
            <img
              :src="getMediaUrl(getSceneImage(scene.scene_number))"
              :alt="`分镜 ${scene.scene_number}`"
              loading="lazy"
            />
          </div>

          <!-- ── 操作区 ── -->
          <div class="scene-actions">
            <div class="actions-row">
              <el-button
                size="small"
                type="primary"
                plain
                :loading="imageState(scene.scene_number) === 'running'"
                :disabled="imageState(scene.scene_number) === 'running'"
                @click="$emit('generate-image', scene.scene_number)"
              >
                {{ imageState(scene.scene_number) === 'success' ? '🔄 重新生成图片' : '🖼️ 生成图片' }}
              </el-button>
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
              <el-button v-if="mediaState(scene.scene_number, 'any') === 'failed'" size="small" type="warning" plain @click="$emit('retry', scene.scene_number)">
                🔄 重试
              </el-button>
            </div>

            <!-- 状态标签 -->
            <div class="status-row" v-if="getSceneMedia(scene.scene_number).length">
              <el-tag v-for="m in getSceneMedia(scene.scene_number)" :key="m.id" size="small" :type="statusType(m)" effect="plain" round class="status-tag">
                {{ m.asset_type === 'image' ? '🖼️' : '🎥' }} {{ statusLabel(m) }}
              </el-tag>
            </div>

            <!-- 错误信息 -->
            <div v-if="getSceneMedia(scene.scene_number).some(m => m.status === 'failed' && m.error_message)" class="error-row">
              <el-alert
                v-for="m in getSceneMedia(scene.scene_number).filter(x => x.status === 'failed' && x.error_message)"
                :key="m.id"
                :title="m.error_message"
                type="error"
                :closable="false"
                show-icon
                class="error-alert"
              />
            </div>
          </div>
        </el-card>
      </div>
    </div>
  </div>
</template>

<script setup>
import { getMediaUrl } from "../utils/media";

const props = defineProps({
  scenes: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  taskId: { type: String, default: "" },
  mediaAssets: { type: Array, default: () => [] },
});

defineEmits(["generate-video", "generate-image", "retry"]);

function getSceneMedia(sceneNumber) {
  return (props.mediaAssets || []).filter(
    (m) => m.scene_number === sceneNumber
  );
}

function getSceneImage(sceneNumber) {
  return getSceneMedia(sceneNumber).find(
    (m) => m.asset_type === "image" && m.status === "success"
  );
}

function videoState(sceneNumber) { return mediaState(sceneNumber, "video"); }
function imageState(sceneNumber) { return mediaState(sceneNumber, "image"); }

function mediaState(sceneNumber, type) {
  const assets = getSceneMedia(sceneNumber);
  if (type === "any" && assets.some((a) => a.status === "failed")) return "failed";
  const matching = assets.filter((a) => (type === "image" ? a.asset_type === "image" : a.asset_type === "video"));
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

function splitChars(text) { return (text || "").split(/[、,，]/).map((s) => s.trim()).filter(Boolean); }
function splitLines(text) { return (text || "").split("\n").filter((l) => l.trim()); }
function isSpeakerLine(line) { return /^[^：:]+[：:]/.test(line); }
</script>

<style lang="scss" scoped>
.storyboard-timeline { position: relative; padding-left: 44px; }
.storyboard-timeline::before { content: ""; position: absolute; left: 19px; top: 0; bottom: 0; width: 2px; background: linear-gradient(to bottom, var(--color-primary), var(--color-primary-light) 80%, transparent); }
.timeline-item { position: relative; margin-bottom: 24px; }
.timeline-item:last-child { margin-bottom: 0; }
.timeline-marker { position: absolute; left: -44px; top: 16px; width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; }
.scene-num { width: 34px; height: 34px; border-radius: 50%; background: var(--color-primary); color: #fff; font-size: 14px; font-weight: 700; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 10px rgba(99, 102, 241, 0.35); }
.timeline-card { transition: box-shadow 0.2s, border-color 0.2s; }
.timeline-card:hover { box-shadow: 0 4px 24px rgba(99, 102, 241, 0.15) !important; border-color: var(--color-primary-light); }
.scene-header { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.scene-title { font-size: 16px; font-weight: 700; color: var(--color-text-primary); margin: 0; }
.scene-meta { display: flex; flex-wrap: wrap; gap: 14px; margin-bottom: 10px; }
.meta-item { font-size: 13px; color: var(--color-text-secondary); display: flex; align-items: center; gap: 4px; }
.scene-characters { display: flex; align-items: center; gap: 6px; margin-bottom: 12px; flex-wrap: wrap; }
.char-label { font-size: 13px; color: var(--color-text-secondary); }
.char-tag { font-size: 12px; }
.scene-image {
  margin-bottom: 14px;
  border-radius: var(--radius-md);
  overflow: hidden;
  border: 1px solid var(--color-border-light);

  img {
    width: 100%;
    display: block;
    aspect-ratio: 16 / 9;
    object-fit: cover;
  }
}

.scene-visual { margin-bottom: 12px; }
.scene-visual p { font-size: 13px; line-height: 1.7; color: var(--color-text-primary); }
.scene-subject { margin-bottom: 10px; }
.scene-subject p { font-size: 13px; line-height: 1.7; color: var(--color-text-primary); }
.scene-environment { margin-bottom: 10px; }
.scene-environment p { font-size: 13px; line-height: 1.7; color: var(--color-text-accent); }
.scene-dialogue { margin-bottom: 8px; }
.dialogue-line { font-size: 13px; line-height: 1.7; color: var(--color-text-primary); padding: 4px 0; padding-left: 8px; border-left: 2px solid var(--color-primary-light); }
.dialogue-line.is-speaker { font-weight: 600; color: var(--color-primary-dark); border-left-color: var(--color-primary); }
.section-label { font-size: 12px; font-weight: 600; color: var(--color-text-tertiary); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
.mood-tag { background: var(--color-primary-bg); padding: 2px 8px; border-radius: 4px; }
.transition-tag { background: rgba(245,158,11,0.12); padding: 2px 8px; border-radius: 4px; color: var(--color-warning, #d97706); }
.prompt-collapse { margin-top: 10px; }
.prompt-collapse :deep(.el-collapse-item__header) { font-size: 12px; color: var(--color-text-tertiary); border-bottom: none; }
.prompt-collapse :deep(.el-collapse-item__wrap) { border-bottom: none; }
.prompt-text { font-size: 12px; line-height: 1.6; color: var(--color-text-secondary); background: var(--color-bg-secondary); padding: 10px; border-radius: 6px; word-break: break-all; white-space: pre-wrap; }

.scene-actions { margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--color-border-light); }
.actions-row { display: flex; gap: 8px; flex-wrap: wrap; }
.status-row { margin-top: 8px; display: flex; gap: 6px; flex-wrap: wrap; }
.status-tag { font-size: 11px; }
.error-row { margin-top: 8px; }
.error-alert { margin-top: 4px; }
.error-alert :deep(.el-alert__title) { font-size: 12px; }
</style>
