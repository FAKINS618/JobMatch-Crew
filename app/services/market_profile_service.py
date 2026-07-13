from datetime import date, timedelta
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from math import ceil
import re
from urllib.parse import urlsplit

from app.schemas import JobMarketProfile, JobPost, MarketDataQuality
from app.role_skill_repository import get_role_detail
from app.search_service import deduplicate_job_results, search_jobs

# 岗位画像服务：
# 负责把联网搜索到的原始岗位结果，清洗成可用于简历匹配分析的市场画像。

SKILL_KEYWORDS = [
    "Python",
    "FastAPI",
    "Flask",
    "Django",
    "MySQL",
    "PostgreSQL",
    "Redis",
    "Docker",
    "Linux",
    "Git",
    "RESTful API",
    "大模型 API",
    "Prompt",
    "CrewAI",
    "LangChain",
    "RAG",
    "向量数据库",
    "Vue",
    "React",
    "TypeScript",
    "Java",
    "Spring Boot",
    "MyBatis",
    "JVM",
    "JavaScript",
    "Vue3",
    "HTML",
    "CSS",
    "PyTorch",
    "Transformer",
    "机器学习",
    "深度学习",
    "SQL",
    "Spark",
    "Flink",
    "Hive",
    "Kafka",
    "Pytest",
    "Selenium",
    "Kubernetes",
    "Nginx",
    "CI/CD",
    "C++",
    "TCP/IP",
]


TOOL_KEYWORDS = [
    "Docker",
    "Git",
    "Linux",
    "Apifox",
    "Postman",
    "MySQL",
    "Redis",
    "Chroma",
    "Milvus",
]

# 这些能力会出现在大量计算机岗位页面中，但单独出现无法说明岗位和目标
# 方向高度相关，不应挤占 AI、后端、算法等方向的核心技能位置。
GENERIC_SKILLS = {"Git", "HTML", "CSS"}
RELEVANCE_THRESHOLD = 25

# 不同招聘网站对同一方向的命名差异很大。多查询只扩大“表达覆盖面”，
# 最终仍由相关性过滤和详情验证决定是否进入市场画像。
ROLE_SEARCH_ALIASES = {
    "AI 应用开发实习": ["大模型应用开发", "RAG 开发", "智能体开发"],
    "Python 后端开发实习": ["Python 后端开发", "FastAPI 后端开发"],
    "Java 后端开发实习": ["Java 后端开发", "Spring Boot 开发"],
    "前端开发实习": ["前端开发", "Vue 前端开发"],
    "算法工程实习": ["算法工程师", "机器学习算法"],
    "数据开发实习": ["数据开发", "大数据开发"],
    "测试开发实习": ["测试开发", "自动化测试"],
    "运维云原生实习": ["云原生运维", "DevOps"],
    "嵌入式软件实习": ["嵌入式开发", "嵌入式软件"],
}

EXPIRED_KEYWORDS = [
    "已下线",
    "停止招聘",
    "职位已关闭",
    "岗位已关闭",
    "招满",
    "已结束",
    "过期",
    "页面不存在",
    "404",
]


