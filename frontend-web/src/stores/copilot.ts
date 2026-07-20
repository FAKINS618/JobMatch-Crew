import { defineStore } from "pinia";
import { computed, ref } from "vue";
import {
  createSession,
  decideArtifact,
  getSession,
  getTurn,
  getTurnEvidence,
  listResumeVersions,
  sendMessage,
  submitEvidenceFeedback,
  triggerMarketSearch,
  type AnalysisTurn,
  type CopilotSession,
  type EvidenceChain,
  type EvidenceFeedbackVerdict,
  type EvidenceStatus,
  type ResumeVersion,
} from "@/api/copilot";
import { ApiError } from "@/api/client";
import { getMarketSearchPreference } from "@/api/workspace";

export const useCopilotStore = defineStore("copilot", () => {
  const session = ref<CopilotSession | null>(null);
  const resumeVersions = ref<ResumeVersion[]>([]);
  const isLoading = ref(false);
  const errorMessage = ref("");
  const noticeMessage = ref("");
  const activeTurn = ref<AnalysisTurn | null>(null);
  const evidenceChain = ref<EvidenceChain | null>(null);
  const evidenceLoading = ref(false);
  const evidenceError = ref("");
  const isAnalyzing = computed(() => ["pending", "running"].includes(activeTurn.value?.status ?? ""));
  const hasActiveContext = computed(() => Boolean(session.value?.active_report_id));

  async function loadResumeVersions() {
    resumeVersions.value = await listResumeVersions();
  }

  async function startSession(resumeVersionId?: number, targetRole = "") {
    isLoading.value = true;
    errorMessage.value = "";
    noticeMessage.value = "";
    try {
      session.value = await createSession({ resume_version_id: resumeVersionId, target_role: targetRole });
      session.value.messages = [];
      session.value.turns = [];
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : "无法创建副驾会话";
    } finally {
      isLoading.value = false;
    }
  }

  async function submit(content: string) {
    if (!session.value || !content.trim()) return;
    isLoading.value = true;
    errorMessage.value = "";
    noticeMessage.value = "";
    try {
      activeTurn.value = await sendMessage(session.value.id, content.trim());
      evidenceChain.value = null;
      evidenceError.value = "";
      session.value = await getSession(session.value.id);
      void pollTurn(activeTurn.value.id);
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : "分析未能完成";
    } finally {
      isLoading.value = false;
    }
  }

  async function pollTurn(turnId: number) {
    for (let attempts = 0; attempts < 240; attempts += 1) {
      const turn = await getTurn(turnId);
      activeTurn.value = turn;
      if (turn.status === "failed") {
        errorMessage.value = turn.error_message || "分析未能完成，请稍后重试";
        session.value = await getSession(turn.session_id);
        return;
      }
      if (turn.status === "completed") {
        session.value = await getSession(turn.session_id);
        void loadEvidence(turn.id);
        void maybeTriggerMarketSearch(turn);
        return;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 700));
    }
    errorMessage.value = "基础结果已展示，深度分析仍在运行。稍后刷新页面可查看补充结果。";
  }

  async function maybeTriggerMarketSearch(turn: AnalysisTurn) {
    if (turn.input_type !== "initial_jd" || !session.value?.resume_version_id) return;
    const strategy = turn.artifacts.find((item) => item.artifact_type === "fit_strategy")?.payload;
    if (strategy?.recommendation !== "apply_now") return;
    try {
      const preference = await getMarketSearchPreference(session.value.resume_version_id);
      if (!preference.auto_search_enabled) return;
      await triggerMarketSearch(turn.id);
      noticeMessage.value = "已启动岗位搜索，结果将进入岗位收件箱。";
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        noticeMessage.value = "当前分析未满足自动搜索条件，未启动岗位搜索。";
        return;
      }
      // Automatic search is auxiliary; keep the completed Copilot result visible.
      if (!(error instanceof ApiError && error.status === 404)) {
        noticeMessage.value = "分析已完成，岗位搜索暂未启动，可在岗位收件箱手动搜索。";
      }
    }
  }

  async function loadEvidence(turnId: number) {
    evidenceLoading.value = true;
    evidenceError.value = "";
    try {
      evidenceChain.value = await getTurnEvidence(turnId);
    } catch (error) {
      evidenceChain.value = null;
      evidenceError.value = error instanceof Error ? error.message : "证据链暂时无法加载";
    } finally {
      evidenceLoading.value = false;
    }
  }

  async function reviewEvidence(
    turnId: number,
    requirementId: string,
    verdict: EvidenceFeedbackVerdict,
    correctedStatus: EvidenceStatus | null = null,
    evidenceIds: string[] = [],
    note = "",
  ) {
    await submitEvidenceFeedback(turnId, {
      requirement_id: requirementId,
      verdict,
      corrected_status: correctedStatus,
      evidence_ids: evidenceIds,
      note,
    });
    await loadEvidence(turnId);
  }

  async function decide(artifactId: number, decision: "accept" | "reject" | "ask" | "create_task") {
    await decideArtifact(artifactId, decision);
  }

  return {
    session,
    resumeVersions,
    isLoading,
    isAnalyzing,
    hasActiveContext,
    errorMessage,
    noticeMessage,
    activeTurn,
    evidenceChain,
    evidenceLoading,
    evidenceError,
    loadResumeVersions,
    loadEvidence,
    reviewEvidence,
    startSession,
    submit,
    decide,
  };
});
