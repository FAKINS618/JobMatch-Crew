import pytest

from app.schemas import JobPost, MarketResumeMatchAnalysis, ScoreDimension

# 测 Schema 校验
def test_score_dimension_rejects_score_greater_than_max():
    with pytest.raises(ValueError):
        ScoreDimension(name="Python", score=30, max_score=20)


def test_score_dimension_accepts_valid_score():
    item = ScoreDimension(name="FastAPI", score=15, max_score=20)

    assert item.score == 15


def test_job_post_default_values():
    post = JobPost(title="Python 后端实习")

    assert post.status == "unknown"
    assert post.freshness_score == 0.5
    assert post.fetched_at is not None


def test_job_post_rejects_invalid_freshness_score():
    with pytest.raises(ValueError):
        JobPost(title="测试岗位", freshness_score=1.5)


def test_market_analysis_allows_missing_score_when_data_is_insufficient():
    analysis = MarketResumeMatchAnalysis(
        score=None,
        summary="市场样本不足，本次结果只可作为技能趋势参考。",
    )

    assert analysis.score is None
