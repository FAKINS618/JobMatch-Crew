<script setup lang="ts">
import { onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { getNextActions, type NextAction } from "@/api/workspace";

const actions = ref<NextAction[]>([]);
const isLoading = ref(false);
const errorMessage = ref("");

const priorityLabels: Record<NextAction["priority"], string> = {
  high: "优先处理",
  medium: "建议处理",
  low: "稍后查看",
};

function formatDate(value: string | null): string {
  return value ? value.replace("T", " ") : "";
}

async function loadActions() {
  isLoading.value = true;
  errorMessage.value = "";
  try {
    actions.value = await getNextActions();
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "读取今日待办失败";
  } finally {
    isLoading.value = false;
  }
}

onMounted(loadActions);
</script>

<template>
  <div class="workspace">
    <header class="page-heading workspace-heading">
      <div>
        <p class="eyebrow">今日工作台</p>
        <h1>先完成最重要的一步</h1>
      </div>
      <button class="secondary" :disabled="isLoading" @click="loadActions">
        {{ isLoading ? "正在刷新" : "刷新待办" }}
      </button>
    </header>

    <p class="helper-text">这里聚合简历、报告、投递、面试和成长计划中的下一步，不会自动替你投递或修改简历。</p>
    <p v-if="errorMessage" class="error-message">{{ errorMessage }}</p>
    <section v-if="isLoading && actions.length === 0" class="artifact-section">
      <p>正在整理你的下一步...</p>
    </section>
    <section v-else-if="actions.length === 0" class="artifact-section empty-state">
      <h2>目前没有待处理事项</h2>
      <p>可以先从简历中心确认一个版本，或从岗位收件箱审阅可投递岗位。</p>
      <div class="action-links">
        <RouterLink class="link-button" to="/resumes">查看简历中心</RouterLink>
        <RouterLink class="link-button" to="/inbox">查看岗位收件箱</RouterLink>
      </div>
    </section>
    <section v-else class="next-action-list" aria-label="今日待办">
      <article v-for="item in actions" :key="item.id" class="next-action-row">
        <div class="next-action-content">
          <div class="artifact-heading">
            <div>
              <p class="eyebrow">{{ priorityLabels[item.priority] }}</p>
              <h2>{{ item.title }}</h2>
            </div>
            <time v-if="item.due_at" :datetime="item.due_at">截止 {{ formatDate(item.due_at_local || item.due_at) }}</time>
          </div>
          <p>{{ item.description }}</p>
          <p v-if="item.updated_at" class="helper-text">最近更新：{{ formatDate(item.updated_at_local || item.updated_at) }}</p>
        </div>
        <RouterLink class="link-button primary-action" :to="item.route">{{ item.action_label }}</RouterLink>
      </article>
    </section>
  </div>
</template>
