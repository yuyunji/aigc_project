<!--
  大纲展示 Tab — 解析 Markdown 为结构化幕卡片 + 时间轴
-->
<template>
  <div class="outline-tab" v-loading="loading">
    <el-empty v-if="!loading && !content" description="大纲尚未生成" />

    <div v-else-if="sections.length" class="outline-timeline">
      <div v-for="(sec, i) in sections" :key="i" class="timeline-item">
        <div class="timeline-marker">
          <span class="act-num">{{ sec.actNum || '•' }}</span>
          <div v-if="i < sections.length - 1" class="timeline-line"></div>
        </div>
        <el-card shadow="hover" class="act-card">
          <template #header>
            <div class="act-header">
              <span class="act-title">{{ sec.title }}</span>
              <el-tag v-if="sec.duration" size="small" effect="plain" round>{{ sec.duration }}</el-tag>
            </div>
          </template>
          <div class="act-body">
            <div v-for="(p, j) in sec.paragraphs" :key="j" class="act-paragraph">{{ p }}</div>
            <div v-if="sec.bullets.length" class="act-bullets">
              <div v-for="(b, k) in sec.bullets" :key="k" class="bullet">• {{ b }}</div>
            </div>
          </div>
        </el-card>
      </div>
    </div>

    <!-- 兜底：纯文本渲染 -->
    <el-card v-else-if="content" shadow="hover">
      <div class="markdown-body" v-html="rendered" />
    </el-card>
  </div>
</template>

<script setup>
import { computed } from "vue";
import { renderMarkdown } from "../utils/markdown";

const props = defineProps({
  content: { type: String, default: "" },
  loading: { type: Boolean, default: false },
});

const rendered = computed(() => renderMarkdown(props.content));

// 解析大纲为结构化幕
const sections = computed(() => {
  const text = props.content || "";
  if (!text.trim()) return [];

  // 按 "## " 或 "# " 标题拆分
  const parts = text.split(/\n(?=#{1,2}\s)/);
  const result = [];

  for (const part of parts) {
    const lines = part.trim().split("\n");
    const header = lines[0].replace(/^#+\s*/, "").trim();
    if (!header) continue;

    // 提取时长（如"约3分钟"）
    const durMatch = header.match(/[约]*(\d+)\s*分钟/);
    const duration = durMatch ? `约${durMatch[1]}分钟` : "";

    const body = lines.slice(1).join("\n").trim();
    const paragraphs = [];
    const bullets = [];

    for (const line of body.split("\n")) {
      const trimmed = line.replace(/^[-*]\s*/, "").trim();
      if (!trimmed) continue;
      if (line.match(/^[-*]\s/)) {
        bullets.push(trimmed);
      } else {
        paragraphs.push(trimmed);
      }
    }

    result.push({
      title: header,
      duration,
      actNum: result.length + 1,
      paragraphs: paragraphs.slice(0, 3),
      bullets: bullets.slice(0, 8),
    });
  }
  return result;
});
</script>

<style lang="scss" scoped>
.outline-timeline {
  position: relative;
  padding-left: 56px;
}

.timeline-item {
  position: relative;
  margin-bottom: 20px;
  &:last-child { margin-bottom: 0; }
}

.timeline-marker {
  position: absolute;
  left: -56px;
  top: 16px;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.act-num {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--color-primary), var(--color-primary-light));
  color: #fff;
  font-size: 15px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 2px 10px rgba(99, 102, 241, 0.3);
  flex-shrink: 0;
}

.timeline-line {
  width: 2px;
  flex: 1;
  min-height: 30px;
  background: linear-gradient(to bottom, var(--color-primary-light), var(--color-border-light));
  margin-top: 6px;
}

.act-card {
  transition: all var(--transition-base);
  &:hover { transform: translateX(4px); }
  :deep(.el-card__header) { padding: 14px 20px; background: #FAFAFB; }
  :deep(.el-card__body) { padding: 18px 20px; }
}

.act-header {
  display: flex;
  align-items: center;
  gap: 10px;
}

.act-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--color-text-primary);
}

.act-body {
  font-size: 13px;
  line-height: 1.8;
  color: var(--color-text-primary);
}

.act-paragraph {
  margin-bottom: 8px;
  &:last-child { margin-bottom: 0; }
}

.act-bullets {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--color-border-light);
}

.bullet {
  font-size: 13px;
  line-height: 1.8;
  color: var(--color-text-secondary);
  padding: 3px 0;
}
</style>
