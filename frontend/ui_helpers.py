from typing import Any

import streamlit as st


def apply_global_style() -> None:
    """集中维护 Streamlit 的基础视觉样式。

    Streamlit 的组件由框架生成，不能像 Vue 一样逐个写 CSS 类。
    因此把只影响视觉、不影响业务逻辑的样式集中在这里，避免散落在各页面函数中。
    """
    st.markdown(
        """
        <style>
            .stApp {
                background: #f6f8fb;
            }
            .block-container {
                max-width: 1240px;
                padding-top: 2rem;
                padding-bottom: 3rem;
            }
            [data-testid="stSidebar"] {
                background: #ffffff;
                border-right: 1px solid #e5e7eb;
            }
            [data-testid="stMetric"] {
                background: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 6px;
                padding: 0.9rem;
            }
            .stTabs [data-baseweb="tab-list"] {
                gap: 1.25rem;
            }
            .stTabs [data-baseweb="tab"] {
                height: 2.8rem;
                padding: 0 0.25rem;
            }
            .stButton > button,
            .stDownloadButton > button {
                border-radius: 6px;
                min-height: 2.5rem;
            }
            [data-testid="stExpander"] {
                border: 1px solid #e5e7eb;
                border-radius: 6px;
                background: #ffffff;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def format_tags(items: list[str]) -> str:
    """把技能列表渲染成 Streamlit 友好的 Markdown 标签。"""
    if not items:
        return "暂无"
    return " ".join(f"`{item}`" for item in items)


def get_role_core_skills(role_info: dict[str, Any]) -> list[str]:
    """安全读取岗位技能图谱中的核心技能。"""
    skills = role_info.get("core_skills", [])
    if not isinstance(skills, list):
        return []
    return [str(skill) for skill in skills]
