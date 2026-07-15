import streamlit as st

from api_client import fetch_role_detail, fetch_roles
from views import (
    render_action_plan,
    render_copilot_workspace,
    render_dashboard,
    render_explore_tab,
    render_job_pipeline,
    render_job_preferences,
    render_resume_center,
    render_reports_tab,
    render_workbench,
)
from ui_helpers import apply_global_style


def main() -> None:
    """Streamlit 前端入口，只负责全局配置、目标岗位和页面导航。"""
    st.set_page_config(
        page_title="CS JobMate",
        page_icon="🎯",
        layout="wide",
    )
    apply_global_style()

    try:
        roles = fetch_roles()
    except Exception as exc:
        st.error(f"无法连接后端服务，请先启动 FastAPI。错误：{exc}")
        st.stop()

    if not roles:
        st.error("后端未返回岗位方向，请检查 /api/roles 接口。")
        st.stop()

    # 把求职意向保存在会话状态中，侧栏只显示当前选择；真正的编辑入口
    # 位于主流程第一步，避免用户忽略岗位方向和目标城市。
    st.session_state.setdefault("selected_role_option", roles[0])
    st.session_state.setdefault("target_role", roles[0])
    st.session_state.setdefault("target_city", "北京")

    # 导航和分析配置都在左侧维护。历史报告不再与工作台并列成顶部 Tab，
    # 用户可以在任意时刻从左侧进入历史记录，而不会与当前分析结果混在一起。
    with st.sidebar:
        st.markdown("## CS JobMate")
        st.caption("求职决策工作台")
        st.markdown("### 工作区")
        page = st.radio(
            "工作区",
            ["AI 求职助手", "求职总览", "简历中心", "开始分析", "投递管道", "成长计划", "历史报告", "岗位探索"],
            label_visibility="collapsed",
            key="app_navigation",
        )
        st.divider()
        st.markdown("### 当前求职意向")
        selected_role = st.session_state.get("selected_role_option", roles[0])
        current_role = (
            st.session_state.get("custom_target_role") or "待填写自定义方向"
            if selected_role == "其他计算机岗位（自定义）"
            else selected_role
        )
        st.markdown(f"**{current_role}**")
        st.caption(f"目标城市：{st.session_state['target_city'] or '不限'}")
        st.caption("在“开始分析”或“岗位探索”中修改求职意向。")
        st.divider()
        st.success("FastAPI 服务已连接")

    st.title("CS JobMate")
    st.caption("计算机专业实习求职分析工作台")

    if page == "AI 求职助手":
        render_copilot_workspace(roles)
        return

    if page == "求职总览":
        render_dashboard()
        return

    if page == "简历中心":
        render_resume_center()
        return

    if page == "投递管道":
        render_job_pipeline()
        return

    if page == "成长计划":
        render_action_plan()
        return

    if page == "历史报告":
        render_reports_tab()
        return

    # 求职意向是市场搜索词、技能画像和补强建议的共同输入，因此放在主内容区。
    target_role, city = render_job_preferences(roles)

    if page == "开始分析":
        render_workbench(
            target_role=target_role,
            city=city,
        )
    else:
        try:
            role_info = fetch_role_detail(target_role)
        except Exception as exc:
            role_info = {}
            st.warning(f"岗位技能图谱读取失败：{exc}")
        render_explore_tab(
            target_role=target_role,
            city=city,
            role_info=role_info,
        )


if __name__ == "__main__":
    main()
