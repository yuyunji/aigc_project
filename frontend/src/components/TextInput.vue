<!--
  文本输入组件 —— v-model + 字数统计 + 长文本标签
-->
<template>
  <div class="text-input">
    <el-input
      :model-value="modelValue"
      @update:model-value="$emit('update:modelValue', $event)"
      type="textarea"
      :rows="rows"
      :placeholder="placeholder"
      :maxlength="maxLength"
      show-word-limit
      resize="vertical"
    />
    <div class="text-input__footer">
      <span class="text-input__count">
        共 <strong>{{ charCount.toLocaleString() }}</strong> 字
        <el-tag
          v-if="modelValue && modelValue.length > 5000"
          size="small"
          type="warning"
          effect="plain"
          round
          class="long-tag"
        >
          长文本 · 将自动分片处理
        </el-tag>
      </span>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({
  modelValue: { type: String, default: "" },
  rows: { type: Number, default: 12 },
  placeholder: {
    type: String,
    default: "请粘贴原著文本内容（不少于 10 个字符）...",
  },
  maxLength: { type: Number, default: 100000 },
});

defineEmits(["update:modelValue"]);

const charCount = computed(() => (props.modelValue || "").length);
</script>

<style lang="scss" scoped>
.text-input {
  width: 100%;
  &__footer {
    margin-top: 6px;
    display: flex;
    justify-content: flex-end;
  }

  &__count {
    font-size: 13px;
    color: var(--color-text-secondary);

    strong {
      font-weight: 700;
      color: var(--color-text-primary);
    }
  }
}

.long-tag {
  margin-left: 8px;
  font-size: 11px;
}
</style>
