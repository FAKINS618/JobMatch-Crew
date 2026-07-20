"""Pluggable lexical retrieval and deterministic evidence reranking."""

import re
from typing import Protocol

from app.rag.resume_retriever import requirement_query, retrieve_resume_chunks
from app.schemas.agent_pipeline import (
    JDRequirement,
    ResumeChunk,
    ResumeChunkCandidate,
)


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

    def retrieve(
        self, requirement: JDRequirement, resume_text: str, top_k: int = 8
    ) -> list[ResumeChunkCandidate]:
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
        requirement_text = requirement.text.casefold()
        requirement_keywords = _keywords(requirement_text)
        project_sections = {"项目经历", "项目经验", "实习经历", "工作经历"}
        skill_sections = {"专业技能", "技能", "技能概览"}
        scored: list[ResumeChunkCandidate] = []
        for candidate in candidates:
            content = candidate.chunk.content.casefold()
            section = candidate.chunk.section
            bonus = 0.0
            if requirement.skill.casefold() in content:
                bonus += 0.50
            if any(keyword in content for keyword in requirement_keywords):
                bonus += 0.20
            if section in project_sections:
                bonus += 0.15
            elif section in skill_sections:
                bonus += 0.10
            rerank_score = max(0.0, min(1.0, candidate.lexical_score + bonus))
            scored.append(candidate.model_copy(update={"rerank_score": rerank_score}))
        scored.sort(key=lambda item: (-float(item.rerank_score or 0), item.chunk.id))
        return scored[:top_k]

