<script setup lang="ts">
import type { EvidenceChain } from "@/api/copilot";

defineProps<{
  chain: EvidenceChain | null;
  loading: boolean;
  error: string;
}>();

function statusLabel(value: string): string {
  if (value === "supported") return "已有直接证据";
  if (value === "partial") return "语义相关，证据待补强";
  return "缺少简历证据";
}

function scoreLabel(value: number | null): string {
  return value === null ? "未计算" : value.toFixed(2);
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
    <p v-if="error" class="evidence-chain-error">{{ error }}</p>
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
        <div v-if="item.candidates.length" class="evidence-candidate-list">
          <div v-for="candidate in item.candidates" :key="candidate.id" class="evidence-candidate">
            <p>{{ candidate.snippet }}</p>
            <small>TF-IDF {{ scoreLabel(candidate.lexical_score) }} · 重排 {{ scoreLabel(candidate.rerank_score) }}</small>
          </div>
        </div>
        <p v-else class="helper-text">没有召回到候选简历片段。</p>
        <p v-if="item.decision" class="evidence-rationale">裁决理由：{{ item.decision.rationale }}</p>
      </article>
    </div>
  </section>
</template>

