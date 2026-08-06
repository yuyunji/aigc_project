<!--
  角色展示 Tab — 每个角色独立卡片，内部分组展示为子卡片
-->
<template>
  <div class="character-tab" v-loading="loading">
    <el-empty v-if="!loading && characters.length === 0" description="人物角色尚未生成" />

    <div v-else class="character-grid">
      <!-- 每个角色一张大卡片 -->
      <el-card v-for="char in characters" :key="char.id" shadow="hover" class="character-card">

        <!-- 顶部：头像 + 名称 + 身份标签 -->
        <div class="char-header">
          <div class="char-avatar" :style="{ background: avatarColor(char.name) }">
            <span class="avatar-text">{{ char.name.charAt(0) }}</span>
          </div>
          <div class="char-identity">
            <h3 class="char-name">{{ char.name }}</h3>
            <el-tag v-if="roleTag(char)" size="small" :type="roleTagType(roleTag(char))" effect="dark" round>
              {{ roleTag(char) }}
            </el-tag>
          </div>
        </div>

        <!-- 分组子卡片 -->
        <div v-if="groups(char.description).length" class="char-groups">
          <div v-for="(grp, gi) in groups(char.description)" :key="gi" class="info-group">
            <div class="group-header" v-if="grp.title">
              <span class="group-icon">{{ groupIcon(grp) }}</span>
              <span class="group-title">{{ grp.title }}</span>
            </div>
            <div class="group-body">
              <div v-for="item in grp.items" :key="item.key" class="info-row">
                <span class="info-key">{{ item.key }}</span>
                <span class="info-value">{{ item.value }}</span>
              </div>
            </div>
          </div>
        </div>

        <!-- 兜底：无结构化字段时折叠显示原文 -->
        <el-collapse v-else-if="char.description" class="char-collapse">
          <el-collapse-item title="详细描述">
            <div class="markdown-body" v-html="renderMarkdown(char.description)" />
          </el-collapse-item>
        </el-collapse>

      </el-card>
    </div>
  </div>
</template>

<script setup>
import { renderMarkdown } from "../utils/markdown";

defineProps({
  characters: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
});

// ── 头像颜色 hash ──
function avatarColor(name) {
  const colors = ["#6366F1", "#EC4899", "#F59E0B", "#22C55E", "#3B82F6", "#EF4444", "#8B5CF6", "#06B6D4"];
  let hash = 0;
  for (let i = 0; i < (name || "?").length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
  return colors[Math.abs(hash) % colors.length];
}

// ── 分组解析：按 # / ## / ### 标题拆分 ──
function groups(desc) {
  if (!desc) return [];

  const lines = desc.split("\n");
  const result = [];
  let current = null;

  function flush() {
    if (current && current.items.length) {
      // 无标题分组自动推断标签
      if (!current.title) current.title = inferTitle(current.items);
      result.push(current);
    }
    current = null;
  }

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;

    // 检测 # / ## / ### 分组标题（如 "# 性格特征"、"### 外貌描述"）
    const headMatch = line.match(/^#{1,3}\s+(.+)/);
    if (headMatch) {
      flush();
      current = { title: headMatch[1].trim(), items: [] };
      continue;
    }

    // 独立的 # 或 ## 作为无标题分隔符
    if (/^#{1,2}$/.test(line)) {
      flush();
      continue;
    }

    // 解析 **key**：value
    const kv = line.match(/^[-*]\s*\*\*(.+?)\*\*[：:]\s*(.+)/);
    if (kv) {
      if (!current) current = { title: "", items: [] };
      const key = kv[1].trim();
      const value = kv[2].trim();
      if (key && value && value.length < 200) {
        current.items.push({ key, value });
      }
    }
  }
  flush();
  return result;
}

// ── 无标题分组根据内容推断名称 ──
function inferTitle(items) {
  const keys = items.map(i => i.key);
  const joined = keys.join("");
  if (/定位|身份|年龄|性别|种族|门派/.test(joined)) return "基本信息";
  if (/性格|人格|心理|情绪|脾气/.test(joined)) return "性格特征";
  if (/外貌|发型|身材|特征|衣着|标志|细节/.test(joined)) return "外貌描述";
  if (/背景|经历|身世|历史|过往/.test(joined)) return "背景故事";
  if (/弧线|成长|转变|结局/.test(joined)) return "角色弧线";
  if (/能力|技能|武功|武器|功法/.test(joined)) return "能力设定";
  if (/关系|羁绊|师徒|亲友/.test(joined)) return "人物关系";
  return "";
}

// ── 分组图标 ──
function groupIcon(grp) {
  const t = grp.title || "";
  if (/基本/.test(t)) return "📋";
  if (/性格/.test(t)) return "🧠";
  if (/外貌/.test(t)) return "👤";
  if (/背景/.test(t)) return "📖";
  if (/弧线/.test(t)) return "📈";
  if (/能力/.test(t)) return "⚔️";
  if (/关系/.test(t)) return "🔗";
  return "📌";
}

// ── 从描述中提取角色身份标签 ──
function roleTag(char) {
  const desc = char.description || "";
  const allGroups = groups(desc);
  for (const g of allGroups) {
    for (const item of g.items) {
      if (/定位|角色/.test(item.key)) return item.value;
    }
  }
  if (/主角|主人公/.test(desc)) return "主角";
  if (/反派/.test(desc)) return "反派";
  if (/配角/.test(desc)) return "配角";
  return "";
}

function roleTagType(role) {
  if (/主角/.test(role)) return "";
  if (/反派/.test(role)) return "danger";
  return "info";
}
</script>

<style lang="scss" scoped>
.character-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: var(--space-lg);
}

