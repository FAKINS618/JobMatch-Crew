<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  createJobTarget,
  createMarketMatchTask,
  getReport,
  getMarketTask,
  listJobTargets,
  listReports,
  type MarketTask,
  type JobPost,
  type ReportDetail,
  type ReportSummary,
} from "@/api/workspace";
import { listResumeVersions, type ResumeVersion } from "@/api/copilot";

const reports = ref<ReportSummary[]>([]);
const selectedReport = ref<ReportDetail | null>(null);
const selectedPostId = ref<number | null>(null);
const addedPostIds = ref(new Set<string>());
const isLoading = ref(false);
const errorMessage = ref("");
const noticeMessage = ref("");
const resumeVersions = ref<ResumeVersion[]>([]);
const selectedResumeId = ref<number | null>(null);
const targetRole = ref("");
const city = ref("");
const marketTask = ref<MarketTask | null>(null);

const marketReports = computed(() => reports.value.filter((report) => report.job_post_count > 0));
const selectedPost = computed<JobPost | null>(() =>
  selectedReport.value?.job_posts.find((post) => post.id === selectedPostId.value) ?? null,
);

function recommendationFor(post: JobPost): { level: "A" | "B" | "C"; matchScore: number; reason: string } | null {
  if (!selectedReport.value?.parsed_result) return null;
  try {
    const parsed = JSON.parse(selectedReport.value.parsed_result) as { job_recommendations?: unknown };
    const recommendation = Array.isArray(parsed.job_recommendations)
      ? parsed.job_recommendations.find(
          (item): item is { url?: unknown; level?: unknown; match_score?: unknown; reason?: unknown } =>
            typeof item === "object" && item !== null && (item as { url?: unknown }).url === post.url,
        )
      : null;
    if (!recommendation || !["A", "B", "C"].includes(String(recommendation.level))) return null;
    return {
      level: recommendation.level as "A" | "B" | "C",
      matchScore: typeof recommendation.match_score === "number" ? recommendation.match_score : post.relevance_score,
      reason: typeof recommendation.reason === "string" ? recommendation.reason : "",
    };
  } catch {
    return null;
  }
}

function statusLabel(post: JobPost): string {
  if (post.status === "active") return "已确认可投";
  if (post.status === "likely_active") return "可能有效，需人工确认";
  if (post.status === "expired") return "已过期";
  return "有效性待确认";
}

async function loadReports() {
  isLoading.value = true;
  errorMessage.value = "";
  try {
    resumeVersions.value = await listResumeVersions();
    selectedResumeId.value = resumeVersions.value[0]?.id ?? null;
    targetRole.value = resumeVersions.value[0]?.target_role ?? "";
    const existingTargets = await listJobTargets();
    addedPostIds.value = new Set(existingTargets.map((target) => target.url));
    reports.value = (await listReports()).reports;
    if (marketReports.value.length > 0) await openReport(marketReports.value[0].id);
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "读取岗位收件箱失败";
  } finally {
    isLoading.value = false;
  }
}

async function startMarketSearch() {
  const resume = resumeVersions.value.find((version) => version.id === selectedResumeId.value);
  if (!resume) {
    errorMessage.value = "请先在简历中心保存一份简历版本";
    return;
  }
  if (targetRole.value.trim().length < 2) {
    errorMessage.value = "请输入目标岗位方向";
    return;
  }
  isLoading.value = true;
  errorMessage.value = "";
  noticeMessage.value = "市场搜索已启动，完成后会自动进入岗位收件箱。";
  try {
    const createdTask = await createMarketMatchTask({
      resume_text: resume.raw_text,
      target_role: targetRole.value.trim(),
      city: city.value.trim(),
      max_results: 8,
      resume_version_id: resume.id,
    });
    const taskId = createdTask.task_id;
    marketTask.value = {
      id: taskId,
      task_type: "market_match",
      status: createdTask.status,
      progress: 0,
      report_id: null,
      error_message: "",
    };
    for (let attempt = 0; attempt < 180; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 700));
      marketTask.value = await getMarketTask(taskId);
      if (marketTask.value.status === "success") {
        await loadReports();
        noticeMessage.value = "市场岗位已更新，请审阅岗位依据后再加入投递管道。";
        return;
      }
      if (marketTask.value.status === "failed") {
        throw new Error(marketTask.value.error_message || "市场分析失败");
      }
    }
    noticeMessage.value = "市场分析仍在运行，稍后刷新收件箱即可查看结果。";
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "启动市场分析失败";
  } finally {
    isLoading.value = false;
  }
}

async function openReport(reportId: number) {
  isLoading.value = true;
  errorMessage.value = "";
  noticeMessage.value = "";
  try {
    selectedReport.value = await getReport(reportId);
    selectedPostId.value = selectedReport.value.job_posts[0]?.id ?? null;
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "读取岗位报告失败";
  } finally {
    isLoading.value = false;
  }
}

