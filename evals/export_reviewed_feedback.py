"""Export reviewed evidence labels without exporting resume or JD text."""

import argparse
import json
import re
from pathlib import Path

from app import database
from evals.models import ReviewedFeedbackCandidate


_TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9+#./-]*|[\u4e00-\u9fff]{2,}")
_SENSITIVE_PATTERN = re.compile(
    r"(?:[\w.+-]+@[\w.-]+|https?://|\b1[3-9]\d{9}\b|\b\d{7,}\b)",
    re.IGNORECASE,
)
_STOPWORDS = {
    "项目经历",
    "项目经验",
    "简历正文",
    "使用",
    "完成",
    "负责",
    "实现",
    "开发",
    "支持",
    "相关",
}


def _candidate_keywords(snippets: list[str], requirement_skill: str) -> list[str]:
    values: list[str] = []
    for snippet in snippets:
        if _SENSITIVE_PATTERN.search(snippet):
            continue
        for token in _TOKEN_PATTERN.findall(snippet):
            normalized = token.strip()
            if normalized in _STOPWORDS or normalized.casefold() == requirement_skill.casefold():
                continue
            if normalized not in values:
                values.append(normalized)
            if len(values) >= 7:
                break
        if len(values) >= 7:
            break
    return [requirement_skill, *values][:8]


def export_reviewed_feedback(
    *, database_path: Path | None = None, output_path: Path | None = None
) -> list[ReviewedFeedbackCandidate]:
    previous_db_path = database.DB_PATH
    if database_path is not None:
        database.DB_PATH = database_path
    try:
        with database.sqlite3.connect(database.DB_PATH) as conn:
            turn_rows = conn.execute(
                """
                SELECT DISTINCT turn_id
                FROM evidence_feedback
                WHERE verdict IN ('corrected', 'rejected')
                ORDER BY turn_id
                """
            ).fetchall()
        exported: list[ReviewedFeedbackCandidate] = []
        for (turn_id,) in turn_rows:
            chain = database.get_analysis_evidence_chain(int(turn_id))
            if chain is None:
                continue
            for item in chain.get("items", []):
                review = item.get("review") or {}
                if review.get("verdict") not in {"corrected", "rejected"}:
                    continue
                requirement = item.get("requirement") or {}
                candidates = item.get("candidates") or []
                evidence_ids = set(review.get("evidence_ids") or [])
                snippets = [
                    candidate.get("snippet", "")
                    for candidate in candidates
                    if not evidence_ids or candidate.get("id") in evidence_ids
                ]
                verdict = review["verdict"]
                exported.append(
                    ReviewedFeedbackCandidate(
                        case_id=f"reviewed-turn-{turn_id}-{requirement.get('id', 'unknown')}",
                        requirement_skill=requirement.get("skill", ""),
                        expected_status=review.get("corrected_status"),
                        expected_chunk_keywords=_candidate_keywords(
                            snippets, requirement.get("skill", "")
                        ),
                        feedback_verdict=verdict,
                        feedback_at=review.get("created_at", ""),
                        needs_manual_label=verdict == "rejected",
                    )
                )
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(
                    {"cases": [item.model_dump() for item in exported]},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        return exported
    finally:
        database.DB_PATH = previous_db_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Export reviewed evidence labels safely")
    parser.add_argument("--database", type=Path, default=database.DB_PATH)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "results" / "reviewed_feedback_candidates.json",
    )
    args = parser.parse_args()
    exported = export_reviewed_feedback(
        database_path=args.database, output_path=args.output
    )
    print(f"exported_reviewed_candidates={len(exported)} output={args.output}")


if __name__ == "__main__":
    main()
