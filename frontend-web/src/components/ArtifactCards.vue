<script setup lang="ts">
import { computed } from "vue";
import type { Artifact } from "@/api/copilot";

const props = defineProps<{ artifacts: Artifact[]; busy: boolean; analysisMode: "rule" | "deep" }>();
const emit = defineEmits<{ decide: [artifactId: number, decision: "accept" | "reject" | "ask" | "create_task"] }>();

const jobBrief = computed(() => props.artifacts.find((item) => item.artifact_type === "job_brief"));
const evidenceMap = computed(() => props.artifacts.find((item) => item.artifact_type === "evidence_map"));
const strategy = computed(() => props.artifacts.find((item) => item.artifact_type === "fit_strategy"));
const actionBundle = computed(() => props.artifacts.find((item) => item.artifact_type === "action_bundle"));
const hasMissingSkills = computed(() =>
  stringList(actionBundle.value?.payload ?? {}, "missing_skills").length > 0,
);

function text(payload: Record<string, unknown>, key: string): string {
  return typeof payload[key] === "string" ? payload[key] : "";
}

function number(payload: Record<string, unknown>, key: string): number | null {
  return typeof payload[key] === "number" ? payload[key] : null;
}

function array(payload: Record<string, unknown>, key: string): Record<string, unknown>[] {
  return Array.isArray(payload[key])
    ? payload[key].filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null)
    : [];
}

function stringList(payload: Record<string, unknown>, key: string): string[] {
  return Array.isArray(payload[key])
    ? payload[key].filter((item): item is string => typeof item === "string")
    : [];
}

function statusLabel(value: string): string {
  if (value === "supported") return "已有直接证据";
  if (value === "partial") return "语义相关，证据待补强";
  return "缺少简历证据";
}

function confidenceLabel(payload: Record<string, unknown>): string {
  return typeof payload.confidence === "number"
    ? `可信度 ${Math.round(payload.confidence * 100)}%`
    : "可信度待确认";
}
</script>

<template>
  <section v-if="jobBrief" class="artifact-section">
    <header class="artifact-heading">
      <div>
        <p class="eyebrow">岗位理解</p>
        <h2>{{ text(jobBrief.payload, "title") }}</h2>
      </div>
      <strong v-if="number(jobBrief.payload, 'match_score') !== null" class="score">
        {{ number(jobBrief.payload, "match_score") }}<small>/100</small>
      </strong>
    </header>
    <p>{{ text(jobBrief.payload, "summary") }}</p>
    <p v-if="text(jobBrief.payload, 'next_question')" class="next-question">
      {{ text(jobBrief.payload, "next_question") }}
    </p>
  </section>

  <section v-if="strategy" class="artifact-section strategy">
    <p class="eyebrow">{{ analysisMode === "deep" ? "CrewAI 多 Agent 建议" : "基础匹配建议" }}</p>
    <h2>{{ text(strategy.payload, "title") }}</h2>
    <p>{{ text(strategy.payload, "reason") }}</p>
    <div class="decision-row">
      <button :disabled="busy" @click="emit('decide', strategy.id, 'accept')">采纳建议</button>
      <button v-if="hasMissingSkills" :disabled="busy" class="secondary" @click="emit('decide', strategy.id, 'create_task')">创建行动</button>
      <button :disabled="busy" class="text-button" @click="emit('decide', strategy.id, 'ask')">追问副驾</button>
    </div>
  </section>

  <section v-if="evidenceMap" class="artifact-section">
    <header class="artifact-heading">
      <div>
        <p class="eyebrow">岗位要求与简历证据</p>
        <h2>先看依据，再做决定</h2>
      </div>
    </header>
    <div v-for="item in array(evidenceMap.payload, 'items')" :key="text(item, 'requirement')" class="evidence-item">
      <div>
        <strong>{{ text(item, "requirement") }}</strong>
        <p>{{ text(item, "category") === "preferred" ? "加分要求" : "核心要求" }} · {{ text(item, "suggestion") }}</p>
      </div>
      <div class="evidence-detail">
        <span :class="text(item, 'status')">{{ statusLabel(text(item, "status")) }} · {{ confidenceLabel(item) }}</span>
        <p v-for="evidence in stringList(item, 'keyword_evidence')" :key="`keyword-${evidence}`">关键词证据：{{ evidence }}</p>
        <p v-for="evidence in stringList(item, 'semantic_evidence')" :key="`semantic-${evidence}`">语义证据：{{ evidence }}</p>
        <template v-if="stringList(item, 'keyword_evidence').length === 0 && stringList(item, 'semantic_evidence').length === 0">
          <p v-for="evidence in stringList(item, 'evidence')" :key="evidence">证据：{{ evidence }}</p>
        </template>
      </div>
    </div>
  </section>

  <section v-if="actionBundle" class="artifact-section">
    <p class="eyebrow">下一步行动</p>
    <h2>最小可执行改进</h2>
    <ul>
      <li v-for="skill in stringList(actionBundle.payload, 'missing_skills')" :key="skill">补强 {{ skill }}</li>
    </ul>
  </section>
</template>