async function addToPipeline(post: JobPost) {
  const recommendation = recommendationFor(post);
  if (!selectedReport.value || !recommendation || post.status !== "active") return;
  selectedPostId.value = post.id;
  errorMessage.value = "";
  noticeMessage.value = "";
  try {
    await createJobTarget({ report_id: selectedReport.value.id, url: post.url, priority: recommendation.level });
    addedPostIds.value = new Set([...addedPostIds.value, post.url]);
    noticeMessage.value = "已加入投递管道，可继续记录投递进度。";
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "加入投递管道失败";
  }
}

onMounted(loadReports);
</script>

<template>
  <div class="workspace">
    <header class="page-heading">
      <div><p class="eyebrow">岗位收件箱</p><h1>先审阅，再决定是否投递</h1></div>
      <p>岗位来自市场分析报告。系统保留来源、有效性和匹配依据，不自动投递。</p>
    </header>
    <section class="artifact-section inbox-search">
      <div class="artifact-heading"><div><p class="eyebrow">岗位搜索</p><h2>让副驾先找一批值得审阅的岗位</h2></div><span v-if="marketTask" class="helper-text">{{ marketTask.status }} · {{ marketTask.progress }}%</span></div>
      <div class="intake-grid">
        <label>用于搜索的简历<select v-model="selectedResumeId"><option :value="null">请选择简历版本</option><option v-for="resume in resumeVersions" :key="resume.id" :value="resume.id">{{ resume.version_name }}</option></select></label>
        <label>目标岗位方向<input v-model="targetRole" placeholder="例如：AI 应用开发实习" /></label>
        <label>城市（可选）<input v-model="city" placeholder="例如：上海" /></label>
      </div>
      <button :disabled="isLoading || !selectedResumeId" @click="startMarketSearch">{{ isLoading ? "市场分析中" : "搜索并更新岗位" }}</button>
      <progress v-if="marketTask && ['pending', 'running'].includes(marketTask.status)" :value="marketTask.progress" max="100" />
    </section>
    <p v-if="errorMessage" class="error-message">{{ errorMessage }}</p>
    <p v-if="noticeMessage" class="success-message">{{ noticeMessage }}</p>
    <section v-if="isLoading && !selectedReport" class="artifact-section"><p>正在读取岗位报告...</p></section>
    <section v-else-if="marketReports.length === 0" class="artifact-section">
      <h2>还没有市场岗位</h2>
      <p>先在 Streamlit 工作台完成一次市场岗位分析，岗位样本会自动进入这里。</p>
    </section>
    <div v-else class="inbox-layout">
      <aside class="artifact-section report-list">
        <h2>市场分析记录</h2>
        <button
          v-for="report in marketReports"
          :key="report.id"
          class="report-list-item"
          :class="{ selected: selectedReport?.id === report.id }"
          @click="openReport(report.id)"
        >
          <strong>{{ report.target_role }}</strong>
          <span>{{ report.job_post_count }} 个岗位 · {{ report.created_at_local || report.created_at }}</span>
        </button>
      </aside>
      <section v-if="selectedReport" class="artifact-section inbox-results">
        <header class="artifact-heading"><div><p class="eyebrow">{{ selectedReport.target_role }}</p><h2>岗位样本</h2></div><strong>{{ selectedReport.job_posts.length }} 个</strong></header>
        <article v-for="post in selectedReport.job_posts" :key="post.id" class="job-post-row" :class="{ selected: selectedPostId === post.id }" @click="selectedPostId = post.id">
          <div><strong>{{ post.title || "未命名岗位" }}</strong><p>{{ post.company || "公司待确认" }} · {{ statusLabel(post) }}</p></div>
          <div class="job-post-meta"><span v-if="recommendationFor(post)" class="priority-badge">{{ recommendationFor(post)?.level }} 类</span><span>{{ recommendationFor(post)?.matchScore ?? post.relevance_score }} 分</span></div>
        </article>
      </section>
      <section v-if="selectedPost" class="artifact-section job-detail">
        <header class="artifact-heading"><div><p class="eyebrow">岗位详情</p><h2>{{ selectedPost.title || "未命名岗位" }}</h2><p>{{ selectedPost.company || "公司待确认" }}</p></div><a :href="selectedPost.url" target="_blank" rel="noreferrer">打开原链接</a></header>
        <p>{{ selectedPost.content || "暂无完整岗位描述，请打开原链接核实。" }}</p>
        <p v-if="recommendationFor(selectedPost)?.reason" class="next-question">{{ recommendationFor(selectedPost)?.reason }}</p>
        <div class="decision-row"><span class="helper-text">{{ statusLabel(selectedPost) }} · {{ selectedPost.verification_reason || "来源信息已保留" }}</span><button :disabled="selectedPost.status !== 'active' || !recommendationFor(selectedPost) || addedPostIds.has(selectedPost.url)" @click="addToPipeline(selectedPost)">{{ addedPostIds.has(selectedPost.url) ? "已在投递管道" : "加入投递管道" }}</button></div>
      </section>
    </div>
  </div>
</template>
