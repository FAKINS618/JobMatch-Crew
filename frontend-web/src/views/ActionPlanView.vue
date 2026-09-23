<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { listResumeVersions, type ResumeVersion } from "@/api/copilot";
import {
  archiveActionItem,
  createActionEvidence,
  listActionItems,
  restoreActionItem,
  updateActionItem,
  type ActionItem,
} from "@/api/workspace";

const items = ref<ActionItem[]>([]);
const resumeVersions = ref<ResumeVersion[]>([]);
const errorMessage = ref("");
const evidenceNotes = ref<Record<number, string>>({});
const evidenceUrls = ref<Record<number, string>>({});
const includeArchived = ref(false);
const selectedResumeId = ref<number | "all">("all");
const selectedSource = ref<"all" | "report" | "interview_review">("all");
const statusLabels: Record<ActionItem["status"], string> = {
  todo: "待开始",
  in_progress: "进行中",
  completed: "已完成",
  cancelled: "已归档",
};
const sourceLabels: Record<ActionItem["source_type"], string> = {
  report: "报告缺口",
  interview_review: "面试复盘",
};

async function loadItems() {
  try {
    const [loadedItems, versions] = await Promise.all([
      listActionItems({ includeArchived: includeArchived.value }),
      listResumeVersions(),
    ]);
    items.value = loadedItems;
    resumeVersions.value = versions;
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "读取成长任务失败";
  }
}

const filteredItems = computed(() => items.value.filter((item) => {
  const matchesResume = selectedResumeId.value === "all" || item.resume_version_id === selectedResumeId.value;
  const matchesSource = selectedSource.value === "all" || item.source_type === selectedSource.value;
  return matchesResume && matchesSource;
}));

const groupedItems = computed(() => {
  const groups = new Map<number | null, ActionItem[]>();
  for (const item of filteredItems.value) {
    const key = item.resume_version_id ?? null;
    groups.set(key, [...(groups.get(key) ?? []), item]);
  }
  return [...groups.entries()].map(([resumeId, groupItems]) => ({
    resumeId,
    label: resumeId === null ? "未关联简历版本" : (resumeVersions.value.find((version) => version.id === resumeId)?.version_name ?? `简历版本 #${resumeId}`),
    items: groupItems,
  }));
});

async function updateStatus(item: ActionItem, status: ActionItem["status"]) {
  try {
    await updateActionItem(item.id, status);
    await loadItems();
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "更新任务失败";
  }
}

async function archive(item: ActionItem) {
  try {
    await archiveActionItem(item.id);
    await loadItems();
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "归档任务失败";
  }
}

async function restore(item: ActionItem) {
  try {
    await restoreActionItem(item.id);
    await loadItems();
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "恢复任务失败";
  }
}

function readStatus(event: Event): ActionItem["status"] {
  return (event.target as HTMLSelectElement).value as ActionItem["status"];
}

async function submitEvidence(item: ActionItem) {
  const content = evidenceNotes.value[item.id]?.trim() ?? "";
  const url = evidenceUrls.value[item.id]?.trim() ?? "";
  if (!content && !url) {
    errorMessage.value = "请填写成果说明或成果链接";
    return;
  }
  try {
    await createActionEvidence(item.id, content, url);
    evidenceNotes.value[item.id] = "";
    evidenceUrls.value[item.id] = "";
    await loadItems();
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "提交成果证据失败";
  }
}

onMounted(loadItems);
</script>

<template>
  <div class="workspace">
    <header class="page-heading">
      <div><p class="eyebrow">成长计划</p><h1>把缺口变成可验证成果</h1></div>
      <p>任务按简历版本和来源分组；归档只隐藏任务，不删除证据和来源记录。</p>
    </header>
    <section class="artifact-section action-filters">
      <label>简历版本
        <select v-model="selectedResumeId"><option value="all">全部简历版本</option><option v-for="version in resumeVersions" :key="version.id" :value="version.id">{{ version.version_name }}</option></select>
      </label>
      <label>任务来源
        <select v-model="selectedSource"><option value="all">全部来源</option><option value="report">报告缺口</option><option value="interview_review">面试复盘</option></select>
      </label>
      <label class="checkbox-label"><input v-model="includeArchived" type="checkbox" @change="loadItems" /> 显示已归档</label>
    </section>
    <p v-if="errorMessage" class="error-message">{{ errorMessage }}</p>
    <section v-if="filteredItems.length === 0" class="artifact-section"><p>当前筛选下暂无成长任务。</p></section>
    <section v-for="group in groupedItems" :key="group.resumeId ?? 'none'" class="action-group">
      <header class="section-heading"><p class="eyebrow">简历上下文</p><h2>{{ group.label }}</h2></header>
      <article v-for="item in group.items" :key="item.id" class="artifact-section action-card">
        <header class="artifact-heading">
          <div><p class="eyebrow">{{ sourceLabels[item.source_type] }} · {{ item.priority }} priority · {{ statusLabels[item.status] }}</p><h2>{{ item.title }}</h2></div>
          <strong>{{ item.evidence_count }} 份证据</strong>
        </header>
        <p>{{ item.expected_output }}</p>
        <div class="source-links">
          <RouterLink v-if="item.source_report_id" :to="{ path: '/inbox', query: { report: item.source_report_id } }">查看来源报告</RouterLink>
          <RouterLink v-if="item.source_job_target_id" :to="{ path: '/pipeline', query: { target: item.source_job_target_id } }">查看关联岗位{{ item.source_job_title ? `：${item.source_job_title}` : "" }}</RouterLink>
          <RouterLink v-if="item.source_interview_review_id && item.source_job_target_id" :to="{ path: '/pipeline', query: { target: item.source_job_target_id } }">查看面试复盘</RouterLink>
        </div>
        <div class="action-controls">
          <select v-if="!item.archived_at" :value="item.status" @change="updateStatus(item, readStatus($event))"><option v-for="(label, status) in statusLabels" :key="status" :value="status">{{ label }}</option></select>
          <button v-if="item.archived_at" class="secondary" @click="restore(item)">恢复任务</button>
          <button v-else class="secondary" @click="archive(item)">归档</button>
        </div>
        <div v-if="item.status !== 'completed' && !item.archived_at" class="evidence-form"><input v-model="evidenceUrls[item.id]" placeholder="成果链接（可选）" /><textarea v-model="evidenceNotes[item.id]" placeholder="成果说明，例如完成了什么、验证了什么..." /><button @click="submitEvidence(item)">提交成果证据</button></div>
      </article>
    </section>
  </div>
</template>