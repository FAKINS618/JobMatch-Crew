<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import {
  getMarketSearchPreference,
  getResumeAnalysisHistory,
  parseResume,
  saveResumeVersion,
  updateMarketSearchPreference,
  type ResumeAnalysisHistory,
  type ResumeMarketSearchPreference,
  type ResumeProfile,
} from "@/api/workspace";
import { useCopilotStore } from "@/stores/copilot";
import ResumeSuggestionReview from "@/components/ResumeSuggestionReview.vue";

const store = useCopilotStore();
const expandedResumeId = ref<number | null>(null);
const rawText = ref("");
const versionName = ref("");
const targetRole = ref("");
const draftProfile = ref<ResumeProfile | null>(null);
const isParsing = ref(false);
const isSaving = ref(false);
const errorMessage = ref("");
const successMessage = ref("");
const canSave = computed(() => Boolean(draftProfile.value && versionName.value.trim()));
const historyByResume = ref<Record<number, ResumeAnalysisHistory | undefined>>({});
const preferenceByResume = ref<Record<number, ResumeMarketSearchPreference | undefined>>({});
const historyLoading = ref<number | null>(null);
const historyError = ref("");

onMounted(() => store.loadResumeVersions());

async function parseDraft() {
  isParsing.value = true;
  errorMessage.value = "";
  successMessage.value = "";
  try {
    draftProfile.value = (await parseResume(rawText.value.trim())).profile;
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "简历解析失败";
  } finally {
    isParsing.value = false;
  }
}

async function saveDraft() {
  if (!draftProfile.value) return;
  isSaving.value = true;
  errorMessage.value = "";
  try {
    await saveResumeVersion({
      version_name: versionName.value.trim(),
      target_role: targetRole.value.trim(),
      raw_text: rawText.value.trim(),
      profile: draftProfile.value,
    });
    await store.loadResumeVersions();
    successMessage.value = "简历版本已保存，可立即用于副驾分析。";
    draftProfile.value = null;
    versionName.value = "";
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "保存简历版本失败";
  } finally {
    isSaving.value = false;
  }
}

async function toggleHistory(resumeId: number) {
  expandedResumeId.value = expandedResumeId.value === resumeId ? null : resumeId;
  if (expandedResumeId.value !== resumeId || historyByResume.value[resumeId]) return;
  historyLoading.value = resumeId;
  historyError.value = "";
  try {
    const [history, preference] = await Promise.all([
      getResumeAnalysisHistory(resumeId),
      getMarketSearchPreference(resumeId),
    ]);
    historyByResume.value = { ...historyByResume.value, [resumeId]: history };
    preferenceByResume.value = { ...preferenceByResume.value, [resumeId]: preference };
  } catch (error) {
    historyError.value = error instanceof Error ? error.message : "分析历史暂时无法加载";
  } finally {
    historyLoading.value = null;
  }
}

async function updateAutoSearch(resumeId: number, enabled: boolean) {
  const current = preferenceByResume.value[resumeId];
  if (!current) return;
  try {
    const preference = await updateMarketSearchPreference(resumeId, {
      auto_search_enabled: enabled,
      city: current.city,
    });
    preferenceByResume.value = { ...preferenceByResume.value, [resumeId]: preference };
    const history = historyByResume.value[resumeId];
    if (history) history.market_search_preference = preference;
  } catch (error) {
    historyError.value = error instanceof Error ? error.message : "自动搜索偏好保存失败";
  }
}

async function updateSearchCity(resumeId: number, city: string) {
  const current = preferenceByResume.value[resumeId];
  if (!current) return;
  try {
    const preference = await updateMarketSearchPreference(resumeId, {
      auto_search_enabled: current.auto_search_enabled,
      city,
    });
    preferenceByResume.value = { ...preferenceByResume.value, [resumeId]: preference };
  } catch (error) {
    historyError.value = error instanceof Error ? error.message : "搜索城市保存失败";
  }
}

function onAutoSearchChange(resumeId: number, event: Event) {
  updateAutoSearch(resumeId, (event.target as HTMLInputElement).checked);
}

function onCityChange(resumeId: number, event: Event) {
  updateSearchCity(resumeId, (event.target as HTMLInputElement).value);
}

function artifactText(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  return typeof value === "string" ? value : "";
}
</script>