def build_market_profile(
    target_role: str,
    city: str = "",
    max_results: int = 8,
) -> tuple[JobMarketProfile, list[JobPost]]:
    """搜索真实岗位，并从岗位内容中提取高频技能和要求。

    返回二元组：
    - JobMarketProfile：给前端和 LLM 使用的聚合画像；
    - list[JobPost]：保留原始岗位样本，后续写入 job_posts 表用于追溯。
    """
    role_detail = get_role_detail(target_role) or {}
    role_core_skills = role_detail.get("core_skills", [])
    queries = build_market_queries(target_role=target_role, city=city)
    per_query_results = max(3, ceil(max_results / len(queries)))
    raw_results = []
    for query in queries:
        raw_results.extend(search_jobs(query=query, max_results=per_query_results))
    raw_results = deduplicate_job_results(raw_results)[:max_results]

    posts = []
    for item in raw_results:
        text = f"{item.get('title') or ''}\n{item.get('content') or ''}"
        published_at, deadline_at = extract_job_dates(text)
        post = JobPost(
            title=item.get("title") or "",
            url=item.get("url") or "",
            content=item.get("content") or "",
            source=item.get("source") or "",
            published_at=published_at,
            deadline_at=deadline_at,
        )

        post.status, post.invalid_reason = detect_job_status(post)
        post.freshness_score = calc_freshness_score(post)

        post.relevance_score = calculate_job_relevance(
            post=post,
            target_role=target_role,
            role_core_skills=role_core_skills,
        )
        posts.append(post)

    _verify_relevant_posts(
        posts=posts,
        target_role=target_role,
        role_core_skills=role_core_skills,
    )

    # 趋势画像允许 relevant 的 unknown / likely_active 样本参与，但投递推荐
    # 只使用 active。这样既不浪费有价值的市场趋势，也不伪造可投岗位。
    relevant_posts = [
        post for post in posts if post.relevance_score >= RELEVANCE_THRESHOLD
    ]
    usable_posts = [post for post in relevant_posts if is_usable_post(post)]
    role_core_skill_keys = {skill.casefold() for skill in role_core_skills}
    skill_keywords = list(dict.fromkeys([*SKILL_KEYWORDS, *role_core_skills]))

    core_skill_counter = Counter()
    secondary_skill_counter = Counter()
    tool_counter = Counter()
    responsibilities = []
    project_requirements = []
    source_urls = []

    for post in usable_posts:
        text = f"{post.title}\n{post.content}"
        lower_text = text.lower()

        for skill in skill_keywords:
            if skill.lower() in lower_text:
                if skill.casefold() in role_core_skill_keys:
                    core_skill_counter[skill] += post.freshness_score
                elif skill not in GENERIC_SKILLS:
                    secondary_skill_counter[skill] += post.freshness_score

        for tool in TOOL_KEYWORDS:
            if tool.lower() in lower_text:
                tool_counter[tool] += post.freshness_score

        if post.content:
            responsibilities.append(post.content[:160])

        if any(word in text for word in ["项目", "经验", "开发", "系统"]):
            project_requirements.append(post.content[:160])

        if post.url:
            source_urls.append(post.url)

    frequent_skills = [item for item, _ in core_skill_counter.most_common(10)]
    if len(frequent_skills) < 10:
        frequent_skills.extend(
            item
            for item, _ in secondary_skill_counter.most_common(
                10 - len(frequent_skills)
            )
            if item not in frequent_skills
        )

    active_posts = [post for post in relevant_posts if post.status == "active"]
    likely_active_posts = [
        post for post in relevant_posts if post.status == "likely_active"
    ]
    unknown_posts = [post for post in relevant_posts if post.status == "unknown"]
    domains = {
        urlsplit(post.url).netloc.lower()
        for post in active_posts
        if post.url
    }

    if len(active_posts) >= 5 and len(domains) >= 2:
        quality = MarketDataQuality(
            level="high",
            active_job_count=len(active_posts),
            source_domain_count=len(domains),
            message="有效岗位样本充足，可作为投递决策参考。",
        )
    elif len(active_posts) >= 3:
        quality = MarketDataQuality(
            level="medium",
            active_job_count=len(active_posts),
            source_domain_count=len(domains),
            message="岗位样本有限，建议结合岗位原链接确认后投递。",
        )
    else:
        quality = MarketDataQuality(
            level="low",
            active_job_count=len(active_posts),
            source_domain_count=len(domains),
            message="有效岗位样本不足，仅展示技能趋势，不生成投递结论。",
        )


    market_profile = JobMarketProfile(
        target_role=target_role,
        sample_count=len(posts),
        valid_count=len(active_posts),
        likely_active_count=len(likely_active_posts),
        relevant_count=len(relevant_posts),
        expired_count=len([post for post in posts if post.status == "expired"]),
        unknown_count=len(unknown_posts),
        freshness_level=calc_freshness_level(usable_posts),
        frequent_skills=frequent_skills,
        frequent_tools=[item for item, _ in tool_counter.most_common(8)],
        project_requirements=project_requirements[:5],
        common_responsibilities=responsibilities[:5],
        source_urls=source_urls[:10],
        source_domain_count=len(domains),
        data_quality=quality,
    )

    return market_profile, posts


def build_market_queries(target_role: str, city: str = "") -> list[str]:
    """为一个目标方向生成少量差异化召回查询，避免只依赖单一岗位命名。"""
    terms = [target_role, *ROLE_SEARCH_ALIASES.get(target_role, [])]
    queries = []
    for term in terms[:3]:
        query = f"{term} 实习 招聘 {city}".strip()
        if query not in queries:
            queries.append(query)
    return queries or [f"{target_role} 实习 招聘 {city}".strip()]


