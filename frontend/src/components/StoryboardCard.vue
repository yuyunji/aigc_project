<!--
  分镜脚本卡片 — JSON 结构化渲染：标题/场景/运镜/台词/画面描述
-->
<template>
  <div class="storyboard-card" v-loading="loading">
    <el-empty v-if="!loading && scenes.length === 0" description="分镜脚本尚未生成" />

    <div v-else class="storyboard-timeline">
      <div
        v-for="scene in scenes"
        :key="scene.id"
        class="timeline-item"
      >
        <div class="timeline-marker">
          <span class="scene-num">{{ scene.scene_number }}</span>
        </div>

        <el-card shadow="hover" class="timeline-card">
          <!-- 标题行 -->
          <div class="scene-header">
            <h3 class="scene-title">{{ scene.scene_title || '第' + scene.scene_number + '幕' }}</h3>
            <el-tag size="small" effect="plain" round v-if="scene.duration_seconds">
              {{ scene.duration_seconds }}s
            </el-tag>
          </div>

          <!-- 元信息行 -->
          <div class="scene-meta">
            <span v-if="scene.location" class="meta-item">
              <span class="meta-icon">📍</span>{{ scene.location }}
            </span>
            <span v-if="scene.time_of_day" class="meta-item">
              <span class="meta-icon">🕐</span>{{ scene.time_of_day }}
            </span>
            <span v-if="scene.camera_movement" class="meta-item">
              <span class="meta-icon">🎥</span>{{ scene.camera_movement }}
            </span>
          </div>

          <!-- 出场角色 -->
          <div v-if="scene.characters_in_scene" class="scene-characters">
            <span class="char-label">👥 出场：</span>
            <el-tag
              v-for="char in splitChars(scene.characters_in_scene)"
              :key="char"
              size="small"
              effect="plain"
              class="char-tag"
            >{{ char }}</el-tag>
          </div>

          <!-- 画面描述 -->
          <div v-if="scene.visual_description" class="scene-visual">
            <div class="section-label">🖼️ 画面描述</div>
            <p>{{ scene.visual_description }}</p>
          </div>

          <!-- 台词 -->
          <div v-if="scene.dialogue" class="scene-dialogue">
            <div class="section-label">💬 台词</div>
            <div
              v-for="(line, i) in splitLines(scene.dialogue)"
              :key="i"
              class="dialogue-line"
              :class="{ 'is-speaker': isSpeakerLine(line) }"
            >{{ line }}</div>
          </div>

          <!-- Image Prompt（折叠） -->
          <el-collapse v-if="scene.image_prompt" class="prompt-collapse">
            <el-collapse-item title="🎨 AI Image Prompt">
              <code class="prompt-code">{{ scene.image_prompt }}</code>
            </el-collapse-item>
          </el-collapse>
        </el-card>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  scenes: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
});

function splitChars(text) {
  return text.split(/[、,，]/).map((s) => s.trim()).filter(Boolean);
}
function splitLines(text) {
  return text.split("\n").filter((l) => l.trim());
}
function isSpeakerLine(line) {
  return /^[^：:]+[：:]/.test(line);
}
</script>

<style lang="scss" scoped>
.storyboard-timeline {
  position: relative;
  padding-left: 44px;

  &::before {
    content: "";
    position: absolute;
    left: 19px;
    top: 0;
    bottom: 0;
    width: 2px;
    background: linear-gradient(to bottom, var(--color-primary), var(--color-primary-light) 80%, transparent);
  }
}

.timeline-item {
  position: relative;
  margin-bottom: 24px;
  &:last-child { margin-bottom: 0; }
}

.timeline-marker {
  position: absolute;
  left: -44px;
  top: 16px;
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;

  .scene-num {
    width: 34px;
    height: 34px;
    border-radius: 50%;
    background: var(--color-primary);
    color: #fff;
    font-size: 14px;
    font-weight: 700;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 2px 10px rgba(99, 102, 241, 0.35);
  }
}

.timeline-card {
  transition: all var(--transition-base);
  &:hover { transform: translateX(4px); }
}

// 标题行
.scene-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;

  .scene-title {
    font-size: 16px;
    font-weight: 700;
    color: var(--color-text-primary);
    margin: 0;
  }
}

// 元信息
.scene-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-bottom: 10px;
}

.meta-item {
  font-size: 13px;
  color: var(--color-text-secondary);
  display: flex;
  align-items: center;
  gap: 4px;

  .meta-icon { font-size: 14px; }
}

// 角色标签
.scene-characters {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
  flex-wrap: wrap;

  .char-label {
    font-size: 13px;
    color: var(--color-text-secondary);
  }

  .char-tag {
    font-size: 12px;
  }
}

// 画面描述
.scene-visual {
  margin-bottom: 12px;

  p {
    font-size: 13px;
    line-height: 1.7;
    color: var(--color-text-primary);
  }
}

// 台词
.scene-dialogue {
  margin-bottom: 8px;
}

.dialogue-line {
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-text-primary);
  padding: 4px 0;
  padding-left: 8px;
  border-left: 2px solid var(--color-primary-light);

  &.is-speaker {
    font-weight: 600;
    color: var(--color-primary-dark);
    border-left-color: var(--color-primary);
  }
}

// 公共标签
.section-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 4px;
}

// Prompt 折叠
.prompt-collapse {
  margin-top: 10px;
  border: none;

  :deep(.el-collapse-item__header) {
    font-size: 12px;
    color: var(--color-text-tertiary);
    border: none;
  }
  :deep(.el-collapse-item__wrap) {
    border: none;
  }
}

.prompt-code {
  font-size: 12px;
  color: var(--color-text-secondary);
  font-family: var(--font-mono);
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
