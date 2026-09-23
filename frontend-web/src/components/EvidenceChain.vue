<script setup lang="ts">
import { computed, ref } from "vue";
import type { EvidenceChain, EvidenceFeedbackVerdict, EvidenceStatus } from "@/api/copilot";

const props = defineProps<{ chain: EvidenceChain | null; loading: boolean; error: string; }>();

const isUnavailable = computed(() => props.error.includes("证据链不存在") || props.error.includes("未生成可审阅证据链"));

const emit = defineEmits<{
  review: [payload: {
    turnId: number;
    requirementId: string;
    verdict: EvidenceFeedbackVerdict;
    correctedStatus: EvidenceStatus | null;
    evidenceIds: string[];
    note: string;
  }];
}>();

const correctionRequirementId = ref<string | null>(null);
const correctionStatus = ref<EvidenceStatus>("partial");
const selectedEvidenceIds = ref<string[]>([]);
const reviewNote = ref("");

function quickReview(chain: EvidenceChain, requirementId: string, verdict: EvidenceFeedbackVerdict) {
  emit("review", {
    turnId: chain.turn_id,
    requirementId,
    verdict,
    correctedStatus: null,
    evidenceIds: [],
    note: "",
  });
}

function openCorrection(item: EvidenceChain["items"][number]) {
  correctionRequirementId.value = item.requirement.id;
  correctionStatus.value = item.decision?.status ?? "partial";
  selectedEvidenceIds.value = item.decision?.evidence_ids.filter((id) =>
    item.candidates.some((candidate) => candidate.id === id),
  ) ?? [];
  reviewNote.value = "";
}

function submitCorrection(chain: EvidenceChain) {
  if (!correctionRequirementId.value) return;
  emit("review", {
    turnId: chain.turn_id,
    requirementId: correctionRequirementId.value,
    verdict: "corrected",
    correctedStatus: correctionStatus.value,
    evidenceIds: correctionStatus.value === "missing_evidence" ? [] : selectedEvidenceIds.value,
    note: reviewNote.value.trim(),
  });
  correctionRequirementId.value = null;
}

function statusLabel(value: string): string {
  if (value === "supported") return "已有直接证据";
  if (value === "partial") return "语义相关，证据待补强";
  return "缺少简历证据";
}

function scoreLabel(value: number | null): string {
  return value === null ? "未计算" : value.toFixed(2);
}

function reviewLabel(verdict: EvidenceFeedbackVerdict, correctedStatus: EvidenceStatus | null): string {
  if (verdict === "confirmed") return "已确认系统判断";
  if (verdict === "rejected") return "已标记为错误";
  return `已修正为 ${statusLabel(correctedStatus ?? "partial")}`;
}
</script>

<template>
  <section class="artifact-section evidence-chain-section">
    <header class="artifact-heading">
      <div>
        <p class="eyebrow">可追溯证据链</p>
        <h2>岗位要求与简历片段</h2>
      </div>
      <span v-if="loading" class="evidence-loading">正在加载</span>
    </header>
    <p v-if="error" :class="isUnavailable ? 'evidence-chain-notice' : 'evidence-chain-error'">{{ isUnavailable ? "该历史分析未生成可审阅证据链。" : error }}</p>
    <p v-else-if="!loading && !chain" class="helper-text">分析完成后显示可核对的证据链。</p>
    <div v-else-if="chain" class="evidence-chain-list">
      <article v-for="item in chain.items" :key="item.requirement.id" class="evidence-chain-item">
        <div class="evidence-chain-heading">
          <div>
            <strong>{{ item.requirement.skill }}</strong>
            <p>{{ item.requirement.category === "preferred" ? "加分要求" : "核心要求" }} · {{ item.requirement.source_quote }}</p>
          </div>
          <span v-if="item.decision" :class="['evidence-status', item.decision.status]">
            {{ statusLabel(item.decision.status) }} · {{ Math.round(item.decision.confidence * 100) }}%
          </span>
        </div>
        <div class="evidence-review-actions">
          <button type="button" @click="quickReview(chain, item.requirement.id, 'confirmed')">确认判断</button>
          <button type="button" @click="quickReview(chain, item.requirement.id, 'rejected')">判断有误</button>
          <button type="button" @click="openCorrection(item)">修正</button>
        </div>
        <div v-if="item.candidates.length" class="evidence-candidate-list">
          <div v-for="candidate in item.candidates" :key="candidate.id" class="evidence-candidate">
            <p>{{ candidate.snippet }}</p>
            <small>TF-IDF {{ scoreLabel(candidate.lexical_score) }} · 重排 {{ scoreLabel(candidate.rerank_score) }}</small>
          </div>
        </div>
        <p v-else class="helper-text">没有召回到候选简历片段。</p>
        <p v-if="item.decision" class="evidence-rationale">裁决理由：{{ item.decision.rationale }}</p>
        <div v-if="correctionRequirementId === item.requirement.id" class="evidence-correction-form">
          <label>
            修正状态
            <select v-model="correctionStatus">
              <option value="supported">已有直接证据</option>
              <option value="partial">证据待补强</option>
              <option value="missing_evidence">缺少简历证据</option>
            </select>
          </label>
          <fieldset v-if="correctionStatus !== 'missing_evidence'">
            <legend>关联证据</legend>
            <label v-for="candidate in item.candidates" :key="candidate.id">
              <input v-model="selectedEvidenceIds" type="checkbox" :value="candidate.id" />
              {{ candidate.id }}
            </label>
          </fieldset>
          <textarea v-model="reviewNote" maxlength="300" placeholder="补充复核说明（可选）" />
          <button type="button" @click="submitCorrection(chain)">保存修正</button>
        </div>
        <div v-if="item.review" class="evidence-review-result">
          {{ reviewLabel(item.review.verdict, item.review.corrected_status) }}<span v-if="item.review.note">：{{ item.review.note }}</span>
        </div>
      </article>
    </div>
  </section>
</template>
