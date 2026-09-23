<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  confirmInterviewActions,
  createApplicationEvent,
  createInterviewReview,
  getJobTargetTimeline,
  listJobTargets,
  updateJobTarget,
  type InterviewReview,
  type JobTarget,
  type JobTargetTimeline,
} from "@/api/workspace";
import { useRoute } from "vue-router";

const route = useRoute();
const targets = ref<JobTarget[]>([]);
const timelineById = ref<Record<number, JobTargetTimeline | undefined>>({});
const selectedTargetId = ref<number | null>(null);
const errorMessage = ref("");
const noticeMessage = ref("");
const eventNote = ref("");
const reviewRound = ref(1);
const reviewQuestions = ref("");
const reviewPerformance = ref<InterviewReview["performance"]>("mixed");
const reviewFeedback = ref("");
const reviewResult = ref<InterviewReview["result"]>("unknown");
const reviewMissingSkills = ref("");
const reviewConclusion = ref("");
const isSaving = ref(false);

const statusLabels: Record<JobTarget["status"], string> = {
  saved: "待投递", applied: "已投递", written_test: "笔试", interview: "面试",
  offer: "Offer", rejected: "未通过", withdrawn: "已撤回",
};
const nextStatuses: Record<JobTarget["status"], JobTarget["status"][]> = {
  saved: ["applied", "withdrawn"],
  applied: ["written_test", "interview", "offer", "rejected", "withdrawn"],
  written_test: ["interview", "offer", "rejected", "withdrawn"],
  interview: ["offer", "rejected", "withdrawn"],
  offer: [], rejected: [], withdrawn: [],
};
const selectedTarget = computed(() => targets.value.find((target) => target.id === selectedTargetId.value) ?? null);
const selectedTimeline = computed(() => selectedTarget.value ? timelineById.value[selectedTarget.value.id] : undefined);
const canReview = computed(() => ["interview", "offer", "rejected"].includes(selectedTarget.value?.status ?? "saved"));

async function loadTimeline(targetId: number) {
  timelineById.value = { ...timelineById.value, [targetId]: await getJobTargetTimeline(targetId) };
}

async function loadTargets() {
  errorMessage.value = "";
  try {
    targets.value = await listJobTargets();
    const requestedTargetId = Number(route.query.target);
    selectedTargetId.value = targets.value.some((target) => target.id === requestedTargetId)
      ? requestedTargetId
      : selectedTargetId.value ?? targets.value[0]?.id ?? null;
    await Promise.all(targets.value.map((target) => loadTimeline(target.id)));
  } catch (error) { errorMessage.value = error instanceof Error ? error.message : "读取投递管道失败"; }
}

async function changeStatus(target: JobTarget, event: Event) {
  const status = (event.target as HTMLSelectElement).value as JobTarget["status"];
  try {
    await updateJobTarget(target.id, status, target.note);
    await loadTargets();
    noticeMessage.value = `已更新为${statusLabels[status]}`;
  } catch (error) { errorMessage.value = error instanceof Error ? error.message : "更新投递状态失败"; }
}

async function addNote() {
  if (!selectedTarget.value || !eventNote.value.trim()) return;
  isSaving.value = true;
  try {
    await createApplicationEvent(selectedTarget.value.id, { event_type: "note", note: eventNote.value.trim() });
    eventNote.value = "";
    await loadTimeline(selectedTarget.value.id);
  } catch (error) { errorMessage.value = error instanceof Error ? error.message : "保存投递记录失败"; }
  finally { isSaving.value = false; }
}

function resetReviewDraft() {
  reviewRound.value = (selectedTimeline.value?.interview_reviews.length ?? 0) + 1;
  reviewQuestions.value = "";
  reviewPerformance.value = "mixed";
  reviewFeedback.value = "";
  reviewResult.value = "unknown";
  reviewMissingSkills.value = "";
  reviewConclusion.value = "";
}

async function saveReview() {
  if (!selectedTarget.value) return;
  isSaving.value = true;
  try {
    await createInterviewReview(selectedTarget.value.id, {
      round_number: reviewRound.value,
      questions: reviewQuestions.value.split("\n").map((item) => item.trim()).filter(Boolean),
      performance: reviewPerformance.value,
      feedback: reviewFeedback.value,
      result: reviewResult.value,
      missing_skills: reviewMissingSkills.value.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean),
      conclusion: reviewConclusion.value,
    });
    await loadTimeline(selectedTarget.value.id);
    resetReviewDraft();
    noticeMessage.value = "面试复盘已保存";
  } catch (error) { errorMessage.value = error instanceof Error ? error.message : "面试复盘保存失败"; }
  finally { isSaving.value = false; }
}

