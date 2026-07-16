from datetime import date

from app.schemas import JobPost
from app.services.job_verification_service import verify_job_post


class FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        return None


def test_verify_job_post_reads_jobposting_json_ld(monkeypatch):
    html = f"""
    <script type="application/ld+json">
    {{
      "@context": "https://schema.org",
      "@type": "JobPosting",
      "title": "AI 应用开发实习",
      "datePosted": "{date.today().isoformat()}",
      "validThrough": "2099-12-31",
      "hiringOrganization": {{"name": "示例公司"}},
      "description": "需要 Python FastAPI RAG"
    }}
    </script>
    """
    monkeypatch.setattr(
        "app.services.job_verification_service.requests.get",
        lambda *_args, **_kwargs: FakeResponse(html),
    )

    verified = verify_job_post(JobPost(title="候选岗位", url="https://example.com/job"))

    assert verified.status == "active"
    assert verified.company == "示例公司"
    assert verified.verification_status == "verified"
    assert verified.published_at == date.today()
