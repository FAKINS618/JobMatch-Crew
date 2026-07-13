from crewai import Agent, Crew, Process, Task
from app.rag.retriever import format_retrieved_knowledge,  retrieve_knowledge
from app.llm_factory import build_llm
from app.prompt_loader import load_prompt
from app.role_skill_repository import format_role_skill_map_for_prompt
from app.schemas import JobMatchAnalysis

def run_jobmatch_crew(resume_text: str, jd_text: str, target_role: str) -> tuple[JobMatchAnalysis | None, str]:
    """运行多 Agent 分析流程。

    当前 DeepSeek/OpenAI-compatible 接口不支持 CrewAI 的 response_format
    结构化输出参数，所以这里不使用 output_pydantic。Agent 先返回原始文本，
    后续由 service 层提取 JSON 并用 Pydantic 在本地校验。
    """
    llm = build_llm()

    # 4 个 Agent 分别负责一个清晰的子任务，避免把所有要求塞进单个 Prompt。
    jd_analyst = Agent(
        role="AI 应用开发岗位分析专家",
        goal="从岗位 JD 中提取岗位职责、必备技能、加分技能和面试考点",
        backstory=load_prompt("jd_analyst.md"),
        llm=llm,
        verbose=True,
    )

    resume_analyst = Agent(
        role="技术简历分析专家",
        goal="分析候选人简历中的技能、项目经历、工程能力和短板",
        backstory=load_prompt("resume_analyst.md"),
        llm=llm,
        verbose=True,
    )

    match_scorer = Agent(
        role="招聘匹配评分专家",
        goal="根据 JD 和简历内容进行客观评分，并指出证据和缺口",
        backstory=load_prompt("match_scorer.md"),
        llm=llm,
        verbose=True,
    )

    report_writer = Agent(
        role="求职分析报告专家",
        goal="综合分析结果，生成 Markdown 求职匹配报告",
        backstory=load_prompt("report_writer.md"),
        llm=llm,
        verbose=True,
    )

    role_skill_map = format_role_skill_map_for_prompt()

    # 前三个任务产出中间分析，最后一个任务汇总成 JobMatchAnalysis 风格的 JSON 文本。
    jd_task = Task(
        description=f"""
            目标岗位：{target_role}

            岗位技能图谱参考：
            {role_skill_map}

            请分析下面岗位 JD：

            {jd_text}
        """,
        expected_output="结构化岗位分析结果",
        agent=jd_analyst,
    )

    resume_task = Task(
        description=f"""
        请分析下面候选人简历：

        {resume_text}
        """,
        expected_output="结构化简历能力分析结果",
        agent=resume_analyst,
    )

    score_task = Task(
        description="""
        结合岗位分析和简历分析，完成匹配评分。

        输出要求：
        1. 总分，满分 100
        2. 分项得分
        3. 匹配证据
        4. 缺失能力
        5. 优先补强建议
        """,
        expected_output="匹配评分和证据分析",
        agent=match_scorer,
        context=[jd_task, resume_task],
    )

    rag_query = f"""
    目标岗位：{target_role}

    岗位 JD：
    {jd_text[:1500]}

    候选人简历：
    {resume_text[:1500]}
    """

    retrieved_chunks = retrieve_knowledge(rag_query, top_k=5)
    interview_knowledge = format_retrieved_knowledge(retrieved_chunks)


    report_task = Task(
        description=f"""
        根据前面所有结果，生成结构化求职分析结果。

        请参考以下面试题知识库生成更贴合的面试问题：

        {interview_knowledge}

        输出必须符合 JobMatchAnalysis 结构：
        1. 匹配总览 summary
        2. 匹配总分 score
        3. 已匹配技能 matched_skills
        4. 缺失技能 missing_skills
        5. 分项评分 score_dimensions
        6. 可写入简历的项目 bullet resume_bullets
        7. 面试高频问题 interview_questions
        8. 7 天补强计划 action_plan
        9. 风险点 risk_points
        """,
        expected_output="符合 JobMatchAnalysis schema 的结构化求职分析结果",
        agent=report_writer,
        context=[jd_task, resume_task, score_task],
    )

    # 顺序执行能保证 report_task 一定拿到前面三个任务的上下文。
    crew = Crew(
        agents=[jd_analyst, resume_analyst, match_scorer, report_writer],
        tasks=[jd_task, resume_task, score_task, report_task],
        process=Process.sequential,
        verbose=True,
    )

    result = crew.kickoff()

    # analysis 暂时返回 None，表示结构化校验交给 service 层完成。
    raw_result = str(result)
    return None, raw_result