async function confirmActions(review: InterviewReview) {
  try {
    await confirmInterviewActions(review.id, review.missing_skills);
    await loadTimeline(review.job_target_id);
    noticeMessage.value = "已将确认的待补能力加入行动计划";
  } catch (error) { errorMessage.value = error instanceof Error ? error.message : "行动计划创建失败"; }
}

function selectTarget(target: JobTarget) {
  selectedTargetId.value = target.id;
  errorMessage.value = "";
  resetReviewDraft();
}

onMounted(loadTargets);
</script>

<template>
  <div class="workspace">
    <header class="page-heading"><div><p class="eyebrow">投递管道</p><h1>让每次投递都有后续</h1></div><p>记录真实投递、面试过程和复盘结果；系统不会替你自动投递。</p></header>
    <p v-if="errorMessage" class="error-message">{{ errorMessage }}</p>
    <p v-if="noticeMessage" class="success-message">{{ noticeMessage }}</p>
    <section v-if="targets.length === 0" class="artifact-section"><p>暂无投递目标。先从岗位收件箱加入确认有效的岗位。</p></section>
    <div v-else class="pipeline-layout">
      <section class="artifact-section pipeline-list">
        <h2>岗位列表</h2>
        <article v-for="target in targets" :key="target.id" class="pipeline-card" :class="{ selected: selectedTargetId === target.id }" @click="selectTarget(target)">
          <div class="artifact-heading"><div><p class="eyebrow">{{ target.priority }} 类 · 匹配 {{ target.match_score ?? "-" }}</p><h2>{{ target.title }}</h2><p>{{ target.company }}</p></div><span class="status-badge">{{ statusLabels[target.status] }}</span></div>
          <select :value="target.status" @click.stop @change="changeStatus(target, $event)"><option :value="target.status">{{ statusLabels[target.status] }}</option><option v-for="status in nextStatuses[target.status]" :key="status" :value="status">{{ statusLabels[status] }}</option></select>
        </article>
      </section>
      <section v-if="selectedTarget && selectedTimeline" class="artifact-section pipeline-detail">
        <header class="artifact-heading"><div><p class="eyebrow">投递时间线</p><h2>{{ selectedTarget.title }}</h2><p>{{ selectedTarget.company }} · {{ statusLabels[selectedTarget.status] }}</p></div><a :href="selectedTarget.url" target="_blank" rel="noreferrer">查看岗位</a></header>
        <p v-if="selectedTarget.deadline_at" class="helper-text">截止日期：{{ selectedTarget.deadline_at }}</p>
        <div class="timeline-list"><article v-for="event in selectedTimeline.events" :key="event.id" class="timeline-item"><time>{{ event.occurred_at }}</time><strong>{{ event.event_type }}</strong><p>{{ event.note || "无补充说明" }}</p></article></div>
        <div class="event-form"><label>补充投递记录<textarea v-model="eventNote" placeholder="例如：通过官网投递，等待笔试通知" /></label><button :disabled="isSaving || !eventNote.trim()" @click="addNote">保存记录</button></div>
        <section v-if="canReview" class="review-section">
          <div class="resume-row"><div><p class="eyebrow">面试复盘</p><h2>记录每一轮真实反馈</h2></div><span class="helper-text">{{ selectedTimeline.interview_reviews.length }} 轮</span></div>
          <article v-for="review in selectedTimeline.interview_reviews" :key="review.id" class="review-card"><div class="artifact-heading"><strong>第 {{ review.round_number }} 轮 · {{ review.result }}</strong><span>{{ review.performance }}</span></div><p>{{ review.conclusion || "暂无结论" }}</p><p v-if="review.feedback" class="helper-text">反馈：{{ review.feedback }}</p><p v-if="review.missing_skills.length" class="helper-text">待补能力：{{ review.missing_skills.join("、") }}</p><button v-if="review.missing_skills.length && !review.actions_confirmed_at" class="secondary" @click="confirmActions(review)">确认加入行动计划</button><span v-else-if="review.actions_confirmed_at" class="helper-text">已加入行动计划</span></article>
          <div class="review-form"><label>面试轮次<input v-model.number="reviewRound" type="number" min="1" /></label><label>面试问题（每行一题）<textarea v-model="reviewQuestions" /></label><label>表现<select v-model="reviewPerformance"><option value="strong">较好</option><option value="mixed">一般</option><option value="needs_work">需加强</option></select></label><label>结果<select v-model="reviewResult"><option value="unknown">未知</option><option value="pending">等待结果</option><option value="passed">通过</option><option value="failed">未通过</option></select></label><label>反馈<textarea v-model="reviewFeedback" /></label><label>待补能力（逗号或换行分隔）<input v-model="reviewMissingSkills" /></label><label>复盘结论<textarea v-model="reviewConclusion" /></label><button :disabled="isSaving" @click="saveReview">{{ isSaving ? "正在保存" : "保存面试复盘" }}</button></div>
        </section>
      </section>
    </div>
  </div>
</template>
