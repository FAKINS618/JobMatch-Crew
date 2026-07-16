<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import ArtifactCards from "@/components/ArtifactCards.vue";
import { createActionItemsFromReport } from "@/api/workspace";
import { useCopilotStore } from "@/stores/copilot";

const store = useCopilotStore();
const route = useRoute();
const selectedResumeId = ref<number | undefined>();
const targetRole = ref("");
const message = ref("");
const hasSession = computed(() => store.session !== null);
const newestArtifacts = computed(() => {
  if (store.activeTurn?.artifacts.length) return store.activeTurn.artifacts;
  const previousTurn = store.session?.turns.find((turn) => turn.artifacts.length);
  return previousTurn?.artifacts ?? [];
});
const analysisMode = computed<"rule" | "deep">(() =>
  ["completed", "follow_up_ready"].includes(store.activeTurn?.stage ?? "") ? "deep" : "rule",
);
const stageLabel = computed(() => {
  const labels: Record<string, string> = {
    queued: "已创建分析任务",
    reading_resume: "正在读取简历版本",
    matching_evidence: "正在核对岗位要求与简历证据",
    deepening_analysis: "基础对比已完成，CrewAI 多 Agent 正在协作",
    follow_up_analysis: "正在基于当前岗位上下文回答",
    follow_up_ready: "追问已完成",
    follow_up_failed: "追问失败，原岗位上下文仍保留",
    building_recommendation: "正在生成投递与行动建议",
    completed: "分析已完成",
    rule_based_ready: "已完成基础证据对比",
    awaiting_resume: "等待选择简历版本",
    awaiting_job_details: "等待补充岗位信息",
    failed: "分析未完成",
  };
  return labels[store.activeTurn?.stage ?? ""] ?? "正在准备分析";
});

onMounted(async () => {
  await store.loadResumeVersions();
  const requestedResumeId = Number(route.query.resume);
  if (store.resumeVersions.some((resume) => resume.id === requestedResumeId)) {
    selectedResumeId.value = requestedResumeId;
    targetRole.value = store.resumeVersions.find((resume) => resume.id === requestedResumeId)?.target_role ?? "";
  } else if (store.resumeVersions.length === 1) {
    selectedResumeId.value = store.resumeVersions[0].id;
    targetRole.value = store.resumeVersions[0].target_role;
  }
});

async function createSession() {
  await store.startSession(selectedResumeId.value, targetRole.value);
}

async function submit() {
  await store.submit(message.value);
  message.value = "";
}

async function decide(artifactId: number, decision: "accept" | "reject" | "ask" | "create_task") {
  store.errorMessage = "";
  store.noticeMessage = "";
  store.isLoading = true;
  try {
    if (decision === "create_task") {
      const reportId = store.activeTurn?.report_id;
      const actionBundle = newestArtifacts.value.find((artifact) => artifact.artifact_type === "action_bundle");
      const missingSkills = Array.isArray(actionBundle?.payload.missing_skills)
        ? actionBundle.payload.missing_skills.filter((skill): skill is string => typeof skill === "string")
        : [];
      if (!reportId) throw new Error("当前分析尚未生成可创建行动的报告，请稍后重试");
      if (missingSkills.length === 0) throw new Error("当前没有需要补强的技能缺口");
      const createdItems = await createActionItemsFromReport(reportId, missingSkills);
      store.noticeMessage = `已加入 ${createdItems.length} 项成长任务，可在成长计划中提交成果证据。`;
    }
    await store.decide(artifactId, decision);
  } catch (error) {
    store.errorMessage = error instanceof Error ? error.message : "保存决定失败";
  } finally {
    store.isLoading = false;
  }
}
</script>

<template>
  <div class="workspace">
    <header class="page-heading">
      <div>
        <p class="eyebrow">AI 求职副驾</p>
        <h1>从岗位判断到下一步行动</h1>
      </div>
      <p>粘贴完整岗位 JD，副驾会让多个 Agent 分工分析，再把结论沉淀为行动。</p>
    </header>

    <section v-if="!hasSession" class="intake-panel">
      <h2>先建立本次求职上下文</h2>
      <div class="intake-grid">
        <label>
          当前简历版本
          <select v-model="selectedResumeId">
            <option :value="undefined">暂不选择</option>
            <option v-for="resume in store.resumeVersions" :key="resume.id" :value="resume.id">
              {{ resume.version_name }} · {{ resume.target_role || "未设置方向" }}
            </option>
          </select>
        </label>
        <label>
          目标岗位方向
          <input v-model="targetRole" placeholder="例如：Python 后端开发实习" />
        </label>
      </div>
      <p v-if="store.resumeVersions.length === 0" class="helper-text">还没有已确认的简历版本。请先在简历中心导入并确认一份简历。</p>
      <button :disabled="store.isLoading || !selectedResumeId" @click="createSession">开始与副驾协作</button>
    </section>

    <template v-else>
      <section class="conversation-panel">
        <p v-if="store.hasActiveContext" class="context-banner">
          当前上下文：{{ store.session?.target_role || "当前岗位" }} · 已关联简历和分析报告，可直接继续追问
        </p>
        <p v-else class="context-banner pending-context">
          当前上下文：{{ store.session?.target_role || "当前岗位" }} · 发送完整 JD 后会生成可持续追问的分析报告
        </p>
        <div class="message-list" aria-live="polite">
          <article v-for="item in store.session?.messages" :key="item.id" :class="['message', item.role]">
            <span>{{ item.role === "user" ? "你" : "副驾" }}</span>
            <p>{{ item.content }}</p>
          </article>
          <p v-if="store.isAnalyzing" class="analysis-status">正在理解岗位、核对简历证据并制定建议...</p>
        </div>
        <div v-if="store.isAnalyzing && store.activeTurn" class="progress-panel">
          <div><strong>{{ stageLabel }}</strong><span>{{ store.activeTurn.progress }}%</span></div>
          <progress :value="store.activeTurn.progress" max="100" />
          <p>你可以先阅读已返回的岗位理解卡，分析完成后会自动补全证据和建议。</p>
        </div>
        <form class="composer" @submit.prevent="submit">
          <textarea v-model="message" :disabled="store.isAnalyzing" :placeholder="store.hasActiveContext ? '继续追问当前岗位，例如：我还需要补强哪些能力？' : '粘贴完整岗位职责和任职要求...'" />
          <button type="submit" :disabled="store.isAnalyzing || message.trim().length < 2">{{ store.isAnalyzing ? "分析中" : "开始分析" }}</button>
        </form>
      </section>

      <p v-if="store.errorMessage" class="error-message">{{ store.errorMessage }}</p>
      <p v-if="store.noticeMessage" class="success-message">{{ store.noticeMessage }}</p>
      <ArtifactCards :artifacts="newestArtifacts" :analysis-mode="analysisMode" :busy="store.isLoading" @decide="decide" />
    </template>
  </div>
</template>
