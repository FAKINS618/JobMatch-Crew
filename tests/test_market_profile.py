from datetime import date, timedelta

from app.schemas import JobMarketProfile, JobPost, MarketDataQuality
from app.services.market_profile_service import (
    build_market_profile,
    build_market_queries,
    calc_freshness_score,
    detect_job_status,
    extract_job_dates,
    has_sufficient_market_data,
)


def test_detect_expired_job_post():
    post = JobPost(title="Python 实习", content="该职位已关闭")

    status, reason = detect_job_status(post)

    assert status == "expired"
    assert "职位已关闭" in reason


def test_calc_freshness_score_for_recent_post():
    post = JobPost(
        title="Python 实习",
        published_at=date.today() - timedelta(days=3),
    )

    assert calc_freshness_score(post) == 1.0


def test_calc_freshness_score_for_expired_deadline():
    post = JobPost(
        title="Python 实习",
        deadline_at=date.today() - timedelta(days=1),
    )

    assert calc_freshness_score(post) == 0.0


def test_build_market_profile_filters_expired_jobs(monkeypatch):
    def fake_search_jobs(query: str, max_results: int):
        return [
            {
                "title": "Python 后端实习",
                "url": "https://example.com/1",
                "content": "需要 Python FastAPI Docker 项目开发经验",
                "source": "test",
            },
            {
                "title": "已关闭岗位",
                "url": "https://example.com/2",
                "content": "该职位已关闭，需要 Java",
                "source": "test",
            },
        ]

    monkeypatch.setattr(
        "app.services.market_profile_service.search_jobs",
        fake_search_jobs,
    )
    monkeypatch.setattr(
        "app.services.job_verification_service.verify_job_post",
        lambda post: post,
    )

    profile, posts = build_market_profile(
        target_role="Python 后端开发实习",
        city="北京",
        max_results=2,
    )

    assert profile.sample_count == 2
    assert profile.expired_count == 1
    assert profile.unknown_count == 1
    assert "Python" in profile.frequent_skills
    assert "FastAPI" in profile.frequent_skills
    assert len(posts) == 2


def test_market_profile_prioritizes_role_skills_over_generic_skills(monkeypatch):
    def fake_search_jobs(query: str, max_results: int):
        return [
            {
                "title": "AI 应用开发实习",
                "url": "https://example.com/ai",
                "content": "需要 Python、Git、HTML、RAG 和 FastAPI",
                "source": "test",
            }
        ]

    monkeypatch.setattr(
        "app.services.market_profile_service.search_jobs",
        fake_search_jobs,
    )
    monkeypatch.setattr(
        "app.services.job_verification_service.verify_job_post",
        lambda post: post,
    )

    profile, _ = build_market_profile(
        target_role="AI 应用开发实习",
        max_results=1,
    )

    assert "Python" in profile.frequent_skills
    assert "FastAPI" in profile.frequent_skills
    assert "RAG" in profile.frequent_skills
    assert "Git" not in profile.frequent_skills
    assert "HTML" not in profile.frequent_skills


def test_build_market_queries_includes_role_aliases():
    queries = build_market_queries("AI 应用开发实习", city="北京")

    assert len(queries) == 3
    assert any("大模型应用开发" in query for query in queries)
    assert all("北京" in query for query in queries)


def test_market_score_requires_valid_post_and_frequent_skills():
    insufficient_profile = JobMarketProfile(
        target_role="AI 应用开发实习",
        valid_count=0,
        frequent_skills=["Python"],
    )
    sufficient_profile = JobMarketProfile(
        target_role="AI 应用开发实习",
        valid_count=3,
        frequent_skills=["Python", "FastAPI", "Docker"],
        data_quality=MarketDataQuality(
            level="medium",
            active_job_count=3,
            source_domain_count=1,
        ),
    )

    assert has_sufficient_market_data(insufficient_profile) is False
    assert has_sufficient_market_data(sufficient_profile) is True


def test_extract_job_dates_from_labeled_dates():
    published_at, deadline_at = extract_job_dates(
        "发布时间：2026-07-10，投递截止时间：2026年08月01日"
    )

    assert published_at == date(2026, 7, 10)
    assert deadline_at == date(2026, 8, 1)


def test_extract_job_dates_from_relative_publish_time():
    published_at, deadline_at = extract_job_dates("Python 后端实习，3 天前发布")

    assert published_at == date.today() - timedelta(days=3)
    assert deadline_at is None


def test_old_post_is_not_confirmed_active():
    post = JobPost(title="旧岗位", published_at=date.today() - timedelta(days=100))

    status, reason = detect_job_status(post)

    assert status == "unknown"
    assert "90" in reason
