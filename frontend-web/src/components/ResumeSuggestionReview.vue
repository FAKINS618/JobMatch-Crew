<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink } from "vue-router";
import {
  createResumeVersionFromSuggestions,
  listResumeSuggestions,
  updateResumeSuggestion,
  type ResumeProfile,
  type ResumeSuggestion,
} from "@/api/workspace";

const props = defineProps<{
  reportId: number;
  sourceResumeVersionId: number;
  targetRole: string;
  sourceRawText: string;
  sourceProfile: ResumeProfile;
}>();
const emit = defineEmits<{ saved: [] }>();
const suggestions = ref<ResumeSuggestion[]>([]);
const draftText = ref(props.sourceRawText);
const versionName = ref(`${props.targetRole || "求职"} 优化版`);
const loading = ref(false);
const saving = ref(false);
const bulkSaving = ref(false);
const createdVersionId = ref<number | null>(null);
const errorMessage = ref("");
const successMessage = ref("");

const pendingSuggestions = computed(() => suggestions.value.filter((item) => item.status === "pending"));
const confirmedSuggestions = computed(() => suggestions.value.filter((item) => ["accepted", "edited"].includes(item.status)));

async function load() {
  loading.value = true;
  errorMessage.value = "";
  try {
    suggestions.value = await listResumeSuggestions(props.reportId);
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "简历建议暂时无法加载";
  } finally {
    loading.value = false;
  }
}

async function changeSuggestion(item: ResumeSuggestion, status: ResumeSuggestion["status"]) {
  try {
    const updated = await updateResumeSuggestion(item.id, { status, edited_text: status === "edited" ? item.edited_text : "" });
    suggestions.value = suggestions.value.map((current) => current.id === updated.id ? updated : current);
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "简历建议保存失败";
  }
}

async function acceptAll() {
  if (!pendingSuggestions.value.length) return;
  bulkSaving.value = true;
  errorMessage.value = "";
  try {
    const updated = await Promise.all(
      pendingSuggestions.value.map((item) => updateResumeSuggestion(item.id, { status: "accepted", edited_text: "" })),
    );
    const updates = new Map(updated.map((item) => [item.id, item]));
    suggestions.value = suggestions.value.map((item) => updates.get(item.id) ?? item);
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "批量确认建议失败";
  } finally {
    bulkSaving.value = false;
  }
}

async function createVersion() {
  if (!versionName.value.trim() || draftText.value.trim().length < 80 || confirmedSuggestions.value.length === 0) return;
  saving.value = true;
  errorMessage.value = "";
  successMessage.value = "";
  try {
    const created = await createResumeVersionFromSuggestions({
      report_id: props.reportId,
      source_resume_version_id: props.sourceResumeVersionId,
      suggestion_ids: confirmedSuggestions.value.map((item) => item.id),
      version_name: versionName.value.trim(),
      target_role: props.targetRole,
      raw_text: draftText.value.trim(),
      profile: props.sourceProfile,
    });
    createdVersionId.value = created.id;
    successMessage.value = "已创建新的简历版本，原版本保持不变。";
    emit("saved");
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "新简历版本创建失败";
  } finally {
    saving.value = false;
  }
}

watch(() => props.reportId, load);
onMounted(load);
</script>

<template>
  <section class="suggestion-review">
    <div class="resume-row">
      <div><h2>简历建议确认</h2><p>先确认建议，再由你编辑完整简历文本；系统不会自动覆盖原版本。</p></div>
      <span class="helper-text">{{ confirmedSuggestions.length }} 条已确认 · {{ pendingSuggestions.length }} 条待处理</span>
    </div>
    <p v-if="loading" class="helper-text">正在加载建议...</p>
    <p v-if="errorMessage" class="error-message">{{ errorMessage }}</p>
    <p v-if="successMessage" class="success-message">{{ successMessage }}</p>
    <div v-if="pendingSuggestions.length" class="suggestion-toolbar">
      <span class="helper-text">处理完建议后，下一步是提交完整简历文本。</span>
      <button class="secondary" :disabled="bulkSaving" @click="acceptAll">{{ bulkSaving ? "正在确认" : "接受全部待处理建议" }}</button>
    </div>
    <article v-for="item in suggestions" :key="item.id" class="suggestion-item">
      <div>
        <strong>{{ item.suggestion_type === "resume_bullet" ? "简历 Bullet" : "简历优化建议" }}</strong>
        <span class="helper-text"> · {{ item.status === "pending" ? "待确认" : item.status === "accepted" ? "已接受" : item.status === "edited" ? "已编辑" : "已拒绝" }}</span>
        <p>{{ item.suggested_text }}</p>
      </div>
      <label class="helper-text">状态
        <select :value="item.status" @change="changeSuggestion(item, ($event.target as HTMLSelectElement).value as ResumeSuggestion['status'])">
          <option value="pending">待确认</option><option value="accepted">已接受</option><option value="edited">已编辑</option><option value="rejected">已拒绝</option>
        </select>
      </label>
      <div class="suggestion-actions">
        <button v-if="item.status === 'pending'" @click="changeSuggestion(item, 'accepted')">接受</button>
        <button v-if="item.status === 'pending'" class="secondary" @click="changeSuggestion(item, 'rejected')">拒绝</button>
        <button v-if="item.status !== 'rejected'" class="secondary" :disabled="!item.edited_text.trim()" @click="changeSuggestion(item, 'edited')">保存编辑</button>
      </div>
      <input v-if="item.status !== 'rejected'" v-model="item.edited_text" placeholder="填写修改后的建议内容" @change="changeSuggestion(item, 'edited')" />
    </article>
    <p v-if="!loading && suggestions.length === 0" class="helper-text">这份报告暂无可确认的简历建议。</p>
    <div v-if="confirmedSuggestions.length" class="confirmed-summary">
      <strong>已确认建议汇总</strong>
      <ul><li v-for="item in confirmedSuggestions" :key="item.id">{{ item.edited_text || item.suggested_text }}</li></ul>
    </div>
    <div v-if="confirmedSuggestions.length" class="suggestion-apply">
      <label>新版本名称<input v-model="versionName" /></label>
      <label>编辑完整简历文本<textarea v-model="draftText" /></label>
      <p class="helper-text">完整简历文本至少 80 个字符，系统不会自动拼接建议。</p>
      <button :disabled="saving || draftText.trim().length < 80" @click="createVersion">{{ saving ? "正在创建" : "创建确认后的新版本" }}</button>
      <p v-if="createdVersionId" class="action-links">
        <RouterLink class="link-button primary-action" :to="{ path: '/copilot', query: { resume: createdVersionId } }">使用新版本开始分析</RouterLink>
        <RouterLink class="link-button" to="/">返回工作台</RouterLink>
      </p>
    </div>
  </section>
</template>