<template>
  <div class="workspace">
    <header class="page-heading">
      <div>
        <p class="eyebrow">简历中心</p>
        <h1>选择有证据的简历版本</h1>
      </div>
      <p>分析会记录所使用的版本，避免不同版本的结果混在一起。</p>
    </header>
    <section class="artifact-section resume-import">
      <p class="eyebrow">导入简历</p>
      <h2>先由 AI 提取，再由你确认保存</h2>
      <textarea v-model="rawText" placeholder="粘贴不少于 80 字的简历内容..." />
      <div class="resume-actions">
        <button :disabled="isParsing || rawText.trim().length < 80" @click="parseDraft">
          {{ isParsing ? "正在解析" : "提取简历信息" }}
        </button>
      </div>
      <p v-if="errorMessage" class="error-message">{{ errorMessage }}</p>
      <p v-if="successMessage" class="success-message">{{ successMessage }}</p>
      <div v-if="draftProfile" class="resume-detail draft-profile">
        <p class="eyebrow">待确认档案</p>
        <section><h2>技能</h2><p>{{ draftProfile.skills.join(" · ") || "未提取" }}</p></section>
        <section><h2>项目</h2><p v-if="draftProfile.projects.length === 0">未提取项目经历</p><ul v-else><li v-for="project in draftProfile.projects" :key="project.name"><strong>{{ project.name }}</strong><span>{{ project.technologies.join(" · ") }}</span><p>{{ project.description }}</p></li></ul></section>
        <div class="intake-grid">
          <label>版本名称<input v-model="versionName" placeholder="例如：Python 后端实习 v1" /></label>
          <label>适用岗位方向<input v-model="targetRole" placeholder="例如：Python 后端开发实习" /></label>
        </div>
        <button :disabled="isSaving || !canSave" @click="saveDraft">{{ isSaving ? "正在保存" : "确认并保存版本" }}</button>
      </div>
    </section>
    <section class="artifact-section">
      <p v-if="store.resumeVersions.length === 0" class="helper-text">暂未保存简历版本。简历导入与区块确认将在此页面继续补齐。</p>
      <article v-for="resume in store.resumeVersions" :key="resume.id" class="resume-card">
        <div class="resume-row">
          <div><strong>{{ resume.version_name }}</strong><p>{{ resume.target_role || "未设置岗位方向" }}</p></div>
          <time>{{ resume.created_at || "" }}</time>
        </div>
        <div class="resume-actions">
          <button class="secondary" @click="toggleHistory(resume.id)">
            {{ expandedResumeId === resume.id ? "收起详情" : "查看简历与分析历史" }}
          </button>
          <RouterLink class="link-button" :to="{ path: '/', query: { resume: resume.id } }">用此版本开始分析</RouterLink>
        </div>
        <div v-if="expandedResumeId === resume.id" class="resume-detail">
          <section><h2>技能</h2><p>{{ resume.profile.skills.join(" · ") || "未提取" }}</p></section>
          <section><h2>项目</h2><p v-if="resume.profile.projects.length === 0">未提取项目经历</p><ul v-else><li v-for="project in resume.profile.projects" :key="project.name"><strong>{{ project.name }}</strong><span>{{ project.technologies.join(" · ") }}</span><p>{{ project.description }}</p></li></ul></section>
          <details><summary>查看原始简历文本</summary><pre>{{ resume.raw_text }}</pre></details>
          <section class="analysis-history">
            <div class="resume-row"><h2>自动搜索岗位</h2><span v-if="historyLoading === resume.id">加载中...</span></div>
            <p v-if="historyError" class="error-message">{{ historyError }}</p>
            <template v-if="preferenceByResume[resume.id]">
              <label class="helper-text">
                <input
                  type="checkbox"
                  :checked="preferenceByResume[resume.id]?.auto_search_enabled"
                  :disabled="!resume.target_role"
                  @change="onAutoSearchChange(resume.id, $event)"
                />
                仅当副驾建议立即投递时自动搜索岗位
              </label>
              <label>搜索城市（可选）<input :value="preferenceByResume[resume.id]?.city" @change="onCityChange(resume.id, $event)" /></label>
              <p v-if="!resume.target_role" class="helper-text">请先设置目标岗位方向，自动搜索才可开启。</p>
            </template>
            <template v-if="historyByResume[resume.id]">
              <h2>副驾分析历史</h2>
              <article v-for="item in historyByResume[resume.id]?.sessions" :key="item.id" class="history-item">
                <strong>{{ item.target_role || resume.target_role || "当前岗位" }}</strong>
                <p>{{ item.messages[item.messages.length - 1]?.content || "暂无对话" }}</p>
                <div v-for="turn in item.turns" :key="turn.id" class="helper-text">
                  回合 {{ turn.id }} · {{ turn.status }} · {{ turn.progress }}% · 报告 {{ turn.report_id || "未生成" }}
                  <span v-for="artifact in turn.artifacts" :key="artifact.id">
                    · {{ artifact.artifact_type }}
                    <span v-if="artifact.artifact_type === 'job_brief' && artifactText(artifact.payload, 'summary')">：{{ artifactText(artifact.payload, 'summary') }}</span>
                    <span v-else-if="artifact.artifact_type === 'fit_strategy' && artifactText(artifact.payload, 'title')">：{{ artifactText(artifact.payload, 'title') }}</span>
                  </span>
                </div>
              </article>
              <h2 v-if="historyByResume[resume.id]?.reports.length">分析报告</h2>
              <article v-for="report in historyByResume[resume.id]?.reports" :key="report.id" class="history-item">
                <strong>{{ report.target_role }} · {{ report.score ?? "暂不评分" }}</strong>
                <p>{{ report.report_summary || "暂无结构化摘要" }}</p>
                <ResumeSuggestionReview
                  :report-id="report.id"
                  :source-resume-version-id="resume.id"
                  :target-role="report.target_role"
                  :source-raw-text="resume.raw_text"
                  :source-profile="resume.profile"
                  @saved="store.loadResumeVersions"
                />
              </article>
              <h2 v-if="historyByResume[resume.id]?.market_search_triggers.length">自动搜索记录</h2>
              <p v-for="trigger in historyByResume[resume.id]?.market_search_triggers" :key="trigger.id" class="helper-text">
                回合 {{ trigger.source_turn_id }} · {{ trigger.status }} · {{ trigger.city || "不限城市" }} · 任务 {{ trigger.analysis_task_id }}
                <RouterLink v-if="trigger.report_id" :to="{ path: '/inbox', query: { report: trigger.report_id } }">查看岗位报告</RouterLink>
              </p>
            </template>
          </section>
        </div>
      </article>
    </section>
  </div>
</template>