/* ── 角色大卡片 ── */
.character-card {
  transition: all var(--transition-base);
  border-radius: var(--radius-lg);
  overflow: hidden;

  &:hover {
    transform: translateY(-3px);
    box-shadow: var(--shadow-lg) !important;
  }

  :deep(.el-card__body) {
    padding: 0;
  }
}

/* ── 头部：头像 + 名称 + 标签 ── */
.char-header {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 24px 24px 20px;
  background: linear-gradient(135deg, #fafafe 0%, #f0f0ff 100%);
  border-bottom: 1px solid var(--color-border-light);
}

.char-avatar {
  width: 60px;
  height: 60px;
  border-radius: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.avatar-text {
  font-size: 26px;
  font-weight: 800;
  color: #fff;
  letter-spacing: 2px;
}

.char-identity {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.char-name {
  font-size: 20px;
  font-weight: 800;
  color: var(--color-text-primary);
  margin: 0;
  line-height: 1.2;
}

/* ── 分组子卡片区域 ── */
.char-groups {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 16px 20px 20px;
}

/* ── 单个分组子卡片 ── */
.info-group {
  background: #fff;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  overflow: hidden;
  transition: border-color 0.2s;

  &:hover {
    border-color: var(--color-primary-light);
  }
}

.group-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  background: linear-gradient(to right, #f8f9fc, #fdfdfe);
  border-bottom: 1px solid var(--color-border-lighter);
}

.group-icon {
  font-size: 14px;
}

.group-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--color-text-secondary);
  letter-spacing: 0.3px;
}

.group-body {
  padding: 6px 0;
}

/* ── 单行信息 ── */
.info-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 8px 16px;
  transition: background 0.15s;

  &:hover {
    background: #fafafc;
  }

  & + & {
    border-top: 1px solid #f5f5f8;
  }
}

.info-key {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-tertiary);
  white-space: nowrap;
  flex-shrink: 0;
  min-width: 60px;
  padding-top: 2px;

  &::after {
    content: "";
    // subtle key separator handled by gap
  }
}

.info-value {
  font-size: 13px;
  color: var(--color-text-primary);
  line-height: 1.7;
}

/* ── 兜底折叠 ── */
.char-collapse {
  border: none;
  margin: 16px 20px 20px;

  :deep(.el-collapse-item__header) {
    font-size: 13px;
    color: var(--color-primary);
    border: none;
  }
  :deep(.el-collapse-item__content) {
    font-size: 13px;
  }
}
</style>
