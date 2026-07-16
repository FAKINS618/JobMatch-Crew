from app.search_service import deduplicate_job_results, normalize_job_result


def test_normalize_job_result_removes_tracking_parameters_and_extra_whitespace():
    result = normalize_job_result(
        {
            "title": " Python   后端实习 ",
            "url": "https://Example.com/jobs/1/?from=search#detail",
            "content": "需要  Python\nFastAPI",
        },
        source="test",
    )

    assert result == {
        "title": "Python 后端实习",
        "url": "https://example.com/jobs/1",
        "content": "需要 Python FastAPI",
        "source": "test",
    }


def test_deduplicate_job_results_uses_canonical_url():
    results = deduplicate_job_results(
        [
            {
                "title": "Python 实习",
                "url": "https://example.com/jobs/1?utm=source",
                "content": "需要 FastAPI",
                "source": "source-a",
            },
            {
                "title": "Python 后端开发实习",
                "url": "https://example.com/jobs/1",
                "content": "需要 Python FastAPI Docker",
                "source": "source-b",
            },
        ]
    )

    assert len(results) == 1
    assert results[0]["source"] == "source-a"
