"""Pluggable TF-IDF/embedding retrieval and deterministic evidence reranking."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

from app.agent_pipeline.evidence_matching import contains_skill, has_positive_skill_evidence
from app.agent_pipeline.embeddings import (
    EmbeddingClient,
    EmbeddingUnavailable,
    OpenAICompatibleEmbeddingClient,
)
from app.config import settings
from app.rag.resume_retriever import (
    requirement_query,
    retrieve_resume_chunks,
    segment_resume,
)
from app.schemas.agent_pipeline import JDRequirement, ResumeChunk, ResumeChunkCandidate


class ResumeRetriever(Protocol):
    def retrieve(
        self, requirement: JDRequirement, resume_text: str, top_k: int
    ) -> list[ResumeChunkCandidate]: ...


class EvidenceReranker(Protocol):
    def rerank(
        self,
        requirement: JDRequirement,
        candidates: list[ResumeChunkCandidate],
        top_k: int,
    ) -> list[ResumeChunkCandidate]: ...


class TfidfResumeRetriever:
    """Adapt the existing standard-library TF-IDF retriever to M2 contracts."""

    def __init__(self, strategy: str = "tfidf") -> None:
        self.retrieval_strategy = strategy
        self.last_strategy = strategy
        self.embedding_available = False

    def retrieve(
        self, requirement: JDRequirement, resume_text: str, top_k: int = 8
    ) -> list[ResumeChunkCandidate]:
        self.last_strategy = self.retrieval_strategy
        chunks = retrieve_resume_chunks(
            resume_text,
            requirement_query(requirement.skill),
            top_k=top_k,
        )
        return [
            ResumeChunkCandidate(
                chunk=ResumeChunk(
                    id=chunk.chunk_id,
                    section=chunk.section,
                    content=chunk.content,
                ),
                lexical_score=max(0.0, min(float(chunk.score), 1.0)),
            )
            for chunk in chunks
        ]


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        raise EmbeddingUnavailable("embedding 向量维度不一致")
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    value = numerator / (left_norm * right_norm)
    if not math.isfinite(value):
        raise EmbeddingUnavailable("embedding 相似度无效")
    return max(0.0, min(1.0, value))


class HybridResumeRetriever:
    """Fuse TF-IDF and optional embeddings without changing the baseline."""

    def __init__(
        self,
        embedding_client: EmbeddingClient,
        *,
        lexical_retriever: TfidfResumeRetriever | None = None,
        fallback_retriever: TfidfResumeRetriever | None = None,
    ) -> None:
        self.embedding_client = embedding_client
        self.lexical_retriever = lexical_retriever or TfidfResumeRetriever()
        self.fallback_retriever = fallback_retriever or TfidfResumeRetriever(
            "tfidf_fallback"
        )
        self.retrieval_strategy = "hybrid"
        self.last_strategy = "hybrid"
        self.embedding_available = True
        self._chunk_embedding_cache: dict[str, tuple[list[ResumeChunk], list[list[float]]]] = {}

    def _cached_chunks(self, resume_text: str) -> tuple[list[ResumeChunk], list[list[float]]]:
        key = hashlib.sha256(resume_text.encode("utf-8")).hexdigest()
        cached = self._chunk_embedding_cache.get(key)
        if cached is not None:
            return cached
        raw_chunks = segment_resume(resume_text)
        chunks = [
            ResumeChunk(id=item.chunk_id, section=item.section, content=item.content)
            for item in raw_chunks
        ]
        vectors = self.embedding_client.embed([item.content for item in chunks])
        if len(vectors) != len(chunks) or not vectors:
            raise EmbeddingUnavailable("embedding chunk 数量不匹配")
        dimension = len(vectors[0])
        if dimension == 0 or any(len(vector) != dimension for vector in vectors):
            raise EmbeddingUnavailable("embedding chunk 维度不一致")
        cached = (chunks, vectors)
        self._chunk_embedding_cache[key] = cached
        return cached

    def retrieve(
        self, requirement: JDRequirement, resume_text: str, top_k: int = 8
    ) -> list[ResumeChunkCandidate]:
        lexical = self.lexical_retriever.retrieve(requirement, resume_text, top_k=8)
        lexical_by_id = {item.chunk.id: item for item in lexical}
        try:
            chunks, chunk_vectors = self._cached_chunks(resume_text)
            query_vector = self.embedding_client.embed([requirement.text])[0]
            if not query_vector:
                raise EmbeddingUnavailable("embedding requirement 向量为空")
            embedding_scores = {
                chunk.id: _cosine(query_vector, vector)
                for chunk, vector in zip(chunks, chunk_vectors)
            }
            embedding_ids = sorted(
                embedding_scores,
                key=lambda chunk_id: (-embedding_scores[chunk_id], chunk_id),
            )[:8]
            lexical_ids = [item.chunk.id for item in lexical]
            merged_ids = set(lexical_ids) | set(embedding_ids)
            lexical_rank = {chunk_id: index + 1 for index, chunk_id in enumerate(lexical_ids)}
            embedding_rank = {chunk_id: index + 1 for index, chunk_id in enumerate(embedding_ids)}
            chunk_by_id = {item.id: item for item in chunks}
            merged: list[ResumeChunkCandidate] = []
            for chunk_id in merged_ids:
                lexical_item = lexical_by_id.get(chunk_id)
                lexical_score = lexical_item.lexical_score if lexical_item else 0.0
                fusion_score = 0.0
                if chunk_id in lexical_rank:
                    fusion_score += 1 / (60 + lexical_rank[chunk_id])
                if chunk_id in embedding_rank:
                    fusion_score += 1 / (60 + embedding_rank[chunk_id])
                merged.append(
                    ResumeChunkCandidate(
                        chunk=lexical_item.chunk if lexical_item else chunk_by_id[chunk_id],
                        lexical_score=lexical_score,
                        embedding_score=embedding_scores.get(chunk_id),
                        fusion_score=max(0.0, min(1.0, fusion_score)),
                    )
                )
            self.last_strategy = "hybrid"
            self.embedding_available = True
            return sorted(
                merged,
                key=lambda item: (-float(item.fusion_score or 0), item.chunk.id),
            )[:top_k]
        except EmbeddingUnavailable:
            self.last_strategy = "tfidf_fallback"
            self.embedding_available = False
            return lexical[:top_k]
        except Exception:
            # Client implementations may raise transport-specific errors; the
            # externally visible contract remains a deterministic TF-IDF fallback.
            self.last_strategy = "tfidf_fallback"
            self.embedding_available = False
            return lexical[:top_k]


def build_resume_retriever(settings_obj=settings) -> ResumeRetriever:
    """Select retrieval explicitly from configuration; TF-IDF remains default."""
    if not settings_obj.embedding_enabled:
        return TfidfResumeRetriever()
    if not settings_obj.embedding_model.strip() or not settings_obj.embedding_base_url.strip():
        return TfidfResumeRetriever("tfidf_fallback")
    try:
        client = OpenAICompatibleEmbeddingClient(
            model=settings_obj.embedding_model,
            api_key=settings_obj.embedding_api_key,
            base_url=settings_obj.embedding_base_url,
            timeout_seconds=settings_obj.embedding_timeout_seconds,
        )
    except EmbeddingUnavailable:
        return TfidfResumeRetriever("tfidf_fallback")
    return HybridResumeRetriever(client)


def _keywords(text: str) -> list[str]:
    values = re.findall(r"[A-Za-z][A-Za-z0-9+#./-]*|[\u4e00-\u9fff]{2,}", text.casefold())
    return list(dict.fromkeys(values))


class RuleEvidenceReranker:
    """Apply transparent boosts without changing the final evidence judgement."""

    def rerank(
        self,
        requirement: JDRequirement,
        candidates: list[ResumeChunkCandidate],
        top_k: int = 3,
    ) -> list[ResumeChunkCandidate]:
        requirement_keywords = _keywords(requirement.text)
        project_sections = {"项目经历", "项目经验", "实习经历", "工作经历"}
        skill_sections = {"专业技能", "技能", "技能概览"}
        scored: list[ResumeChunkCandidate] = []
        for candidate in candidates:
            content = candidate.chunk.content
            section = candidate.chunk.section
            bonus = 0.0
            if has_positive_skill_evidence(content, requirement.skill):
                bonus += 0.50
            if any(contains_skill(content, keyword) for keyword in requirement_keywords):
                bonus += 0.20
            if section in project_sections:
                bonus += 0.15
            elif section in skill_sections:
                bonus += 0.10
            base_score = (
                candidate.fusion_score
                if candidate.fusion_score is not None
                else candidate.lexical_score
            )
            rerank_score = max(0.0, min(1.0, float(base_score) + bonus))
            scored.append(candidate.model_copy(update={"rerank_score": rerank_score}))
        scored.sort(key=lambda item: (-float(item.rerank_score or 0), item.chunk.id))
        return scored[:top_k]
