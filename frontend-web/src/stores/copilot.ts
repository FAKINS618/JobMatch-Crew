import { defineStore } from "pinia";
import { computed, ref } from "vue";
import {
  createSession,
  decideArtifact,
  getSession,
  getTurn,
  listResumeVersions,
  sendMessage,
  type AnalysisTurn,
  type CopilotSession,
  type ResumeVersion,
} from "@/api/copilot";

export const useCopilotStore = defineStore("copilot", () => {
  const session = ref<CopilotSession | null>(null);
  const resumeVersions = ref<ResumeVersion[]>([]);
  const isLoading = ref(false);
  const errorMessage = ref("");
  const noticeMessage = ref("");
  const activeTurn = ref<AnalysisTurn | null>(null);
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
        return;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 700));
    }
    errorMessage.value = "基础结果已展示，深度分析仍在运行。稍后刷新页面可查看补充结果。";
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
    loadResumeVersions,
    startSession,
    submit,
    decide,
  };
});