def calculate_job_relevance(
    post: JobPost,
    target_role: str,
    role_core_skills: list[str],
) -> float:
    """使用可解释规则过滤偏离目标方向的候选岗位。"""
    title = post.title.casefold()
    content = post.content.casefold()
    score = 0.0
    role_terms = [target_role, *ROLE_SEARCH_ALIASES.get(target_role, [])]

    for term in role_terms:
        normalized_term = term.casefold()
        if normalized_term in title:
            score += 35
        elif normalized_term in content:
            score += 15

    for skill in role_core_skills:
        normalized_skill = skill.casefold()
        if normalized_skill in title:
            score += 15
        elif normalized_skill in content:
            score += 8

    # AI 方向常被通用 Java 后端结果污染。只有未出现 AI 核心词时才降权，
    # 避免错杀使用 Java 调用模型服务的真实岗位。
    ai_terms = ("大模型", "llm", "rag", "智能体", "agent", "prompt")
    if "AI 应用" in target_role and "java" in title and not any(
        term in f"{title} {content}" for term in ai_terms
    ):
        score -= 25

    return max(0, min(round(score, 2), 100))


def _verify_relevant_posts(
    posts: list[JobPost],
    target_role: str,
    role_core_skills: list[str],
) -> None:
    """并发验证少量相关候选岗位，避免详情页超时顺序累加。"""
    candidate_indexes = [
        index
        for index, post in enumerate(posts)
        if post.relevance_score >= RELEVANCE_THRESHOLD and post.status != "expired"
    ]
    if not candidate_indexes:
        return

    # 延迟导入避免验证器与本模块的日期规则形成导入环。
    from app.services.job_verification_service import verify_job_post

    with ThreadPoolExecutor(max_workers=min(4, len(candidate_indexes))) as executor:
        future_indexes = {
            executor.submit(verify_job_post, posts[index]): index
            for index in candidate_indexes
        }
        for future in as_completed(future_indexes):
            index = future_indexes[future]
            try:
                verified_post = future.result()
            except Exception as exc:  # 验证失败不可中断整个市场分析。
                posts[index] = posts[index].model_copy(
                    update={
                        "verification_status": "unavailable",
                        "verification_reason": f"详情页验证异常：{type(exc).__name__}",
                    }
                )
                continue

            posts[index] = verified_post.model_copy(
                update={
                    "relevance_score": calculate_job_relevance(
                        post=verified_post,
                        target_role=target_role,
                        role_core_skills=role_core_skills,
                    )
                }
            )


def detect_job_status(post: JobPost) -> tuple[str, str]:
    """用可解释的后端规则判断岗位是否仍可作为投递依据。"""
    text = f"{post.title}\n{post.content}"
    today = date.today()

    for keyword in EXPIRED_KEYWORDS:
        if keyword in text:
            return "expired", f"命中过期关键词：{keyword}"

    if post.deadline_at and post.deadline_at < today:
        return "expired", "投递截止日期已过"

    if post.published_at:
        if post.published_at > today:
            return "unknown", "发布时间晚于当前日期，无法确认"
        if (today - post.published_at).days <= 90:
            return "active", ""
        return "unknown", "发布时间超过 90 天，时效待确认"

    return "unknown", "未提取到发布时间"


def calc_freshness_score(post: JobPost) -> float:
    """根据发布时间和截止时间计算岗位时效分。"""
    today = date.today()

    if post.deadline_at and post.deadline_at < today:
        return 0.0

    if post.published_at is None:
        return 0.5

    days = (today - post.published_at).days

    if days < 0:
        return 0.0

    if days <= 7:
        return 1.0
    if days <= 30:
        return 0.8
    if days <= 90:
        return 0.4

    return 0.0


def calc_freshness_level(posts: list[JobPost]) -> str:
    """把多个岗位的平均时效分转换成内部等级。

    该值反映搜索摘要的时间线索，不代表岗位数据质量；前端投递判断应读取
    data_quality，不能将未知发布时间的 0.5 分显示为“中等可信度”。
    """
    if not posts:
        return "low"

    avg_score = sum(post.freshness_score for post in posts) / len(posts)

    if avg_score >= 0.8:
        return "high"
    if avg_score >= 0.5:
        return "medium"

    return "low"


def is_usable_post(post: JobPost) -> bool:
    """判断岗位是否参与市场画像统计。"""
    if post.status == "expired":
        return False

    if post.freshness_score <= 0:
        return False

    return True


