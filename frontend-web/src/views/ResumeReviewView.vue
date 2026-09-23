<script setup lang="ts">
import { onMounted, ref } from "vue";
import { RouterLink, useRoute } from "vue-router";
import { listResumeVersions, type ResumeVersion } from "@/api/copilot";
import { getResumeAnalysisHistory, type ResumeAnalysisHistory } from "@/api/workspace";
import ResumeSuggestionReview from "@/components/ResumeSuggestionReview.vue";

const route = useRoute();
const resume = ref<ResumeVersion | null>(null);
const report = ref<ResumeAnalysisHistory["reports"][number] | null>(null);
const isLoading = ref(true);
const errorMessage = ref("");

onMounted(async () => {
  try {
    const resumeId = Number(route.query.resume);
    const reportId = Number(route.query.report);
    if (!resumeId || !reportId) throw new Error("缺少简历或报告上下文");
    const versions = await listResumeVersions();
    resume.value = versions.find((item) => item.id === resumeId) ?? null;
    if (!resume.value) throw new Error("来源简历版本不存在");
    const history = await getResumeAnalysisHistory(resumeId);
    report.value = history.reports.find((item) => item.id === reportId) ?? null;
    if (!report.value) throw new Error("该报告不属于当前简历版本");
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "简历建议审阅页加载失败";
  } finally {
    isLoading.value = false;
  }
});
</script>

<template>
  <div class="workspace">
    <header class="page-heading">
      <div>
        <p class="eyebrow">简历建议审阅</p>
        <h1>确认建议，再创建新版本</h1>
      </div>
      <RouterLink class="link-button" to="/">返回工作台</RouterLink>
    </header>
    <section v-if="isLoading" class="artifact-section"><p>正在加载报告建议...</p></section>
    <section v-else-if="errorMessage" class="artifact-section">
      <p class="error-message">{{ errorMessage }}</p>
      <RouterLink class="link-button" to="/resumes">返回简历中心</RouterLink>
    </section>
    <template v-else-if="resume && report">
      <section class="artifact-section review-context">
        <p class="eyebrow">{{ resume.version_name }} · {{ report.target_role || resume.target_role || "当前岗位" }}</p>
        <h2>报告建议</h2>
        <p>每条建议都需要你的明确决定。创建新版本时必须提交完整简历文本，原版本不会被覆盖。</p>
      </section>
      <ResumeSuggestionReview
        :report-id="report.id"
        :source-resume-version-id="resume.id"
        :target-role="report.target_role || resume.target_role"
        :source-raw-text="resume.raw_text"
        :source-profile="resume.profile"
      />
    </template>
  </div>
</template>