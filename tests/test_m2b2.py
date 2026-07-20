from app.agent_pipeline.evidence_judge import retrieve_candidates, rule_judge
from app.agent_pipeline.evidence_matching import (
    contains_skill,
    has_negated_skill_evidence,
    has_positive_skill_evidence,
)
from app.agent_pipeline.retrieval import (
    HybridResumeRetriever,
    TfidfResumeRetriever,
    build_resume_retriever,
)
from app.config import Settings
from app.schemas.agent_pipeline import JDRequirement


def _requirement(skill: str = "Redis") -> JDRequirement:
    return JDRequirement(
        id=f"req-{skill.lower()}",
        text=skill,
        skill=skill,
        category="must",
        source_quote=f"要求 {skill}",
    )


def test_skill_matching_uses_boundaries():
    assert not contains_skill("JavaScript 工程", "Java")
    assert contains_skill("Java 服务", "Java")
    assert not contains_skill("Vuex 状态管理", "Vue")
    assert contains_skill("使用 C++/C# 开发", "C++")


def test_negated_skill_is_not_positive_evidence():
    assert has_negated_skill_evidence("没有直接使用 Redis 或 Docker", "Redis")
    assert not has_positive_skill_evidence("没有直接使用 Redis 或 Docker", "Redis")

    requirement = _requirement()
    candidates, _ = retrieve_candidates(
        "项目经历：没有直接使用 Redis，但完成了缓存设计和接口调试。",
        [requirement],
    )
    assert rule_judge([requirement], candidates)[0].status != "supported"


def test_positive_occurrence_overrides_nearby_negation():
    requirement = _requirement()
    candidates, _ = retrieve_candidates(
        "项目经历：没有直接使用 Redis。另一个项目中使用 Redis 完成缓存。",
        [requirement],
    )
    assert rule_judge([requirement], candidates)[0].status == "supported"


class FakeEmbeddingClient:
    def __init__(self, fail: bool = False):
        self.calls: list[list[str]] = []
        self.fail = fail

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        if self.fail:
            raise RuntimeError("timeout")
        return [[1.0, 0.0] for _ in texts]


def test_hybrid_fuses_candidates_and_caches_resume_chunks():
    client = FakeEmbeddingClient()
    retriever = HybridResumeRetriever(client)
    resume = "项目经历\n使用 Python 完成缓存服务和接口调试。"
    first = retriever.retrieve(_requirement(), resume, top_k=3)
    second = retriever.retrieve(_requirement("Python"), resume, top_k=3)
    assert first and all(item.fusion_score is not None for item in first)
    assert retriever.last_strategy == "hybrid"
    assert client.calls[0][0].startswith("使用 Python")
    assert len(client.calls) == 3
    assert second


def test_hybrid_embedding_failure_falls_back_to_tfidf():
    retriever = HybridResumeRetriever(FakeEmbeddingClient(fail=True))
    result = retriever.retrieve(
        _requirement(), "项目经历\n使用 Python 完成接口服务。", top_k=3
    )
    assert retriever.last_strategy == "tfidf_fallback"
    assert isinstance(result, list)


def test_embedding_disabled_never_builds_hybrid():
    retriever = build_resume_retriever(Settings(embedding_enabled=False))
    assert isinstance(retriever, TfidfResumeRetriever)
    assert retriever.last_strategy == "tfidf"


def test_embedding_enabled_without_endpoint_is_controlled_fallback():
    retriever = build_resume_retriever(
        Settings(embedding_enabled=True, embedding_model="", embedding_base_url="")
    )
    assert isinstance(retriever, TfidfResumeRetriever)
    assert retriever.last_strategy == "tfidf_fallback"