def has_sufficient_market_data(profile: JobMarketProfile) -> bool:
    """判断市场画像是否足以支撑“匹配评分”。

    只有满足最小样本量、稳定的高频技能和中等以上数据质量时，市场评分
    才具有解释意义。未知时效的搜索摘要可以作为趋势参考，不能伪装成
    可靠的投递结论。
    """
    return (
        profile.valid_count >= 3
        and len(profile.frequent_skills) >= 3
        and profile.data_quality is not None
        and profile.data_quality.level in {"high", "medium"}
    )


def has_sufficient_trend_data(profile: JobMarketProfile) -> bool:
    """趋势分析可使用相关但尚未验证日期的岗位样本。"""
    return profile.relevant_count >= 3 and len(profile.frequent_skills) >= 3

def extract_job_dates(text: str) -> tuple[date | None, date | None]:
    """从岗位文本中提取发布时间和截止时间。

    支持常见的中文和数字日期：
    - 发布于 2026-07-10
    - 3 天前
    - 截止时间 2026-08-01

    这个函数的价值是把“岗位是否新鲜”的判断从 Prompt 中拿出来，
    交给后端规则处理，避免 LLM 主观判断过期信息。
    """
    clean_text = " ".join(text.split())
    if not clean_text:
        return None, None

    published_at = _extract_labeled_date(
        clean_text,
        labels=("发布时间", "发布日期", "发布于", "发表于", "发布", "更新于"),
    )
    deadline_at = _extract_labeled_date(
        clean_text,
        labels=("投递截止时间", "投递截止", "申请截止时间", "申请截止", "截止日期", "截止时间", "截止"),
    )

    # 搜索引擎摘要中常见“3 天前”“昨天发布”。相对时间只用于发布时间，
    # 不会误写为投递截止时间。
    if published_at is None:
        published_at = _extract_relative_publish_date(clean_text)

    return published_at, deadline_at


_DATE_PATTERN = re.compile(
    r"(?P<year>20\d{2})\s*(?:[-/.年])\s*(?P<month>1[0-2]|0?[1-9])\s*(?:[-/.月])\s*(?P<day>3[01]|[12]\d|0?[1-9])\s*日?"
)
_MONTH_DAY_PATTERN = re.compile(
    r"(?P<month>1[0-2]|0?[1-9])\s*(?:[-/.月])\s*(?P<day>3[01]|[12]\d|0?[1-9])\s*日?"
)


def _extract_labeled_date(text: str, labels: tuple[str, ...]) -> date | None:
    """提取标签后紧邻的日期，避免将岗位描述中的无关日期当成发布时间。"""
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?:{label_pattern})[^0-9]{{0,16}}", text, flags=re.IGNORECASE)
    if match is None:
        return None

    # 仅检查标签后的有限文本，降低“发布时间 ... 截止日期 ...”串行误匹配概率。
    candidate = text[match.end() : match.end() + 16]
    return _parse_date(candidate)


def _parse_date(value: str) -> date | None:
    """解析完整日期或不含年份的月日；非法日期直接忽略。"""
    today = date.today()
    match = _DATE_PATTERN.search(value)
    if match:
        try:
            return date(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
            )
        except ValueError:
            return None

    match = _MONTH_DAY_PATTERN.search(value)
    if match is None:
        return None

    try:
        parsed = date(today.year, int(match.group("month")), int(match.group("day")))
    except ValueError:
        return None

    # 缺少年份时，若日期明显晚于今天，按上一年度理解（例如 1 月看到的 12/31）。
    if parsed > today + timedelta(days=31):
        return parsed.replace(year=parsed.year - 1)
    return parsed


def _extract_relative_publish_date(text: str) -> date | None:
    """解析“今天发布”“3 天前发布”等相对发布时间。"""
    if not re.search(r"(?:发布|更新)", text):
        return None

    today = date.today()
    if "刚刚" in text or "今天" in text or "今日" in text:
        return today
    if "昨天" in text:
        return today - timedelta(days=1)

    match = re.search(r"(?P<value>\d{1,3})\s*(?P<unit>天|日|小时|周|星期)前", text)
    if match is None:
        return None

    amount = int(match.group("value"))
    unit = match.group("unit")
    if unit == "小时":
        return today
    if unit in {"周", "星期"}:
        amount *= 7
    return today - timedelta(days=amount)
