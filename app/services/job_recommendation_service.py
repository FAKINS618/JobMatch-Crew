from app.schemas import JobPost, JobRecommendation, JobMarketProfile
from app.services.market_profile_service import SKILL_KEYWORDS
"""
    确定性岗位推荐服务
"""

def extract_skills(text: str, skills: list[str]) -> set[str]:
    """简单、可解释的技能提取。后续可替换为更精细的别名词典。"""
    lower_text = text.lower()
    return {
        skill for skill in skills
        if len(skill) > 1 and skill.lower() in lower_text
    }


def freshness_label(post: JobPost) -> str:

    if post.status != "active":
        return "时效待确认"
    if post.freshness_score >= 0.8:
        return "近期发布"
    return "近期可参考"


def build_job_recommendations(
    resume_text: str,
    posts: list[JobPost],
    profile: JobMarketProfile,
) -> list[JobRecommendation]:
    """只对已确认有效岗位分级，避免把趋势样本伪装成投递建议。"""
    candidate_skills = extract_skills(
        resume_text,
        list(dict.fromkeys([*SKILL_KEYWORDS, *profile.frequent_skills])),
    )

    recommendations = []

    for post in posts:
        if post.status != "active" or post.freshness_score <= 0:
            continue

        required_skills = extract_skills(
            f"{post.title}\n{post.content}",
            list(dict.fromkeys([*SKILL_KEYWORDS, *profile.frequent_skills])),
        )
        if not required_skills:
            continue

        matched = sorted(candidate_skills & required_skills)
        missing = sorted(required_skills - candidate_skills)
        coverage = len(matched) / len(required_skills)

        # 技能覆盖 70%，时效占 30%。这是规则分，不让 LLM 黑盒决定。
        score = round(coverage * 70 + post.freshness_score * 30)

        if coverage >= 0.7:
            level = "A"
            reason = "核心技能覆盖较完整，可优先投递。"
        elif coverage >= 0.4:
            level = "B"
            reason = "具备部分基础能力，补强关键缺口后建议投递。"
        else:
            level = "C"
            reason = "当前核心技能缺口较多，建议完成补强项目后再投递。"

        recommendations.append(
            JobRecommendation(
                title=post.title,
                company=post.company,
                url=post.url,
                level=level,
                match_score=score,
                matched_skills=matched,
                missing_skills=missing,
                reason=reason,
                freshness_label=freshness_label(post),
            )
        )

    return sorted(
        recommendations,
        key=lambda item: (
            {"A": 0, "B": 1, "C": 2}[item.level],
            -item.match_score,
        ),
    )[:10]


def build_trend_match(
    resume_text: str,
    profile: JobMarketProfile,
) -> tuple[int | None, list[str], list[str]]:
    """计算趋势适配度，不将其冒充为具体岗位的投递成功率。"""
    trend_skills = profile.frequent_skills
    if not trend_skills:
        return None, [], []

    matched_skills = sorted(extract_skills(resume_text, trend_skills))
    missing_skills = sorted(set(trend_skills) - set(matched_skills))
    coverage = len(matched_skills) / len(trend_skills)

    # 趋势分只由方向相关技能覆盖度组成，明确区别于岗位级 A/B/C 投递分级。
    return round(coverage * 100), matched_skills, missing_skills
