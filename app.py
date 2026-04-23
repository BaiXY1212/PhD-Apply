import time
import hashlib
import base64
import html
import streamlit as st
import google.generativeai as genai
from openai import OpenAI
import json
from streamlit_autorefresh import st_autorefresh
import pandas as pd
from datetime import datetime, timedelta
import os

from phdhub.ai_services import (
    classify_phd_email,
    evaluate_mock_interview_session,
    extract_category,
    generate_high_frequency_answer,
    generate_mock_interview_turn,
    generate_interview_advice,
    generate_resume_analysis,
    generate_rp_analysis,
    generate_interview_questions,
    verify_professor_homepage,
)
from phdhub.constants import EMAILS_CACHE_FILE
from phdhub.email_client import (
    fetch_all_emails as fetch_all_emails_impl,
    test_imap_connection,
)
from phdhub.email_sync import (
    fetch_once,
    get_cached_emails,
    start_background_email_fetch as start_background_email_fetch_worker,
)
from phdhub.i18n import translate
from phdhub.interview_prep import (
    format_interview_time,
    get_homepage_text_excerpt,
    get_interview_records,
    get_interview_picker_defaults,
    resolve_recent_papers,
)
from phdhub.resume_store import add_resume, delete_resume, get_resume, list_resumes
from phdhub.rp_store import add_rp, delete_rp, get_rp, list_rps
from phdhub.resume_utils import build_pdf_thumbnail_png
from phdhub.stats import get_email_stats_from_emails, get_recent_7d_email_stats_from_emails
from phdhub.storage import (
    load_config,
    load_db,
    save_config,
    save_db,
)
from phdhub.timezone_utils import format_local_time
from phdhub.university import get_world_universities as get_world_universities_impl


def t(key):
    return translate(key, st.session_state.get("app_lang", "zh-CN"))


def tr(zh, en):
    return en if st.session_state.get("app_lang", "zh-CN") == "en" else zh


def _save_ai_settings_from_state():
    config = load_config()
    if "settings_ai_provider" in st.session_state:
        config["ai_provider"] = st.session_state.get("settings_ai_provider", "通义千问 (Qwen)")
    if "settings_qwen_api_key" in st.session_state:
        config["qwen_api_key"] = st.session_state.get("settings_qwen_api_key", "")
    if "settings_gemini_api_key" in st.session_state:
        config["gemini_api_key"] = st.session_state.get("settings_gemini_api_key", "")
    save_config(config)


def _ai_cfg_with_app_lang(cfg):
    ai_cfg = dict(cfg or {})
    ai_cfg["app_lang"] = st.session_state.get("app_lang", "zh-CN")
    return ai_cfg


_GEMINI_MODEL = "gemini-2.5-flash"


def _gemini_generate_content_with_fallback(prompt, stream=False):
    if "HTTP_PROXY" not in os.environ and "HTTPS_PROXY" not in os.environ:
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"
    try:
        model = genai.GenerativeModel(_GEMINI_MODEL)
        response = model.generate_content(prompt, stream=stream)
        return response, _GEMINI_MODEL
    except Exception as e:
        raise Exception(f"{_GEMINI_MODEL}: {e}")


@st.cache_data(ttl=86400)
def get_world_universities():
    return get_world_universities_impl()


def get_recent_7d_email_stats():
    success, emails = get_cached_emails(limit=5000)
    if not success:
        return get_recent_7d_email_stats_from_emails([])
    return get_recent_7d_email_stats_from_emails(emails)


def get_total_email_stats():
    success, emails = get_cached_emails(limit=5000)
    if not success:
        return get_email_stats_from_emails([])
    return get_email_stats_from_emails(emails)


def get_recent_7d_scheduled_interviews_count():
    db = load_db()
    if not db:
        return 0
    today = datetime.now().date()
    window_start = today - timedelta(days=6)
    count = 0
    for row in db:
        if row.get("阶段") != "面试预约阶段":
            continue
        raw = str(row.get("更新时间", "")).strip()
        if not raw:
            continue
        parsed = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
            try:
                parsed = datetime.strptime(raw, fmt)
                break
            except Exception:
                continue
        if parsed and window_start <= parsed.date() <= today:
            count += 1
    return count


@st.cache_data(ttl=60)
def fetch_all_emails(email_add, password, imap_server, limit=15):
    return fetch_all_emails_impl(email_add, password, imap_server, limit=limit)


st.set_page_config(
    page_title="PhDHub - 智能申博辅助系统",
    page_icon="assets/phdhub-mark.svg",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 启动后台邮件拉取线程（确保在所有工具函数之后执行）
@st.cache_resource
def init_background_fetch():
    start_background_email_fetch_worker()
    return True

init_background_fetch()

# 注入自定义 CSS
st.markdown("""
<style>
    :root {
        --bg-main: #050a1e;
        --bg-mid: #07163a;
        --bg-deep: #050914;
        --panel: rgba(21, 33, 66, 0.52);
        --panel-strong: rgba(18, 29, 56, 0.72);
        --line: rgba(130, 156, 214, 0.26);
        --line-soft: rgba(102, 187, 255, 0.24);
        --text-main: #e9f0ff;
        --text-soft: #9aa9ca;
        --accent: #3b82f6;
        --ok: #35c47a;
        --card-shadow: 0 16px 32px rgba(3, 9, 26, 0.55);
    }

    html, body, [class*="css"] {
        font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", "PingFang SC", "Noto Sans SC", sans-serif;
        color: var(--text-main);
    }

    [data-testid="stAppViewContainer"] {
        background:
            radial-gradient(780px 460px at 30% 34%, rgba(61, 112, 255, 0.34), transparent 65%),
            radial-gradient(760px 460px at 82% 62%, rgba(255, 121, 53, 0.24), transparent 66%),
            linear-gradient(145deg, var(--bg-main), var(--bg-mid) 54%, var(--bg-deep));
        color: var(--text-main);
    }

    [data-testid="stAppViewContainer"]::before {
        content: "";
        position: fixed;
        inset: 0;
        pointer-events: none;
        background: linear-gradient(to bottom, rgba(255, 255, 255, 0.05), transparent 34%);
    }

    [data-testid="stHeader"] {
        background: rgba(14, 22, 46, 0.66);
        border-bottom: 1px solid var(--line);
        backdrop-filter: blur(14px) saturate(135%);
    }

    /* Hide Streamlit top-right actions (Deploy + overflow menu) */
    [data-testid="stToolbar"],
    [data-testid="stHeaderActionElements"] {
        display: none !important;
        visibility: hidden !important;
    }

    [data-testid="stSidebar"] > div {
        background: rgba(11, 20, 42, 0.66);
        border-right: 1px solid var(--line);
        backdrop-filter: blur(16px) saturate(140%);
    }

    [data-testid="stSidebar"] * {
        color: var(--text-main) !important;
    }

    h1, h2, h3 {
        color: var(--text-main);
        letter-spacing: -0.01em;
        font-weight: 700;
    }

    p, .stCaption, label, small {
        color: var(--text-soft) !important;
    }

    div[data-testid="stAlert"],
    div[data-testid="stMetric"],
    div[data-testid="stDataFrame"],
    div[data-testid="stExpander"],
    div[data-testid="stFileUploaderDropzone"],
    div[data-testid="stForm"],
    div[data-testid="stTextInputRootElement"],
    div[data-testid="stTextAreaRootElement"],
    div[data-testid="stDateInputFieldContainer"],
    div[data-testid="stSelectbox"],
    div[data-testid="stMultiSelect"] {
        background: linear-gradient(140deg, var(--panel), var(--panel-strong));
        border: 1px solid var(--line);
        border-radius: 14px;
        box-shadow: var(--card-shadow), inset 0 1px 0 rgba(255, 255, 255, 0.08);
        backdrop-filter: blur(16px) saturate(140%);
    }

    div[data-testid="stFileUploaderDropzone"] {
        padding: 12px;
        border-style: dashed;
    }

    .stButton > button,
    .stDownloadButton > button {
        border: 1px solid rgba(139, 170, 232, 0.34);
        background: linear-gradient(145deg, rgba(19, 33, 66, 0.86), rgba(15, 26, 50, 0.78));
        color: var(--text-main);
        border-radius: 12px;
        min-height: 40px;
        box-shadow: 0 10px 20px rgba(2, 10, 29, 0.46);
        transition: all 0.18s ease;
    }

    .stButton > button:hover,
    .stDownloadButton > button:hover {
        transform: translateY(-1px);
        border-color: rgba(120, 194, 255, 0.52);
        box-shadow: 0 12px 24px rgba(2, 10, 29, 0.52);
    }

    .stButton > button[kind="primary"] {
        background: linear-gradient(140deg, #3d7cff, #3b82f6);
        border: 1px solid rgba(152, 200, 255, 0.46);
        color: #ffffff;
        box-shadow: 0 14px 26px rgba(52, 120, 255, 0.32);
    }

    .stRadio > div,
    .stSelectbox > div > div {
        border-radius: 10px;
    }

    .stSelectbox,
    .stMultiSelect {
        width: 100%;
        min-width: 0;
    }

    .stSelectbox [data-baseweb="select"],
    .stMultiSelect [data-baseweb="select"] {
        width: 100%;
        min-width: 0;
        max-width: 100%;
        border: 1px solid rgba(139, 170, 232, 0.32) !important;
        border-radius: 10px !important;
        background: rgba(20, 34, 66, 0.76) !important;
        box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08) !important;
        backdrop-filter: blur(12px) saturate(135%);
    }

    .stSelectbox [data-baseweb="select"] > div,
    .stMultiSelect [data-baseweb="select"] > div {
        color: var(--text-main) !important;
        background: transparent !important;
        min-width: 0;
        max-width: 100%;
    }

    .stSelectbox label p,
    .stMultiSelect label p {
        white-space: normal !important;
        overflow-wrap: anywhere;
        word-break: break-word;
    }

    [data-testid="stHorizontalBlock"] > div,
    [data-testid="stColumn"] {
        min-width: 0;
    }

    .stTabs [data-baseweb="tab-list"] {
        background: rgba(16, 28, 55, 0.66);
        border: 1px solid var(--line);
        border-radius: 12px;
        padding: 4px;
        backdrop-filter: blur(14px) saturate(135%);
    }

    .stTabs [data-baseweb="tab"] {
        border-radius: 8px;
        color: var(--text-soft) !important;
    }

    .stTabs [aria-selected="true"] {
        background: rgba(56, 104, 214, 0.46) !important;
        color: var(--text-main) !important;
        font-weight: 700;
        border: 1px solid rgba(122, 180, 255, 0.52);
    }

    hr {
        border: none;
        height: 1px;
        background: rgba(124, 157, 219, 0.34);
    }

    .status-bar {
        background-color: transparent;
        padding: 8px 0;
        margin-bottom: 14px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 6px;
    }

    .status-item {
        font-size: 12.5px;
        font-weight: 600;
        color: #bed1f6;
        background: rgba(20, 36, 70, 0.74);
        border: 1px solid rgba(139, 170, 232, 0.30);
        padding: 6px 10px;
        border-radius: 999px;
        display: flex;
        align-items: center;
    }

    .status-item.active {
        color: #e8f2ff;
        background: rgba(62, 113, 236, 0.52);
        border-color: rgba(150, 203, 255, 0.56);
    }

    .status-item.completed {
        color: #dbffe8;
        background: rgba(35, 126, 87, 0.58);
        border-color: rgba(140, 245, 184, 0.48);
    }

    .status-line {
        flex-grow: 1;
        height: 2px;
        background-color: rgba(124, 157, 219, 0.34);
        margin: 0 8px;
    }

    .status-line.completed {
        background-color: rgba(52, 196, 122, 0.76);
    }

    .tag {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 9999px;
        font-size: 12px;
        font-weight: 600;
        background: rgba(66, 126, 242, 0.30);
        color: #d4e6ff;
        border: 1px solid rgba(140, 189, 255, 0.46);
    }

    .analysis-module-card {
        background: linear-gradient(142deg, rgba(20, 34, 66, 0.76), rgba(16, 27, 53, 0.72));
        border: 1px solid rgba(130, 156, 214, 0.28);
        border-radius: 14px;
        padding: 14px 14px 10px;
        min-height: 220px;
        box-shadow: var(--card-shadow);
        backdrop-filter: blur(16px) saturate(140%);
    }

    .analysis-module-head {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 10px;
        color: #eaf2ff;
        font-size: 15px;
        font-weight: 700;
    }

    .analysis-module-icon {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 22px;
        height: 22px;
        border-radius: 999px;
        background: rgba(59, 130, 246, 0.22);
        border: 1px solid rgba(120, 194, 255, 0.44);
        font-size: 12px;
    }

    .analysis-module-list {
        margin: 0;
        padding-left: 18px;
    }

    .analysis-module-list li {
        color: #a8b9dc;
        margin: 0 0 8px;
        line-height: 1.5;
        font-size: 13.5px;
    }

    .analysis-empty {
        color: #7e93bd !important;
        list-style: none;
        margin-left: -18px !important;
        font-style: italic;
    }

    [data-testid="stTextInputRootElement"] input,
    [data-testid="stTextAreaRootElement"] textarea,
    [data-testid="stDateInputFieldContainer"] input {
        color: var(--text-main) !important;
        background: rgba(20, 34, 66, 0.80) !important;
        border: 1px solid rgba(139, 170, 232, 0.34) !important;
        border-radius: 10px !important;
    }

    [data-testid="stTextInputRootElement"] input:focus,
    [data-testid="stTextAreaRootElement"] textarea:focus,
    [data-testid="stDateInputFieldContainer"] input:focus {
        border-color: rgba(112, 193, 255, 0.66) !important;
        box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.22) !important;
    }

    div[data-testid="stMetric"] > div,
    div[data-testid="stMetricLabel"],
    div[data-testid="stMetricValue"],
    div[data-testid="stMetricDelta"] {
        width: 100%;
        text-align: center;
        align-items: center;
        justify-content: center;
    }

    div[data-testid="stMetric"] * {
        text-align: center !important;
        justify-content: center !important;
    }

    @media (max-width: 900px) {
        div[data-testid="stAlert"],
        div[data-testid="stMetric"],
        div[data-testid="stDataFrame"],
        div[data-testid="stExpander"],
        div[data-testid="stFileUploaderDropzone"],
        div[data-testid="stForm"],
        div[data-testid="stTextInputRootElement"],
        div[data-testid="stTextAreaRootElement"],
        div[data-testid="stDateInputFieldContainer"],
        div[data-testid="stSelectbox"],
        div[data-testid="stMultiSelect"] {
            border-radius: 12px;
        }

        .stButton > button,
        .stDownloadButton > button {
            width: 100%;
        }

        .analysis-module-card {
            min-height: 180px;
        }

    }

    /* 放大对话框宽度，用于 PDF 预览 */
    div[data-testid="stDialog"] div[role="dialog"] {
        width: min(1200px, 95vw);
        border-radius: 16px;
        border: 1px solid rgba(130, 156, 214, 0.34);
        background: rgba(14, 25, 50, 0.84);
        box-shadow: 0 18px 40px rgba(2, 10, 29, 0.62);
        backdrop-filter: blur(20px) saturate(145%);
    }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 数据获取
# ==========================================

@st.dialog("🚨 确认删除 / Confirm Delete")
def confirm_delete_dialog(prof_name, univ_name, delete_idx=None):
    st.warning(tr(f"确定要永久删除 **{prof_name}** ({univ_name}) 的申请记录吗？此操作不可恢复。",
                  f"Permanently delete application record for **{prof_name}** ({univ_name})? This cannot be undone."))
    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button(tr("✖️ 返回 / 取消", "✖️ Back / Cancel"), use_container_width=True):
            st.rerun()
    with c2:
        if st.button(tr("🗑️ 确认删除", "🗑️ Confirm Delete"), use_container_width=True, type="primary"):
            current_db = load_db()
            if delete_idx is not None and 0 <= delete_idx < len(current_db):
                current_db.pop(delete_idx)
                save_db(current_db)
            else:
                new_db = [r for r in current_db if not (r.get("导师/教授") == prof_name and r.get("学校名称") == univ_name)]
                save_db(new_db)
            st.success(tr("✅ 删除成功！", "✅ Deleted successfully!"))
            st.rerun()


@st.dialog("📄 简历预览 / Resume Preview")
def show_resume_pdf_modal(pdf_path, title="简历"):
    st.markdown(f"**{title}**")
    if pdf_path and os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        st.markdown(
            f"""
            <iframe
                src="data:application/pdf;base64,{b64}"
                width="100%"
                height="900"
                type="application/pdf"
            ></iframe>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.warning(tr("找不到该简历文件。", "Resume file not found."))

def get_dashboard_data():
    db = load_db()
    if not db:
        return pd.DataFrame(columns=["导师/教授", "国家/地区", "学校名称", "院系", "研究方向", "推荐级", "阶段", "更新时间"])
    return pd.DataFrame(db)

# ==========================================
# 核心 UI 组件
# ==========================================
def render_status_bar(current_status, interview_time=""):
    step_mapping = {
        "未联系": 0, "已发首封邮件": 1, "收到回复": 2, "收到积极回复": 2, "收到中等回复": 2, "收到消极回复": 2,
        "面试准备": 3, "面试预约阶段": 3, "面试结束阶段": 3, "口头offer": 4
    }
    
    current_index = step_mapping.get(current_status, 0)
    statuses = ["未联系", "首封邮件", "收到回复", "面试环节", "口头offer"]
    
    if current_status in ["收到积极回复", "收到中等回复", "收到消极回复"]:
        statuses[2] = current_status.replace("收到", "")
    elif current_index > 2:
        statuses[2] = "收回复"
        
    if current_status == "面试预约阶段":
        import math
        iv_time = "" if interview_time is None or (isinstance(interview_time, float) and math.isnan(interview_time)) else str(interview_time)
        time_str = f"<br/><span style='font-size:10.5px;color:#f59e0b;'>{iv_time}</span>" if iv_time else ""
        statuses[3] = f"预约{time_str}" 
    elif current_status == "面试结束阶段":
        statuses[3] = "结束"
        
    html_parts = ["<div class='status-bar'>"]
    
    for i, status in enumerate(statuses):
        if i < current_index:
            state_class, icon = "completed", "✓"
        elif i == current_index:
            state_class, icon = "active", "◉"
        else:
            state_class, icon = "", "○"
            
        html_parts.append(f"<div class='status-item {state_class}'>{icon} {status}</div>")
        
        if i < len(statuses) - 1:
            line_class = "completed" if i < current_index else ""
            html_parts.append(f"<div class='status-line {line_class}'></div>")
            
    html_parts.append("</div>")
    st.markdown("".join(html_parts), unsafe_allow_html=True)

def render_analysis_modules(section_title, modules):
    st.markdown(f"### {section_title}")
    cols = st.columns(len(modules))
    for col, (icon, title, items) in zip(cols, modules):
        normalized_items = []
        if isinstance(items, list):
            normalized_items = [str(x).strip() for x in items if str(x).strip()]
        elif isinstance(items, str) and items.strip():
            normalized_items = [items.strip()]

        list_html = "".join(f"<li>{html.escape(x)}</li>" for x in normalized_items)
        if not list_html:
            list_html = "<li class='analysis-empty'>暂无可展示内容</li>"

        col.markdown(
            f"""
            <div class="analysis-module-card">
                <div class="analysis-module-head">
                    <span class="analysis-module-icon">{html.escape(icon)}</span>
                    <span>{html.escape(title)}</span>
                </div>
                <ul class="analysis-module-list">{list_html}</ul>
            </div>
            """,
            unsafe_allow_html=True,
        )

def get_active_resume_text(cfg):
    resume_text = ""
    active_id = cfg.get("active_resume_id", "")
    if active_id:
        active_resume = get_resume(active_id)
        if active_resume:
            resume_text = active_resume.get("text", "")
    if not resume_text:
        resume_text = cfg.get("resume_text", "")
    return resume_text


@st.dialog("🎭 AI 模拟面试官 / Mock Interview")
def show_mock_interview_dialog(idx, row):
    prof_name = row.get("导师/教授", tr("未知导师", "Unknown Professor"))
    univ_name = row.get("学校名称", tr("未知学校", "Unknown University"))
    direction = row.get("研究方向", tr("未明确", "Not specified"))
    hp = row.get("主页链接", "")
    state_key = f"mock_interview_chat_{idx}"
    input_key_prefix = f"mock_interview_input_{idx}"
    input_nonce_key = f"mock_interview_input_nonce_{idx}"
    eval_key = f"mock_interview_eval_{idx}"
    ended_key = f"mock_interview_ended_{idx}"

    cfg = load_config()
    ai_cfg = _ai_cfg_with_app_lang(cfg)
    resume_text = get_active_resume_text(cfg)
    if not resume_text:
        st.warning(tr("请先在【我的简历】上传并设置当前使用简历，再进行模拟面试。",
                      "Please upload and set an active resume before starting mock interview."))
        return

    homepage_text = get_homepage_text_excerpt(hp, limit=3000) if hp else ""
    papers_for_mark = row.get("最近论文", [])
    if not isinstance(papers_for_mark, list):
        papers_for_mark = []

    def _save_high_frequency_point(question_text):
        q_text = str(question_text or "").strip()
        if not q_text:
            return False, tr("问题内容为空。", "Question is empty.")
        with st.spinner(tr("AI 正在生成该考察点的建议回答...", "AI is generating suggested answer...")):
            ok_hf, hf_payload, hf_raw = generate_high_frequency_answer(
                question=q_text,
                prof_name=prof_name,
                univ_name=univ_name,
                research_direction=direction,
                homepage_url=hp,
                homepage_text=homepage_text,
                papers=papers_for_mark,
                resume_text=resume_text,
                config=ai_cfg,
            )
        if not ok_hf:
            return False, f"生成建议回答失败：{hf_raw}"

        db = load_db()
        if not (0 <= int(idx) < len(db)):
            return False, tr("未找到对应导师记录。", "Target professor record not found.")

        points = db[idx].get("高频考察点", [])
        if not isinstance(points, list):
            points = []

        existed = False
        for item in points:
            if isinstance(item, dict) and str(item.get("question", "")).strip() == q_text:
                item["ai_answer"] = hf_payload.get("suggested_answer", "")
                item["key_points"] = hf_payload.get("key_points", [])
                item["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                existed = True
                break
        if not existed:
            points.append(
                {
                    "question": q_text,
                    "ai_answer": hf_payload.get("suggested_answer", ""),
                    "key_points": hf_payload.get("key_points", []),
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        db[idx]["高频考察点"] = points
        save_db(db)
        return True, tr("已标记为高频考察点，并生成 AI 建议回答。", "Marked as high-frequency question and generated AI answer.")

    if state_key not in st.session_state:
        opening = (
            tr(
                f"你好，我是 {univ_name} 的 {prof_name}。请你先用 1-2 分钟做自我介绍，并说明你与我们课题组的匹配点。",
                f"Hi, I'm {prof_name} from {univ_name}. Please introduce yourself in 1-2 minutes and explain your fit with our group."
            )
        )
        st.session_state[state_key] = [{"role": "interviewer", "content": opening}]
        st.session_state[eval_key] = None
        st.session_state[ended_key] = False
        st.session_state[input_nonce_key] = 0

    if input_nonce_key not in st.session_state:
        st.session_state[input_nonce_key] = 0

    chat = st.session_state.get(state_key, [])
    ended = bool(st.session_state.get(ended_key, False))

    st.caption(tr(f"导师：{prof_name} | 学校：{univ_name} | 方向：{direction}",
                  f"Professor: {prof_name} | University: {univ_name} | Direction: {direction}"))
    if hp:
        st.caption(tr(f"主页：{hp}", f"Homepage: {hp}"))

    chat_holder = st.empty()
    input_holder = st.empty()
    live_reply_placeholder = None

    def render_chat_area(chat_items):
        nonlocal live_reply_placeholder
        with chat_holder.container():
            st.markdown(f"#### {tr('对话记录', 'Conversation')}")
            for turn_idx, turn in enumerate(chat_items):
                if turn.get("role") == "candidate":
                    st.markdown(f"**{tr('🧑‍🎓 你：', '🧑‍🎓 You:')}** {turn.get('content', '')}")
                else:
                    q_text = str(turn.get("content", "")).strip()
                    q_col, mark_col = st.columns([8.8, 1.2])
                    with q_col:
                        st.markdown(f"**{tr('👨‍🏫 面试官：', '👨‍🏫 Interviewer:')}** {q_text}")
                    with mark_col:
                        if st.button(tr("标记", "Mark"), key=f"mock_mark_hf_{idx}_{turn_idx}", help=tr("标记为高频考察点", "Mark as high-frequency question")):
                            ok_mark, msg_mark = _save_high_frequency_point(q_text)
                            if ok_mark:
                                st.success(msg_mark)
                            else:
                                st.error(msg_mark)
            live_reply_placeholder = st.empty()

    def render_input_area(is_ended):
        current_key = f"{input_key_prefix}_{st.session_state.get(input_nonce_key, 0)}"
        with input_holder.container():
            if is_ended:
                st.info(tr("本场模拟已结束。你可以查看评分，或点击“重新开始”。",
                           "This mock interview has ended. You can review scores or click restart."))
            else:
                st.text_area(
                    tr("你的回答", "Your Answer"),
                    key=current_key,
                    height=120,
                    placeholder=tr("请输入你的回答，尽量具体，给出证据和方法细节。", "Write your answer with concrete methods and evidence."),
                )
        return current_key

    render_chat_area(chat)
    current_input_key = render_input_area(ended)

    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button(tr("发送回答", "Send Answer"), key=f"mock_send_{idx}", use_container_width=True, disabled=ended):
            answer = str(st.session_state.get(current_input_key, "")).strip()
            if not answer:
                st.warning(tr("请先输入你的回答。", "Please enter your answer first."))
            else:
                chat.append({"role": "candidate", "content": answer})
                live_chat_prefix = f"**{tr('🧑‍🎓 你：', '🧑‍🎓 You:')}** {answer}\n\n"
                live_reply_placeholder.markdown(live_chat_prefix)
                st.session_state[input_nonce_key] = int(st.session_state.get(input_nonce_key, 0)) + 1
                next_input_key = f"{input_key_prefix}_{st.session_state[input_nonce_key]}"
                st.session_state[next_input_key] = ""
                render_input_area(False)
                with st.spinner(tr("面试官正在追问...", "Interviewer is generating follow-up...")):
                    ok, reply, raw = generate_mock_interview_turn(
                        prof_name=prof_name,
                        univ_name=univ_name,
                        research_direction=direction,
                        homepage_url=hp,
                        homepage_text=homepage_text,
                        resume_text=resume_text,
                        conversation=chat,
                        config=ai_cfg,
                    )
                if ok:
                    rendered = ""
                    for ch in str(reply):
                        rendered += ch
                        live_reply_placeholder.markdown(
                            live_chat_prefix + f"**{tr('👨‍🏫 面试官：', '👨‍🏫 Interviewer:')}** {rendered}▌"
                        )
                        time.sleep(0.012)
                    live_reply_placeholder.markdown(
                        live_chat_prefix + f"**{tr('👨‍🏫 面试官：', '👨‍🏫 Interviewer:')}** {rendered}"
                    )
                    chat.append({"role": "interviewer", "content": reply})
                    st.session_state[state_key] = chat
                else:
                    live_reply_placeholder.markdown(live_chat_prefix)
                    st.error(tr(f"生成失败：{raw}", f"Generation failed: {raw}"))

    with c2:
        if st.button(tr("结束面试并评分", "End & Score"), key=f"mock_end_{idx}", use_container_width=True):
            candidate_turns = [x for x in chat if x.get("role") == "candidate"]
            if len(candidate_turns) < 1:
                st.warning(tr("请至少回答 1 轮后再结束评分。", "Answer at least one round before scoring."))
            else:
                with st.spinner(tr("面试官正在评分...", "Scoring interview...")):
                    ok, result, raw = evaluate_mock_interview_session(
                        prof_name=prof_name,
                        univ_name=univ_name,
                        research_direction=direction,
                        resume_text=resume_text,
                        conversation=chat,
                        config=ai_cfg,
                    )
                if ok:
                    st.session_state[eval_key] = result
                    st.session_state[ended_key] = True
                else:
                    st.error(tr(f"评分失败：{raw}", f"Scoring failed: {raw}"))

    with c3:
        if st.button(tr("重新开始", "Restart"), key=f"mock_reset_{idx}", use_container_width=True):
            opening = (
                tr(
                    f"你好，我是 {univ_name} 的 {prof_name}。请你先用 1-2 分钟做自我介绍，并说明你与我们课题组的匹配点。",
                    f"Hi, I'm {prof_name} from {univ_name}. Please introduce yourself in 1-2 minutes and explain your fit with our group."
                )
            )
            for k in list(st.session_state.keys()):
                if str(k).startswith(f"{input_key_prefix}_"):
                    st.session_state.pop(k, None)
            st.session_state[state_key] = [{"role": "interviewer", "content": opening}]
            st.session_state[eval_key] = None
            st.session_state[ended_key] = False
            st.session_state[input_nonce_key] = 0
            render_chat_area(st.session_state[state_key])
            next_input_key = f"{input_key_prefix}_{st.session_state[input_nonce_key]}"
            st.session_state[next_input_key] = ""
            render_input_area(False)

    eval_result = st.session_state.get(eval_key)
    if isinstance(eval_result, dict) and eval_result:
        st.markdown(f"#### {tr('面试评分结果', 'Interview Score')}")
        score = eval_result.get("overall_score", 0)
        tendency = eval_result.get("admission_tendency", tr("待定", "Pending"))
        summary = eval_result.get("summary", "")
        dims = eval_result.get("dimension_scores", {}) if isinstance(eval_result.get("dimension_scores", {}), dict) else {}
        d1, d2, d3, d4, d5 = st.columns(5)
        d1.metric(tr("综合分", "Overall"), score)
        d2.metric(tr("录取倾向", "Admission"), tendency)
        d3.metric(tr("匹配度", "Fit"), dims.get("research_fit", "-"))
        d4.metric(tr("方法深度", "Method"), dims.get("method_depth", "-"))
        d5.metric(tr("表达能力", "Communication"), dims.get("communication", "-"))
        if summary:
            st.info(summary)

        strengths = eval_result.get("strengths", [])
        weaknesses = eval_result.get("weaknesses", [])
        improvements = eval_result.get("improvements", [])
        render_analysis_modules(
            tr("面试复盘", "Interview Review"),
            [
                ("✅", tr("表现亮点", "Strengths"), strengths),
                ("⚠️", tr("主要短板", "Weaknesses"), weaknesses),
                ("🛠️", tr("改进建议", "Improvements"), improvements),
            ],
        )


@st.dialog("🎯 导师档案及邮件记录")
def show_professor_details(row):
    st.markdown(f"#### {row.get('导师/教授', '未知导师')} | 🏛️ {row.get('学校名称', '未知学校')}")
    c1, c2, c3 = st.columns(3)
    with c1: st.write(f"🏷️ **{row.get('推荐级', '未知')}级**")
    with c2: st.write(f"🏢 **{row.get('院系', '未分类')}**")
    with c3: st.write(f"💼 **{row.get('阶段', '未开始')}**")
    
    st.write(f"🔬 **研究方向:** `{row.get('研究方向', '未明确')}`")
    
    if row.get("导师邮箱"):
        st.write(f"📧 **联系邮箱:** `{row.get('导师邮箱')}`")
    
    
    if row.get('主页链接'):
        st.markdown(f"🔗 **个人主页:** [{row.get('主页链接')}]({row.get('主页链接')})")
        
    st.markdown("---")
    
    prof_email = row.get("导师邮箱")
    email_id = row.get("关联邮件ID")
    
    success, emails = get_cached_emails(limit=2000)
    
    if not success or not emails:
        st.warning("🤷‍♂️ 未能从本地缓存加载邮件列表。")
    else:
        # 第一优先级：根据导师的邮箱串联所有往来邮件
        thread_mails = []
        thread_mail_ids = set()
        
        if prof_email:
            # simple matching
            pe_clean = prof_email.strip().lower()
            for m in emails:
                m_from = m.get('from', '').lower()
                m_to = m.get('to', '').lower()
                if pe_clean in m_from or pe_clean in m_to:
                    thread_mails.append(m)
                    if m.get("id"):
                        thread_mail_ids.add(str(m.get("id")))
        
        # 第二优先级：补充手动关联的邮件（支持逗号分隔的多个ID）
        if email_id:
            manual_ids = [x.strip() for x in str(email_id).split(",") if x.strip()]
            for mid in manual_ids:
                if mid not in thread_mail_ids:
                    target_mail = next((m for m in emails if str(m.get("id")) == mid), None)
                    if target_mail:
                        thread_mails.append(target_mail)
                        thread_mail_ids.add(mid)
        
        if thread_mails:
            # 邮箱按照时间正序排列（最旧的在上面，最新的在下面），方便像聊天一样看
            st.markdown(f"##### 💬 往来邮件对话记录 (共 {len(thread_mails)} 封)")
            
            # 由于 IMAP 获取通常是最新在前面，反转一下变成时间正序
            thread_mails.reverse()
            
            for idx, tm in enumerate(thread_mails):
                with st.expander(f"✉️ {tm.get('date', '')} - {tm.get('subject', '无标题')}", expanded=(idx == len(thread_mails)-1)):
                    st.caption(f"**From:** `{tm.get('from', '')}` | **To:** `{tm.get('to', '')}`")
                    st.text_area("邮件内容", tm.get('body', ''), height=200, disabled=True, label_visibility="collapsed", key=f"thread_{row.get('导师/教授', '未知')}_{tm.get('id')}_{idx}")
        else:
            if not prof_email and not email_id:
                st.info("📌 该记录未绑定邮箱或关联邮件。")
            else:
                st.warning("🤷‍♂️ 未能找到该邮箱或该ID的任何往来通讯，可能太久远了！")

# ==========================================
# 主界面构建
# ==========================================
def main():
    ui = lambda zh, en: en if st.session_state.get("app_lang", "zh-CN") == "en" else zh

    # ==== 国际化语言切换 (右上角) ====
    if "app_lang" not in st.session_state:
        st.session_state["app_lang"] = "zh-CN"
    if st.session_state.get("app_lang") not in ("zh-CN", "en"):
        st.session_state["app_lang"] = "zh-CN"
        
    _, lang_col = st.columns([8, 2])
    with lang_col:
        lang_options = {
            "zh-CN": "中文",
            "en": "English",
        }
        st.selectbox("Language", 
                     options=list(lang_options.keys()), 
                     format_func=lambda x: lang_options[x],
                     key="app_lang",
                     label_visibility="collapsed")

    # 侧边栏导航
    with st.sidebar:
        st.image("fig/logo.png", width=180)
        st.caption(ui("AI 智能博士申请辅助系统", "AI-Powered PhD Application Assistant"))
        resume_menu_label = ui("我的简历", "My Resume")
        rp_menu_label = ui("我的RP", "My RP")
        settings_menu_label = ui("系统配置", "System Config")
        menu_items = [resume_menu_label, rp_menu_label, t("menu_dashboard"), t("menu_email"), t("menu_db"), t("menu_interview"), settings_menu_label]
        menu = st.radio(t("nav_menu"), menu_items, index=0)
        
        st.info(ui("💡 **提示**: 用户数据仅保存在本地以保障隐私。", "💡 **Tip**: User data is stored locally for privacy."))

    # 主体内容
    if menu == resume_menu_label:
        st.title(ui("我的简历", "My Resume"))
        st.markdown(f"<p style='color: #9aa9ca; margin-bottom: 1rem;'>{ui('上传 PDF 后将自动保存、自动设为当前简历并自动分析。', 'After PDF upload, it is saved automatically, set as active, and analyzed by AI.')}</p>", unsafe_allow_html=True)

        cfg = load_config()
        resumes = list_resumes()
        preview_resume = None
        if resumes:
            preview_idx = st.session_state.get("resume_pick_idx", 0)
            if not isinstance(preview_idx, int):
                preview_idx = 0
            preview_idx = max(0, min(preview_idx, len(resumes) - 1))
            preview_resume = resumes[preview_idx]

        resume_left, resume_right = st.columns([4.8, 5.2])
        with resume_left:
            upload_file = st.file_uploader(ui("上传 PDF 简历", "Upload Resume PDF"), type=["pdf"], accept_multiple_files=False, key="resume_auto_uploader")
            st.markdown(f"#### {ui('已上传简历', 'Uploaded Resumes')}")
            if not resumes:
                st.info(ui("还没有简历，请先上传。", "No resumes yet. Upload one to get started."))
            else:
                def _resume_label(i):
                    fn = str(resumes[i].get("filename", "简历"))
                    return fn if len(fn) <= 28 else (fn[:25] + "...")

                c_pick, c_del = st.columns([3.2, 1.3])
                with c_pick:
                    idx = st.selectbox(
                        ui("选择简历", "Select Resume"),
                        range(len(resumes)),
                        format_func=_resume_label,
                        key="resume_pick_idx",
                        label_visibility="collapsed",
                    )
                sel = resumes[idx]
                rid = sel.get("id")

                with c_del:
                    st.markdown("<div style='height:2px'></div>", unsafe_allow_html=True)
                    if st.button(ui("删除", "Delete"), key=f"resume_del_icon_{rid}", help=ui("删除这份简历", "Delete this resume"), type="primary", use_container_width=True):
                        st.session_state["resume_del_pending_id"] = rid

                if st.session_state.get("resume_del_pending_id") == rid:
                    st.warning(ui(f"确认删除：{sel.get('filename', '简历')} ?", f"Confirm deletion: {sel.get('filename', 'Resume')} ?"))
                    d1, d2 = st.columns(2)
                    with d1:
                        if st.button(ui("取消", "Cancel"), key=f"resume_del_cancel_{rid}", use_container_width=True):
                            st.session_state.pop("resume_del_pending_id", None)
                            st.rerun()
                    with d2:
                        if st.button(ui("确认删除", "Confirm Delete"), key=f"resume_del_confirm_{rid}", use_container_width=True, type="primary"):
                            delete_resume(rid)
                            cache_after_del = cfg.get("resume_analysis_cache", {})
                            if isinstance(cache_after_del, dict):
                                cache_after_del.pop(str(rid), None)
                                cfg["resume_analysis_cache"] = cache_after_del
                            if cfg.get("active_resume_id") == rid:
                                cfg["active_resume_id"] = ""
                                cfg["resume_text"] = ""
                                cfg["resume_filename"] = ""
                                cfg["resume_analysis"] = {}
                                save_config(cfg)
                            st.session_state.pop("resume_del_pending_id", None)
                            st.success(ui("已删除。", "Deleted."))
                            st.rerun()
        with resume_right:
            st.markdown(f"#### {ui('简历缩略图', 'Resume Thumbnail')}")
            if resumes:
                thumb_idx = st.session_state.get("resume_pick_idx", 0)
                if not isinstance(thumb_idx, int):
                    thumb_idx = 0
                thumb_idx = max(0, min(thumb_idx, len(resumes) - 1))
                thumb_resume = resumes[thumb_idx]
                preview_resume_id = thumb_resume.get("id", "")
                preview_resume_path = thumb_resume.get("path", "")
                thumb_w = 150
                thumb_png = build_pdf_thumbnail_png(pdf_path=preview_resume_path, width=thumb_w) if preview_resume_path else b""
                if thumb_png:
                    thumb_b64 = base64.b64encode(thumb_png).decode("utf-8")
                    st.markdown(
                        f"""
                        <a href="?resume_preview={preview_resume_id}" style="text-decoration:none;">
                            <img src="data:image/png;base64,{thumb_b64}" style="width:{thumb_w}px;height:190px;object-fit:contain;background:#fff;border:1px solid #ddd;border-radius:8px;cursor:pointer;display:block;" />
                        </a>
                        """,
                        unsafe_allow_html=True,
                    )
                    st.caption(ui("点击缩略图可打开预览", "Click thumbnail to preview"))
                else:
                    st.caption(ui("当前简历暂无可用缩略图", "No thumbnail available for this resume"))
            else:
                st.caption(ui("上传简历后显示缩略图", "Thumbnail appears after upload"))

        resume_analysis_cache = cfg.get("resume_analysis_cache", {})
        if not isinstance(resume_analysis_cache, dict):
            resume_analysis_cache = {}

        legacy_active_resume_id = str(cfg.get("active_resume_id", "") or "")
        legacy_analysis = cfg.get("resume_analysis", {})
        legacy_updated_at = cfg.get("resume_analysis_updated_at", "")
        if (
            legacy_active_resume_id
            and isinstance(legacy_analysis, dict)
            and legacy_analysis
            and legacy_active_resume_id not in resume_analysis_cache
        ):
            resume_analysis_cache[legacy_active_resume_id] = {
                "analysis": legacy_analysis,
                "updated_at": legacy_updated_at,
            }
        if upload_file is not None:
            file_bytes = upload_file.getvalue()
            file_sha = hashlib.sha1(file_bytes).hexdigest()
            if st.session_state.get("resume_last_upload_sha") != file_sha:
                with st.spinner("thinking... 正在解析并分析简历"):
                    ok, rec, err = add_resume(upload_file.name, file_bytes)
                    if ok and rec:
                        cfg["active_resume_id"] = rec.get("id")
                        cfg["resume_text"] = rec.get("text", "")
                        cfg["resume_filename"] = rec.get("filename", "")
                        cfg["resume_updated_at"] = rec.get("uploaded_at", "")
                        ai_cfg = dict(cfg)
                        ai_cfg["app_lang"] = st.session_state.get("app_lang", "zh-CN")
                        ok_a, result, raw = generate_resume_analysis(rec.get("text", ""), ai_cfg)
                        if ok_a:
                            cfg["resume_analysis"] = result
                            cfg["resume_analysis_updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            rec_id = str(rec.get("id", ""))
                            if rec_id:
                                resume_analysis_cache[rec_id] = {
                                    "analysis": result,
                                    "updated_at": cfg.get("resume_analysis_updated_at", ""),
                                }
                                cfg["resume_analysis_cache"] = resume_analysis_cache
                        save_config(cfg)
                        st.session_state["resume_last_upload_sha"] = file_sha
                    else:
                        st.error(ui(f"{upload_file.name} 保存失败：{err}", f"{upload_file.name} failed to save: {err}"))
                if ok and rec:
                    st.success(ui("上传成功", "Upload successful"))
                    st.rerun()

        if resumes:
            sel_idx = st.session_state.get("resume_pick_idx", 0)
            if not isinstance(sel_idx, int):
                sel_idx = 0
            sel_idx = max(0, min(sel_idx, len(resumes) - 1))
            sel = resumes[sel_idx]
            rid = str(sel.get("id", ""))

            selected_cache = resume_analysis_cache.get(rid, {})
            selected_analysis = {}
            selected_analysis_updated_at = ""
            if isinstance(selected_cache, dict):
                if isinstance(selected_cache.get("analysis"), dict):
                    selected_analysis = selected_cache.get("analysis", {})
                    selected_analysis_updated_at = selected_cache.get("updated_at", "")
                elif selected_cache:
                    selected_analysis = selected_cache

            if not selected_analysis and str(cfg.get("active_resume_id", "")) == rid and isinstance(cfg.get("resume_analysis", {}), dict):
                fallback_analysis = cfg.get("resume_analysis", {})
                if fallback_analysis:
                    selected_analysis = fallback_analysis
                    selected_analysis_updated_at = cfg.get("resume_analysis_updated_at", "")
                    resume_analysis_cache[rid] = {
                        "analysis": selected_analysis,
                        "updated_at": selected_analysis_updated_at,
                    }

            need_sync_active = str(cfg.get("active_resume_id", "")) != rid
            if need_sync_active:
                cfg["active_resume_id"] = rid
                cfg["resume_text"] = sel.get("text", "")
                cfg["resume_filename"] = sel.get("filename", "")
                cfg["resume_updated_at"] = sel.get("uploaded_at", "")

            if selected_analysis:
                cfg["resume_analysis"] = selected_analysis
                cfg["resume_analysis_updated_at"] = selected_analysis_updated_at

            cfg["resume_analysis_cache"] = resume_analysis_cache
            if need_sync_active or selected_analysis:
                save_config(cfg)

            pdf_path = sel.get("path", "")
            if not (preview_resume and str(preview_resume.get("id", "")) == str(rid)):
                if st.button(ui("打开简历预览", "Open Resume Preview"), key=f"resume_open_fallback_{rid}"):
                    show_resume_pdf_modal(pdf_path, sel.get("filename", "简历"))

            preview_id = st.query_params.get("resume_preview", "")
            if isinstance(preview_id, list):
                preview_id = preview_id[0] if preview_id else ""
            if str(preview_id) == str(rid):
                show_resume_pdf_modal(pdf_path, sel.get("filename", "简历"))
                try:
                    st.query_params.pop("resume_preview")
                except Exception:
                    try:
                        del st.query_params["resume_preview"]
                    except Exception:
                        pass

            analysis = selected_analysis
            if isinstance(analysis, dict) and analysis:
                render_analysis_modules(
                    ui("AI 简历分析", "AI Resume Analysis"),
                    [
                        ("✅", ui("申博优势", "Strengths"), analysis.get("strengths", [])),
                        ("⚠️", ui("申博劣势", "Weaknesses"), analysis.get("weaknesses", [])),
                        ("🛠️", ui("改进建议", "Improvements"), analysis.get("improvements", [])),
                    ],
                )

    elif menu == rp_menu_label:
        st.title(ui("我的RP", "My RP"))
        st.markdown(f"<p style='color: #9aa9ca; margin-bottom: 1rem;'>{ui('上传 RP PDF 后自动分析：写得好的点、缺陷、改进建议。', 'After RP PDF upload, AI analyzes strengths, issues, and improvements.')}</p>", unsafe_allow_html=True)

        cfg = load_config()
        rps = list_rps()
        preview_rp = None
        if rps:
            preview_rp_idx = st.session_state.get("rp_pick_idx", 0)
            if not isinstance(preview_rp_idx, int):
                preview_rp_idx = 0
            preview_rp_idx = max(0, min(preview_rp_idx, len(rps) - 1))
            preview_rp = rps[preview_rp_idx]

        rp_left, rp_right = st.columns([4.8, 5.2])
        with rp_left:
            rp_file = st.file_uploader(ui("上传 RP PDF", "Upload RP PDF"), type=["pdf"], accept_multiple_files=False, key="rp_auto_uploader")
            st.markdown(f"#### {ui('已上传RP', 'Uploaded RPs')}")
            if not rps:
                st.info(ui("还没有RP，请先上传。", "No RP files yet. Upload one to get started."))
            else:
                def _rp_label(i):
                    fn = str(rps[i].get("filename", "RP"))
                    return fn if len(fn) <= 28 else (fn[:25] + "...")

                c_pick, c_del = st.columns([3.2, 1.3])
                with c_pick:
                    ridx = st.selectbox(ui("选择RP", "Select RP"), range(len(rps)), format_func=_rp_label, key="rp_pick_idx", label_visibility="collapsed")
                sel_rp = rps[ridx]
                rp_id = sel_rp.get("id")

                with c_del:
                    st.markdown("<div style='height:2px'></div>", unsafe_allow_html=True)
                    if st.button(ui("删除", "Delete"), key=f"rp_del_icon_{rp_id}", help=ui("删除这份RP", "Delete this RP"), type="primary", use_container_width=True):
                        st.session_state["rp_del_pending_id"] = rp_id

                if st.session_state.get("rp_del_pending_id") == rp_id:
                    st.warning(ui(f"确认删除：{sel_rp.get('filename', 'RP')} ?", f"Confirm deletion: {sel_rp.get('filename', 'RP')} ?"))
                    d1, d2 = st.columns(2)
                    with d1:
                        if st.button(ui("取消", "Cancel"), key=f"rp_del_cancel_{rp_id}", use_container_width=True):
                            st.session_state.pop("rp_del_pending_id", None)
                            st.rerun()
                    with d2:
                        if st.button(ui("确认删除", "Confirm Delete"), key=f"rp_del_confirm_{rp_id}", use_container_width=True, type="primary"):
                            delete_rp(rp_id)
                            if cfg.get("active_rp_id") == rp_id:
                                cfg["active_rp_id"] = ""
                                cfg["rp_analysis"] = {}
                                save_config(cfg)
                            st.session_state.pop("rp_del_pending_id", None)
                            st.success(ui("已删除。", "Deleted."))
                            st.rerun()
        with rp_right:
            st.markdown(f"#### {ui('RP缩略图', 'RP Thumbnail')}")
            if rps:
                thumb_idx = st.session_state.get("rp_pick_idx", 0)
                if not isinstance(thumb_idx, int):
                    thumb_idx = 0
                thumb_idx = max(0, min(thumb_idx, len(rps) - 1))
                thumb_rp = rps[thumb_idx]
                preview_rp_id = thumb_rp.get("id", "")
                preview_rp_path = thumb_rp.get("path", "")
                rp_thumb_w = 150
                rp_thumb_png = build_pdf_thumbnail_png(pdf_path=preview_rp_path, width=rp_thumb_w) if preview_rp_path else b""
                if rp_thumb_png:
                    rp_thumb_b64 = base64.b64encode(rp_thumb_png).decode("utf-8")
                    st.markdown(
                        f"""
                        <a href="?rp_preview={preview_rp_id}" style="text-decoration:none;">
                            <img src="data:image/png;base64,{rp_thumb_b64}" style="width:{rp_thumb_w}px;height:190px;object-fit:contain;background:#fff;border:1px solid #ddd;border-radius:8px;cursor:pointer;display:block;" />
                        </a>
                        """,
                        unsafe_allow_html=True,
                    )
                    st.caption(ui("点击缩略图可打开预览", "Click thumbnail to preview"))
                else:
                    st.caption(ui("当前RP暂无可用缩略图", "No thumbnail available for this RP"))
            else:
                st.caption(ui("上传RP后显示缩略图", "Thumbnail appears after upload"))
        if rp_file is not None:
            file_bytes = rp_file.getvalue()
            file_sha = hashlib.sha1(file_bytes).hexdigest()
            if st.session_state.get("rp_last_upload_sha") != file_sha:
                with st.spinner("thinking... 正在解析并分析RP"):
                    ok, rec, err = add_rp(rp_file.name, file_bytes)
                    if ok and rec:
                        ai_cfg = dict(cfg)
                        ai_cfg["app_lang"] = st.session_state.get("app_lang", "zh-CN")
                        ok_a, result, raw = generate_rp_analysis(rec.get("text", ""), ai_cfg)
                        if ok_a:
                            cfg["active_rp_id"] = rec.get("id")
                            cfg["rp_analysis"] = result
                            cfg["rp_analysis_updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            save_config(cfg)
                        st.session_state["rp_last_upload_sha"] = file_sha
                    else:
                        st.error(ui(f"{rp_file.name} 保存失败：{err}", f"{rp_file.name} failed to save: {err}"))
                if ok and rec:
                    st.success(ui("上传成功", "Upload successful"))
                    st.rerun()

        if rps:
            rp_sel_idx = st.session_state.get("rp_pick_idx", 0)
            if not isinstance(rp_sel_idx, int):
                rp_sel_idx = 0
            rp_sel_idx = max(0, min(rp_sel_idx, len(rps) - 1))
            sel_rp = rps[rp_sel_idx]
            rp_id = sel_rp.get("id")
            rp_pdf_path = sel_rp.get("path", "")
            if not (preview_rp and str(preview_rp.get("id", "")) == str(rp_id)):
                if st.button(ui("打开RP预览", "Open RP Preview"), key=f"rp_open_fallback_{rp_id}"):
                    show_resume_pdf_modal(rp_pdf_path, sel_rp.get("filename", "RP"))

            rp_preview_id = st.query_params.get("rp_preview", "")
            if isinstance(rp_preview_id, list):
                rp_preview_id = rp_preview_id[0] if rp_preview_id else ""
            if str(rp_preview_id) == str(rp_id):
                show_resume_pdf_modal(rp_pdf_path, sel_rp.get("filename", "RP"))
                try:
                    st.query_params.pop("rp_preview")
                except Exception:
                    try:
                        del st.query_params["rp_preview"]
                    except Exception:
                        pass

            active_rp_id = cfg.get("active_rp_id", "")
            if active_rp_id:
                active_rp = get_rp(active_rp_id)
                if active_rp:
                    st.caption(ui(f"当前使用RP：{active_rp.get('filename')} | 上传时间：{active_rp.get('uploaded_at')}",
                                  f"Active RP: {active_rp.get('filename')} | Uploaded: {active_rp.get('uploaded_at')}"))

            rp_analysis = cfg.get("rp_analysis", {})
            if isinstance(rp_analysis, dict) and rp_analysis:
                rp_strengths = rp_analysis.get("good_points", []) or rp_analysis.get("strengths", [])
                rp_weaknesses = rp_analysis.get("weaknesses", [])
                rp_improvements = rp_analysis.get("improvements", []) or rp_analysis.get("suggestions", [])
                render_analysis_modules(
                    ui("AI RP 分析", "AI RP Analysis"),
                    [
                        ("✅", ui("优点", "Strengths"), rp_strengths),
                        ("⚠️", ui("缺点", "Weaknesses"), rp_weaknesses),
                        ("🛠️", ui("改进建议", "Improvements"), rp_improvements),
                    ],
                )

    elif menu == t("menu_dashboard"):
        st.title(ui("套瓷进度大盘", "Outreach Dashboard"))
        st.markdown(f"<p style='color: #9aa9ca; margin-bottom: 2rem;'>{ui('可视化管理你与各大院校导师的沟通时间线。', 'Visualize and manage your communication timeline with professors.')}</p>", unsafe_allow_html=True)
        
        recent_stats = get_recent_7d_email_stats()
        total_stats = get_total_email_stats()
        recent_scheduled_count = get_recent_7d_scheduled_interviews_count()
        interview_scheduled_total = len([r for r in load_db() if r.get("阶段") == "面试预约阶段"])

        st.markdown(f"### 📈 {ui('最近 7 天套瓷沟通指标', 'Last 7 Days Outreach Metrics')}")
        k1, k2, k3, k4, k5, k6 = st.columns(6)
        k1.metric(ui("已发送套瓷信", "Inquiries Sent"), recent_stats["sent_inquiry"])
        k2.metric(ui("收到回复", "Replies"), recent_stats["replied_total"])
        k3.metric(ui("收到积极回复", "Positive"), recent_stats["positive_reply"])
        k4.metric(ui("收到消极回复", "Negative"), recent_stats["negative_reply"])
        k5.metric(ui("收到中立回复", "Neutral"), recent_stats["neutral_reply"])
        k6.metric(ui("已预约面试", "Interviews Scheduled"), recent_scheduled_count)

        st.markdown(f"### 📚 {ui('累计套瓷沟通指标', 'All-Time Outreach Metrics')}")
        t1, t2, t3, t4, t5, t6 = st.columns(6)
        t1.metric(ui("累计已发送", "Sent"), total_stats["sent_inquiry"])
        t2.metric(ui("累计收到回复", "Replies"), total_stats["replied_total"])
        t3.metric(ui("累计积极回复", "Positive"), total_stats["positive_reply"])
        t4.metric(ui("累计消极回复", "Negative"), total_stats["negative_reply"])
        t5.metric(ui("累计中立回复", "Neutral"), total_stats["neutral_reply"])
        t6.metric(ui("已预约面试", "Interviews Scheduled"), interview_scheduled_total)
        st.divider()
        
        # 加载数据
        df = get_dashboard_data()
        
        if df.empty:
            st.info(t("dashboard_empty"))
        else:
            import plotly.express as px
            
            # Prepare data
            if "创建时间" not in df.columns:
                df["创建时间"] = df.get("更新时间", datetime.now().strftime("%Y-%m-%d"))
                
            # Date calculations
            today = datetime.now()
            seven_days_ago = today - timedelta(days=7)
            
            df['创建日期'] = pd.to_datetime(df['创建时间']).dt.date
            recent_df = df[df['创建日期'] > seven_days_ago.date()]
            
            # Chart 1: 7-day creations
            daily_creates = recent_df.groupby('创建日期').size().reset_index(name='发信数量')
            # Fill missing dates
            date_range = pd.date_range(end=today.date(), periods=7).date
            daily_creates = daily_creates.set_index('创建日期').reindex(date_range, fill_value=0).reset_index()
            daily_creates.rename(columns={'创建日期': '日期'}, inplace=True)
            if 'index' in daily_creates.columns:
                daily_creates.rename(columns={'index': '日期'}, inplace=True)
            
            fig_bar = px.bar(daily_creates, x='日期', y='发信数量', title="📊 最近 7 天套瓷发送数量 (按创建时间)", 
                             color_discrete_sequence=['#3b82f6'], text='发信数量')
            fig_bar.update_layout(xaxis_title="日期", yaxis_title="发信数量", plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
            
            # Chart 2: Global Map
            import pycountry
            # Define country mapping helper
            def get_iso3(country_name):
                try:
                    c = pycountry.countries.search_fuzzy(country_name)
                    return c[0].alpha_3
                except:
                    # fallback explicit maps
                    mapping = {"United States": "USA", "USA": "USA", "United Kingdom": "GBR", "UK": "GBR", 
                               "China": "CHN", "Hong Kong": "HKG", "Singapore": "SGP", "Canada": "CAN", 
                               "Australia": "AUS"}
                    for k, v in mapping.items():
                        if k.lower() in str(country_name).lower(): return v
                    return None
            
            df['iso_alpha'] = df['国家/地区'].apply(get_iso3)
            recent_df = df[df['创建日期'] > seven_days_ago.date()]
            
            country_stats = df.groupby(['国家/地区', 'iso_alpha']).size().reset_index(name='总套瓷数')
            recent_country_stats = recent_df.groupby(['国家/地区', 'iso_alpha']).size().reset_index(name='本周新增')
            
            map_df = pd.merge(country_stats, recent_country_stats, on=['国家/地区', 'iso_alpha'], how='left').fillna(0)
            map_df['hover_text'] = map_df['国家/地区'] + "<br>总套瓷数: " + map_df['总套瓷数'].astype(str) + "<br>本周新增: " + map_df['本周新增'].astype(str)
            
            import plotly.graph_objects as go
            fig_map = px.choropleth(map_df, locations="iso_alpha",
                                    color="总套瓷数", hover_name="hover_text",
                                    color_continuous_scale=px.colors.sequential.YlOrRd,
                                    range_color=[1, 100],
                                    title="🌍 全球套瓷地区分布图")
            
            # 标出 Top 5 并在地图上醒目突出（解决小面积地区在填色地图上看不见的问题）
            top_5 = map_df.nlargest(5, '总套瓷数')
            fig_map.add_trace(go.Scattergeo(
                locations=top_5['iso_alpha'],
                text=top_5['国家/地区'] + " (" + top_5['总套瓷数'].astype(str) + ")",
                mode='markers+text',
                marker=dict(size=12, color='#10b981', line=dict(width=2, color='white')),
                textfont=dict(color='white', size=13, weight='bold'),
                textposition="bottom center",
                showlegend=False
            ))

            fig_map.update_layout(geo=dict(showframe=False, showcoastlines=True, bgcolor='rgba(0,0,0,0)'),
                                  paper_bgcolor='rgba(0,0,0,0)')
                                  
            c_chart1, c_chart2 = st.columns(2)
            with c_chart1:
                st.plotly_chart(fig_bar, use_container_width=True)
            with c_chart2:
                st.plotly_chart(fig_map, use_container_width=True)
                
            st.divider()
        
        st.subheader(t("active_applications"))
        
        # 为每位导师渲染卡片
        for index, row in df.iterrows():
            
            c1, c2, c3 = st.columns([1, 2, 1])
            with c1:
                prof_name = row.get('导师/教授', '未知导师')
                homepage = row.get('主页链接', '')
                if isinstance(homepage, str) and homepage.strip():
                    st.markdown(f"### <a href='{homepage}' target='_blank' style='text-decoration:none; color:inherit;'>{prof_name}</a>", unsafe_allow_html=True)
                else:
                    st.markdown(f"### {prof_name}")
                st.markdown(f"**🏛️ {row.get('学校名称', '未知学校')}**")
                st.markdown(f"<span class='tag'>{row.get('推荐级', '未知')} 级</span>", unsafe_allow_html=True)
                
            with c2:
                # 渲染用户要求的进度条
                render_status_bar(row.get('阶段', '未联系'), row.get('面试时间', ''))
                
            with c3:
                st.markdown(f"**🔬 研究方向:**<br><span style='font-size:15px; color:#d6e4ff; font-weight:600;'>{row.get('研究方向', '未明确')}</span>", unsafe_allow_html=True)
                st.markdown(f"**⏱️ 最后互动:** {row.get('更新时间', '')}")
                st.markdown(f"**🕒 导师当地时间:** {format_local_time(row.get('国家/地区', ''))}")
                # 操作按钮
                btn_col1, btn_col2 = st.columns(2)
                with btn_col1:
                    if st.button(ui("👁️ 查看详情", "👁️ Details"), key=f"btn_{index}", use_container_width=True):
                        show_professor_details(row)
                with btn_col2:
                    if st.button(ui("🗑️ 删除记录", "🗑️ Delete"), key=f"del_dash_{index}", use_container_width=True, type="secondary"):
                        confirm_delete_dialog(row.get("导师/教授"), row.get("学校名称"))
            
            
    elif menu == t("menu_email"):
        st.title(t("email_center"))
        config = load_config()
        if not config.get("email") or not config.get("password"):
            st.warning(t("no_email_config"))
        else:
            col_ctrl_1, col_ctrl_2, _ = st.columns([0.85, 1.15, 4.0])
            with col_ctrl_1:
                st.caption(t("show_emails_count"))
                limit = st.selectbox(
                    t("show_emails_count"),
                    [5, 10, 15, 30, 50],
                    index=2,
                    label_visibility="collapsed",
                )
            with col_ctrl_2:
                st.markdown("<div style='height: 2.08rem;'></div>", unsafe_allow_html=True)
                if st.button(ui("手动拉取最新邮件", "Fetch Latest Emails"), use_container_width=True):
                    with st.spinner(t("fetching_info")):
                        fetch_once()
                    st.rerun()
            
            with st.spinner(t("reading_cache")):
                success, emails = get_cached_emails(limit)
                
            if not success:
                st.error(ui(f"拉取失败: {emails}", f"Fetch failed: {emails}"))
            elif not emails:
                st.info(t("inbox_empty"))
            else:
                db = load_db()
                marked_emails = {}
                for record in db:
                    if "关联邮件ID" in record and record["关联邮件ID"]:
                        for mid in str(record["关联邮件ID"]).split(","):
                            marked_emails[mid.strip()] = record.get("导师/教授")

                col_list, col_mail, col_form = st.columns([1, 1.8, 1])
                
                with col_list:
                    st.subheader(t("inbox_list"))
                    
                    # Radio button layout formatting with marked icon
                    def format_email_label(idx):
                        subj = emails[idx]['subject'][:15].replace('\n', ' ')
                        m_id = emails[idx]['id']
                        if m_id in marked_emails:
                            return f"✅ {subj}..."
                        if emails[idx].get('is_phd_related'):
                            return f"🎓 {subj}..."
                        return f"📩 {subj}..."
                        
                    selected_idx = st.radio(t("switch_email"), range(len(emails)), format_func=format_email_label, label_visibility="collapsed", key="email_list_selector")
                
                mail = emails[selected_idx]
                mail_id = mail['id']
                
                with col_mail:
                    if mail_id in marked_emails:
                        col_alert, col_action = st.columns([4, 1.5])
                        with col_alert:
                            st.success(ui(f"🎉 **已提取！** 导师：`{marked_emails[mail_id]}`",
                                          f"🎉 **Extracted!** Professor: `{marked_emails[mail_id]}`"))
                        with col_action:
                            if st.button(t("cancel"), key=f"unmark_{mail_id}", use_container_width=True):
                                current_db = load_db()
                                for r in current_db:
                                    if "关联邮件ID" in r and r["关联邮件ID"]:
                                        mids = [m.strip() for m in str(r["关联邮件ID"]).split(",")]
                                        if mail_id in mids:
                                            mids.remove(mail_id)
                                            r["关联邮件ID"] = ",".join(mids)
                                new_db = current_db
                                save_db(new_db)
                                st.rerun()
                    
                    st.markdown(f"### {mail['subject']}")
                    
                    # Manual category override
                    CAT_NAMES = {
                        0: "非博士申请相关邮件 (Not PhD Related)",
                        1: "已发送询问信 (Sent Inquiry)",
                        2: "得到导师积极回复 (Positive Reply)",
                        3: "得到导师消极回复 (Negative Reply)",
                        4: "得到导师中立回复 (Neutral Reply)",
                        5: "面试预约 (Interview Scheduling)",
                        6: "面试结果告知 (Interview Result)",
                        7: "口头offer (Verbal Offer)",
                        8: "其他沟通 (Other Communication)"
                    }
                    
                    current_cat = mail.get('phd_category') if mail.get('is_phd_related') and mail.get('phd_category') in CAT_NAMES else 0
                    
                    new_cat = st.selectbox(ui("📌 邮件分类状态", "📌 Email Category"), 
                                           options=list(CAT_NAMES.keys()), 
                                           format_func=lambda x: CAT_NAMES[x],
                                           index=list(CAT_NAMES.keys()).index(current_cat),
                                           key=f"cat_override_{mail_id}")
                                           
                    if new_cat != current_cat:
                        updated = False
                        if os.path.exists(EMAILS_CACHE_FILE):
                            try:
                                with open(EMAILS_CACHE_FILE, "r", encoding="utf-8") as f:
                                    cache_data = json.load(f)
                                    if cache_data.get("success"):
                                        for cache_mail in cache_data.get("emails", []):
                                            if str(cache_mail.get("id")) == str(mail_id):
                                                if new_cat == 0:
                                                    cache_mail["is_phd_related"] = False
                                                    cache_mail["phd_category"] = None
                                                else:
                                                    cache_mail["is_phd_related"] = True
                                                    cache_mail["phd_category"] = new_cat
                                                updated = True
                                                break
                                        if updated:
                                            with open(EMAILS_CACHE_FILE, "w", encoding="utf-8") as fw:
                                                json.dump(cache_data, fw, ensure_ascii=False)
                                            st.toast(ui("✅ 状态已自动修改并保存！即将刷新...", "✅ Category updated and saved. Refreshing..."))
                                            import time
                                            time.sleep(0.5)
                                            st.rerun()
                            except Exception as e:
                                st.error(ui(f"保存失败: {str(e)}", f"Save failed: {str(e)}"))
                        if not updated:
                            st.warning(ui("缓存中难以修改数据。", "Failed to update cache data."))

                    # Using container to box the headers
                    with st.container():
                        st.caption(f"**From:** `{mail['from']}`\n\n**To:** `{mail['to']}`\n\n**Time:** `{mail['date']}`")
                        st.markdown("---")
                        st.write(t("email_body"))
                        st.text_area(t("email_body"), mail['body'], height=450, key=f"email_body_{mail_id}", label_visibility="collapsed")
                        st.caption(ui("👈 最左侧菜单栏可以点击顶部的 `>` 或 `X` 隐藏以获得更大视野。",
                                      "👈 Click `>` or `X` on the left sidebar to hide it for a wider workspace."))
                        
                        # Phase 3 Verification Display
                        phd_details = mail.get("phd_details", {})
                        verification_result = phd_details.get("verification_result") if isinstance(phd_details, dict) else None
                        if verification_result:
                            st.markdown(f"### {ui('🕷️ URL防幻觉网页抓取与二次审核', '🕷️ URL Hallucination Check & Verification')}")
                            st.info(ui(f"**尝试抓取的导师主页:** {phd_details.get('scraped_url', '')}",
                                       f"**Fetched URL:** {phd_details.get('scraped_url', '')}"))
                            scraped_text = verification_result.get("scraped_text", "")
                            if scraped_text:
                                with st.expander(ui("📄 爬取到的网页纯净脱水文本 (点击查看)", "📄 Cleaned Web Text (click to view)")):
                                    st.code(scraped_text, language="text")
                            
                            st.markdown(f"#### {ui('🧠 AI 审查与提取过程', '🧠 AI Review & Extraction')}")
                            is_real = verification_result.get('is_real_homepage')
                            ai_reasoning = verification_result.get('reasoning', '')
                            ai_keywords = verification_result.get('research_keywords', '')
                            
                            if is_real:
                                st.success(ui(f"**验证通过!**\n\n**审查判断:** {ai_reasoning}\n\n**提取到的最新关键词:** {ai_keywords}",
                                              f"**Verified!**\n\n**Reasoning:** {ai_reasoning}\n\n**Extracted Keywords:** {ai_keywords}"))
                            else:
                                st.error(ui(f"**验证驳回! (幻觉或死链)**\n\n**驳回原因:** {ai_reasoning}\n\n系统已自动清空该错误的主页链接。",
                                            f"**Verification Rejected (hallucination or dead link)**\n\n**Reason:** {ai_reasoning}\n\nThe invalid homepage URL has been cleared automatically."))

                    st.markdown("---")
                    reasoning_col, thinking_col = st.columns(2)
                    
                    with reasoning_col:
                        st.markdown(f"### {ui('🔍 邮件分类分析', '🔍 Email Classification')}")
                        reasoning = mail.get("phd_reasoning")
                        if reasoning:
                            st.info(reasoning)
                            
                    with thinking_col:
                        st.markdown(f"### {ui('🧠 信息抽取分析', '🧠 Information Extraction')}")
                        thinking_box = st.empty()
                        if f"thinking_{mail_id}" in st.session_state:
                            thinking_box.success(st.session_state[f"thinking_{mail_id}"])
                    
                with col_form:
                    st.subheader(t("tagging_card"))
                    if mail_id in marked_emails:
                        st.info(t("prof_in_db"))
                    else:
                        st.info(t("quick_fill"))
                        
                    # 读取全球大学动态数据
                    # === 【新增：Gemini 智能解析与信息填充大屏】 ===
                    config = load_config()
                    ai_provider = config.get("ai_provider", "通义千问 (Qwen)")
                    api_key = config.get("gemini_api_key", "")
                    qwen_key = config.get("qwen_api_key", "")
                    btn_label = ui("提取信息", "Extract Info")
                    cat_id = mail.get('phd_category')
                    
                    top_default_url = ""
                    p_details = mail.get("phd_details", {})
                    if isinstance(p_details, dict):
                        t_url = p_details.get("verified_homepage", "")
                        if t_url and t_url != "None":
                            top_default_url = t_url
                    
                    st.info(ui("💡 **提取必备**：为了精准抽取导师档案并将内容入库，本系统限制必须通过导师官方网页抓取。\n**请先在此提供真实的导师主页链接**：",
                               "💡 **Required for extraction**: To ensure reliable professor profiling and storage, extraction is limited to official webpages.\n**Please provide a real homepage URL first**:"))
                    hp_input_col, hp_btn_col = st.columns([2.9, 1.1])
                    with hp_input_col:
                        manual_hp_url = st.text_input(
                            ui("导师主页链接（用于大模型推理读取）", "Professor Homepage URL (for model extraction)"),
                            value=top_default_url,
                            key=f"ai_manual_hp_input_{mail_id}",
                            placeholder=ui("必须要填，以 http 开头", "Required, starts with http/https"),
                        )

                    with hp_btn_col:
                        st.markdown("<div style='height: 0.15rem;'></div>", unsafe_allow_html=True)
                        ai_extract_clicked = st.button(btn_label, key=f"ai_btn_{mail_id}", type="primary", use_container_width=True)

                    if ai_extract_clicked:
                        if not manual_hp_url.startswith("http"):
                            st.warning(ui("⚠️ 必须要填写真实的导师个人主页链接 (请以 http 或 https 开头) 才能进行解析并展示录入表单！",
                                          "⚠️ A valid professor homepage URL (http/https) is required for extraction."))
                        elif ai_provider == "Google Gemini" and not api_key:
                            st.warning(ui("⚠️ 请先前往【系统配置】填写 Gemini API Key", "⚠️ Please set Gemini API Key in System Config first."))
                        elif ai_provider == "通义千问 (Qwen)" and not qwen_key:
                            st.warning(ui("⚠️ 请先前往【系统配置】填写 通义千问 API Key", "⚠️ Please set Qwen API Key in System Config first."))
                        else:
                            with st.spinner(f"🚀 {ai_provider} 正在阅读邮件、分析背景..."):
                                try:
                                    if ai_provider == "Google Gemini":
                                        genai.configure(api_key=api_key)

                                    web_text = ""
                                    raw_html = ""
                                    if manual_hp_url.startswith("http"):
                                        st.session_state[f"thinking_{mail_id}"] = f"**正在请求网页：** `{manual_hp_url}`...\n\n"
                                        thinking_box.info(st.session_state[f"thinking_{mail_id}"])
                                        import urllib.request
                                        import re
                                        try:
                                            req = urllib.request.Request(manual_hp_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0)'})
                                            html = urllib.request.urlopen(req, timeout=10).read().decode('utf-8', errors='ignore')
                                            raw_html = html
                                            t_text = re.sub(r'<style.*?>.*?</style>', '', html, flags=re.DOTALL|re.IGNORECASE)
                                            t_text = re.sub(r'<script.*?>.*?</script>', '', t_text, flags=re.DOTALL|re.IGNORECASE)
                                            t_text = re.sub(r'<[^>]+>', ' ', t_text)
                                            t_text = re.sub(r'\s+', ' ', t_text).strip()
                                            web_text = t_text[:5000]
                                            st.session_state[f"thinking_{mail_id}"] += f"✅ **成功爬取纯净网页文本({len(web_text)}字符)。开始投喂大模型...**\n\n---\n"
                                            thinking_box.info(st.session_state[f"thinking_{mail_id}"])
                                        except Exception as e:
                                            st.session_state[f"thinking_{mail_id}"] += f"❌ 网页请求失败: {str(e)}\n\n---\n"
                                            thinking_box.error(st.session_state[f"thinking_{mail_id}"])
                                            
                                        prompt = f"""
请阅读下面提取出的导师主页纯文本信息，以及通讯邮件上下文。请以此抽取出该教授的档案详情。
你不需要阐述搜索推理，提取完后，请直接输出以下 JSON 对象，且用 ```json 包裹：
{{
    "name": "从网页或邮件中提取教授的名字（纯英文）",
    "country": "该大学所在国家（比如 United States, Hong Kong 等纯英文首字母大写）",
    "university": "大学官方正式全称",
    "department": "导师所在的学院/院系/专业",
    "email": "请判断导师邮箱来自发件人还是收件人。值严格返回 'from' 或 'to' 或留空。",
    "homepage": "{manual_hp_url}",
    "research": "导师的研究方向、兴趣或实验室名字（提取3-5个关键词）"
}}

邮件通讯上下文：
发件人 (From): {mail.get('from', '')}
收件人 (To): {mail.get('to', '')}
主题 (Subject): {mail.get('subject', '')}

导师网页文本 (前5000字符):
{web_text}
"""
                                    else:
                                        prompt = f"""
                                        请阅读这封留学生申请博士或套瓷的上下文邮件。帮我提取这名指导教授的完整信息。
                                        请务必结合你的知识库或在线搜索功能（如果被启用）查找到这位教授的官方学术主页或实验室网站。
                                        你可以先简略用文本阐述你的搜索和推理过程，然后把最终完整信息放入 ```json 代码块中返回。
                                        JSON 应严格遵循以下字段（值必须全为字符串）：
                                        {{
                                            "name": "教授的名字（纯英文）",
                                            "country": "该大学所在国家（比如 United States, Hong Kong 等）",
                                            "university": "大学官方正式全称",
                                            "department": "导师所在的学院/院系/专业（例：Computer Science）",
                                            "email": "请判断导师邮箱来自发件人还是收件人。值严格返回 'from' 或 'to' 或留空",
                                            "homepage": "导师的学术个人主页或实验室网站链接（查不到就留空）",
                                            "research": "导师的研究方向、兴趣或实验室名字（提取3-5个关键词）"
                                        }}
                                        
                                        邮件通讯上下文：
                                        发件人 (From): {mail.get('from', '')}
                                        收件人 (To): {mail.get('to', '')}
                                        主题 (Subject): {mail.get('subject', '')}
                                        邮件原文：
                                        {str(mail['body'])[:2000]}
                                        """
                                    
                                    # Request generation
                                    res_text = ""
                                    
                                    if ai_provider == "通义千问 (Qwen)":
                                        client = OpenAI(
                                            api_key=qwen_key, 
                                            base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
                                        )
                                        completion = client.chat.completions.create(
                                            model="qwen-plus",
                                            messages=[{'role': 'user', 'content': prompt}],
                                            stream=True,
                                            extra_body={"enable_search": True}
                                        )
                                        for chunk in completion:
                                            if chunk.choices and chunk.choices[0].delta.content:
                                                res_text += chunk.choices[0].delta.content
                                                thinking_box.info(res_text + "▌")
                                        thinking_box.success(res_text)
                                    else:
                                        response, used_model = _gemini_generate_content_with_fallback(prompt, stream=True)
                                        st.session_state[f"thinking_{mail_id}"] += f"🤖 Gemini model: `{used_model}`\n\n"
                                        for chunk in response:
                                            chunk_text = getattr(chunk, "text", "") or ""
                                            if not chunk_text:
                                                continue
                                            res_text += chunk_text
                                            thinking_box.info(res_text + "▌")
                                        thinking_box.success(res_text)
                                                
                                    # Ensure the thinking process is always saved before strict parsing
                                    st.session_state[f"thinking_{mail_id}"] = res_text
                                    
                                    # Parse json text
                                    text = res_text
                                    if '```json' in text:
                                        text = text.split('```json')[1].split('```')[0].strip()
                                    elif '```' in text:
                                        text = text.replace('```', '').strip()
                                        
                                    data = json.loads(text)
                                    valid_res = {}
                                    
                                    # [Phase 3 UI Interaction: URL Verification]
                                    inferred_hp = data.get("homepage", "")
                                    if cat_id != 1 and inferred_hp and inferred_hp != "None" and inferred_hp.startswith("http"):
                                        st.session_state[f"thinking_{mail_id}"] += f"\n\n---\n\n### 🕷️ URL防幻觉探针介入\n\nAI 首次回答中提供了 URL: `{inferred_hp}`。\n\n**正在发起代码级探针爬取...**\n"
                                        thinking_box.info(st.session_state[f"thinking_{mail_id}"])
                                        
                                        valid_res = verify_professor_homepage(inferred_hp, mail.get("to", ""), config)
                                        scraped = valid_res.get("scraped_text", "")
                                        
                                        st.session_state[f"thinking_{mail_id}"] += f"\n**抓取成功! 获得纯净脱水正文 {len(scraped)} 字符。**\n\n> {scraped[:200]}...\n\n"
                                        thinking_box.info(st.session_state[f"thinking_{mail_id}"])
                                        
                                        # Show Secondary AI verification result
                                        ai_reasoning = valid_res.get("reasoning", "")
                                        if valid_res.get("is_real_homepage"):
                                            st.session_state[f"thinking_{mail_id}"] += f"✅ **AI 二次确权通过!**\n- **推理过程:** {ai_reasoning}\n- **重新总结研究点:** {valid_res.get('research_keywords', '')}"
                                            thinking_box.success(st.session_state[f"thinking_{mail_id}"])
                                            
                                            # Override with more accurate data
                                            data["research"] = valid_res.get("research_keywords", data.get("research"))
                                        else:
                                            st.session_state[f"thinking_{mail_id}"] += f"\n\n❌ **AI 二次确权驳回 (属于假连接或无权限):**\n- {ai_reasoning}\n\n系统已强制清空该幻觉 URL。"
                                            thinking_box.error(st.session_state[f"thinking_{mail_id}"])
                                            data["homepage"] = ""
                                    
                                    # Fill form state
                                    def _safe_str(val):
                                        if isinstance(val, list): return ", ".join(str(v) for v in val)
                                        return "" if val is None else str(val)
                                    
                                    st.session_state[f"prof_{mail_id}"] = _safe_str(data.get("name", ""))
                                    
                                    import re
                                    def extract_email_address(s):
                                        if not s:
                                            return ""
                                        matches = re.findall(
                                            r'(?i)\b[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}\b',
                                            str(s),
                                        )
                                        if not matches:
                                            return ""
                                        return str(matches[0]).strip()

                                    def infer_professor_side():
                                        # 1) Priority: use existing categorized mail type
                                        if cat_id == 1:
                                            return "to"
                                        if cat_id in [2, 3, 4, 5, 6, 7, 8]:
                                            return "from"

                                        # 2) Fallback: use AI judgement from extraction JSON
                                        ai_side = _safe_str(data.get("email", "")).lower().strip()
                                        if ai_side in ["from", "to"]:
                                            return ai_side

                                        # 3) Fallback: lightweight content heuristic
                                        body_text = str(mail.get("body", "") or "").lower()
                                        sent_cues = [
                                            "dear professor",
                                            "i am interested in",
                                            "i would like to apply",
                                            "my cv",
                                            "my research proposal",
                                            "申请博士",
                                            "套磁",
                                        ]
                                        reply_cues = [
                                            "thank you for your email",
                                            "thanks for reaching out",
                                            "unfortunately",
                                            "interview",
                                            "we can schedule",
                                            "best regards",
                                        ]
                                        sent_score = sum(1 for c in sent_cues if c in body_text)
                                        reply_score = sum(1 for c in reply_cues if c in body_text)
                                        return "to" if sent_score >= reply_score else "from"

                                    professor_side = infer_professor_side()
                                    if professor_side == "to":
                                        extracted_email = extract_email_address(mail.get("to", ""))
                                    else:
                                        extracted_email = extract_email_address(mail.get("from", ""))
                                        
                                    st.session_state[f"prof_email_{mail_id}"] = extracted_email
                                    st.session_state[f"dept_{mail_id}"] = _safe_str(data.get("department", ""))
                                    st.session_state[f"hp_{mail_id}"] = _safe_str(data.get("homepage", ""))
                                    st.session_state[f"dir_{mail_id}"] = _safe_str(data.get("research", ""))
                                    
                                    ai_country = data.get("country", "")
                                    ai_univ = data.get("university", "")
                                    world_univ_data = get_world_universities()
                                    
                                    matched_country = "🔽 [不在列表中] 手动补充"
                                    matched_univ = "不在对应院校列表中..."
                                    
                                    if ai_country:
                                        for c_key in sorted(list(world_univ_data.keys())):
                                            if ai_country.lower() in c_key.lower() or c_key.lower() in ai_country.lower():
                                                matched_country = c_key
                                                break
                                                
                                    if matched_country != "🔽 [不在列表中] 手动补充" and ai_univ:
                                        univ_list = world_univ_data.get(matched_country, [])
                                        for u in univ_list:
                                            if ai_univ.lower() == u.lower() or ai_univ.lower() in u.lower() or u.lower() in ai_univ.lower() or ai_univ.replace("The ", "").lower() in u.lower():
                                                matched_univ = u
                                                break
                                                
                                    st.session_state[f"country_{mail_id}"] = matched_country
                                    if matched_country != "🔽 [不在列表中] 手动补充":
                                        if matched_univ != "不在对应院校列表中...":
                                            st.session_state[f"univ_{mail_id}"] = matched_univ
                                            st.session_state[f"mc_{mail_id}"] = ""
                                            st.session_state[f"mu_{mail_id}"] = ""
                                        else:
                                            # 如果国家选出来了但学校没匹配上，依然切回手动模式，让用户检查
                                            st.session_state[f"country_{mail_id}"] = "🔽 [不在列表中] 手动补充"
                                            st.session_state[f"mc_{mail_id}"] = ai_country
                                            st.session_state[f"mu_{mail_id}"] = ai_univ
                                    else:
                                        st.session_state[f"mc_{mail_id}"] = ai_country
                                        st.session_state[f"mu_{mail_id}"] = ai_univ
                                    
                                    # Save extracted details directly to cache to prevent loss when switching tabs
                                    if os.path.exists(EMAILS_CACHE_FILE):
                                        try:
                                            with open(EMAILS_CACHE_FILE, "r", encoding="utf-8") as f:
                                                cache_data = json.load(f)
                                                if cache_data.get("success"):
                                                    for cache_mail in cache_data.get("emails", []):
                                                        if str(cache_mail.get("id")) == str(mail_id):
                                                            cache_mail["phd_details"] = {
                                                                "extracted_prof_name": st.session_state.get(f"prof_{mail_id}", ""),
                                                                "extracted_prof_email": st.session_state.get(f"prof_email_{mail_id}", ""),
                                                                "department": st.session_state.get(f"dept_{mail_id}", ""),
                                                                "verified_homepage": st.session_state.get(f"hp_{mail_id}", ""),
                                                                "research_direction": st.session_state.get(f"dir_{mail_id}", ""),
                                                                "country_guess": st.session_state.get(f"country_{mail_id}", ""),
                                                                "university_name": st.session_state.get(f"univ_{mail_id}", ""),
                                                                "manual_country": st.session_state.get(f"mc_{mail_id}", ""),
                                                                "manual_univ": st.session_state.get(f"mu_{mail_id}", ""),
                                                                "priority_guess": "T1 (平替)"
                                                            }
                                                            break
                                                    with open(EMAILS_CACHE_FILE, "w", encoding="utf-8") as fw:
                                                        json.dump(cache_data, fw, ensure_ascii=False)
                                        except:
                                            pass
                                            
                                    # Force rerender form
                                    st.rerun()
                                    
                                except Exception as e:
                                    st.error(ui(f"❌ 解析未成功，可能是 API 密钥无效或解析格式错误: {str(e)}",
                                                f"❌ Extraction failed. Possible invalid API key or malformed response: {str(e)}"))
                    # ===================================================

                    world_univ_data = get_world_universities()
                    country_list = ["🔽 [不在列表中] 手动补充"] + sorted(list(world_univ_data.keys()))
                    
                    # 将这几个常申大国置顶显示以方便查找
                    priority_countries = ["United States", "United Kingdom", "Hong Kong", "Singapore", "Canada", "Australia", "China"]
                    found_priorities = []
                    for pc in priority_countries:
                        for cl in country_list:
                            if pc in cl:
                                found_priorities.append(cl)
                                break
                    for c in reversed(found_priorities):
                        if c in country_list:
                            country_list.remove(c)
                            country_list.insert(1, c) # 插在"手动补充"之后
                    
                    
                    # 自动带入AI分析结果
                    cat_id = mail.get('phd_category')
                    is_phd = mail.get('is_phd_related')
                    
                    import email.utils
                    default_action_idx = 0
                    default_status_idx = 0
                    default_prof_name = ""
                    default_prof_email = ""
                    default_prio = "T1 (平替)"
                    default_dept = ""
                    default_url = ""
                    default_dir = ""
                    default_country = None
                    default_univ = None
                    
                    if is_phd:
                        if cat_id == 1:
                            default_action_idx = 0 # 新建
                            parsed_name, parsed_email = email.utils.parseaddr(mail.get("to", ""))
                        else:
                            default_action_idx = 1 # 同步
                            cat_to_status = {2: 1, 3: 3, 4: 2, 5: 4, 6: 5, 7: 6}
                            default_status_idx = cat_to_status.get(cat_id, 0)
                            parsed_name, parsed_email = email.utils.parseaddr(mail.get("from", ""))
                        
                        default_prof_email = parsed_email
                        
                        # Leverage second pass info (for ANY category where extraction happened)
                        phd_details = mail.get("phd_details", {})
                        if phd_details:
                            if "extracted_prof_name" in phd_details and phd_details["extracted_prof_name"]:
                                default_prof_name = phd_details["extracted_prof_name"]
                            if "extracted_prof_email" in phd_details and phd_details["extracted_prof_email"]:
                                default_prof_email = phd_details["extracted_prof_email"]
                                
                            default_prio = phd_details.get("priority_guess", "T1 (平替)")
                            if default_prio not in ["T0 (强选)", "T1 (平替)", "T2 (保底)"]:
                                default_prio = "T1 (平替)"
                            default_dept = phd_details.get("department") if phd_details.get("department") != "None" else ""
                            default_url = phd_details.get("verified_homepage") if phd_details.get("verified_homepage") != "None" else ""
                            default_dir = phd_details.get("research_direction") if phd_details.get("research_direction") != "None" else ""
                            
                            c_g = phd_details.get("country_guess")
                            if c_g and c_g != "None": default_country = c_g
                            u_g = phd_details.get("university_name")
                            if u_g and u_g != "None": default_univ = u_g
                            
                            # Store manual values in session state directly if they exist so text inputs grab them
                            if "manual_country" in phd_details and f"mc_{mail_id}" not in st.session_state:
                                st.session_state[f"mc_{mail_id}"] = phd_details["manual_country"]
                            if "manual_univ" in phd_details and f"mu_{mail_id}" not in st.session_state:
                                st.session_state[f"mu_{mail_id}"] = phd_details["manual_univ"]
                            
                    current_db = load_db()
                    if default_action_idx == 1 and not current_db:
                        default_action_idx = 0 # 数据库为空，强制回退至新建模式
                    
                    # 动态级联选择不使用 st.form，使用普通 layout 以支持联动的交互刷新
                    action_mode = st.radio("🔖 记录操作模式", ["➕ 新建导师记录", "🔄 同步至已有导师"], index=default_action_idx, horizontal=True, key=f"radio_mode_{mail_id}")
                    if action_mode == "➕ 新建导师记录":
                        with st.container():
                            col_p1, col_p2 = st.columns(2)
                            with col_p1:
                                prof_name = st.text_input(t("prof_name_req"), value=default_prof_name, placeholder=t("prof_name_ph"), key=f"prof_{mail_id}")
                            with col_p2:
                                prof_email = st.text_input("导师邮箱地址", value=default_prof_email, placeholder="example@univ.edu", key=f"prof_email_{mail_id}")
                            
                            c_idx = 0
                            if default_country in country_list: c_idx = country_list.index(default_country)
                            st.caption(t("target_country"))
                            selected_country = st.selectbox(
                                t("target_country"),
                                country_list,
                                index=c_idx,
                                key=f"country_{mail_id}",
                                label_visibility="collapsed",
                            )
                            
                            univ_options = ["不在对应院校列表中..."]
                            if selected_country != "🔽 [不在列表中] 手动补充":
                                univ_options = world_univ_data.get(selected_country, ["不在对应院校列表中..."])
                                
                            u_idx = 0
                            if default_univ in univ_options: u_idx = univ_options.index(default_univ)
                            st.caption(t("target_univ"))
                            selected_univ = st.selectbox(
                                t("target_univ"),
                                univ_options,
                                index=u_idx,
                                key=f"univ_{mail_id}",
                                label_visibility="collapsed",
                            )
                            
                            manual_country = ""
                            manual_univ = ""
                            if selected_country == "🔽 [不在列表中] 手动补充":
                                col_m1, col_m2 = st.columns([1, 2])
                                with col_m1:
                                    manual_country = st.text_input(t("manual_country"), value=default_country if default_country else "", placeholder="例: 荷兰", key=f"mc_{mail_id}")
                                with col_m2:
                                    manual_univ = st.text_input(t("manual_univ"), value=default_univ if default_univ else "", placeholder="例: TU Delft", key=f"mu_{mail_id}")
                                
                            department = st.text_input("院系/专业", value=default_dept, placeholder="例: CS / AI / EE", key=f"dept_{mail_id}")
                            
                            col_s1, col_s2 = st.columns(2)
                            with col_s1:
                                opts_p = ["T0 (强选)", "T1 (平替)", "T2 (保底)"]
                                p_i = opts_p.index(default_prio) if default_prio in opts_p else 1
                                priority = st.selectbox("意向推荐级", opts_p, index=p_i, key=f"prio_{mail_id}")
                            with col_s2:
                                status = st.selectbox("当前阶段", ["已发首封邮件", "收到积极回复", "收到中等回复", "收到消极回复", "面试预约阶段", "面试结束阶段", "口头offer"], index=default_status_idx, key=f"stat_{mail_id}")
                            interview_time = ""
                            if status == "面试预约阶段":
                                dft_date, dft_time = get_interview_picker_defaults(st.session_state.get(f"intv_{mail_id}", ""))
                                c_dt1, c_dt2 = st.columns(2)
                                with c_dt1:
                                    intv_date = st.date_input("面试日期", value=dft_date, key=f"intv_date_{mail_id}")
                                with c_dt2:
                                    intv_time = st.time_input("面试时间", value=dft_time, key=f"intv_time_{mail_id}")
                                interview_time = format_interview_time(intv_date, intv_time)
                                
                            # 强制使用顶部提取栏填写的链接或默认推断的链接，不在底部展示重复框
                            homepage = manual_hp_url if manual_hp_url else default_url
                            
                            direction = st.text_input("导师研究方向 (Keywords)", value=default_dir, key=f"dir_{mail_id}")
                            
                            if st.button("💾 保存进度至看板", use_container_width=True, key=f"submit_{mail_id}", type="primary"):
                                # 解析最终数据
                                final_country = manual_country.strip() if selected_country == "🔽 [不在列表中] 手动补充" else selected_country
                                final_univ = manual_univ.strip() if selected_country == "🔽 [不在列表中] 手动补充" else selected_univ
    
                                if prof_name and final_univ:
                                    current_db = load_db()
                                    
                                    from email.utils import parsedate_to_datetime
                                    try:
                                        mail_dt = parsedate_to_datetime(mail.get("date", "")).astimezone().strftime("%Y-%m-%d %H:%M:%S")
                                    except Exception:
                                        mail_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                                    new_row = {
                                        "导师/教授": prof_name,
                                        "导师邮箱": prof_email,
                                        "国家/地区": final_country if final_country else "未知",
                                        "学校名称": final_univ,
                                        "院系": department,
                                        "主页链接": homepage if homepage else "",
                                        "研究方向": direction if direction else "未明确",
                                        "推荐级": priority,
                                        "阶段": status,
                                        "面试时间": interview_time,
                                        "更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                        "创建时间": mail_dt,
                                        "关联邮件ID": mail_id
                                    }
                                    current_db.append(new_row)
                                    save_db(current_db)
                                    with st.spinner("正在自动抓取该导师最近10篇论文..."):
                                        try:
                                            result = resolve_recent_papers(
                                                prof_name=prof_name,
                                                univ_name=final_univ,
                                                homepage_url=homepage if homepage else "",
                                                preset_scholar_url="",
                                                limit=10,
                                            )
                                        except Exception:
                                            result = {"papers": [], "status": "exception", "scholar_url": "", "source": ""}

                                    papers = result.get("papers", []) if isinstance(result, dict) else []
                                    if papers:
                                        new_row["Scholar链接"] = result.get("scholar_url", "")
                                        new_row["最近论文"] = papers
                                        new_row["最近论文更新时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                        save_db(current_db)
                                        st.success(f"✅ 成功! {prof_name} 已加入大盘，并自动抓取 {len(papers)} 篇最近论文。")
                                    else:
                                        st.success(f"✅ 成功! {prof_name} 已加入大盘。")
                                        st.info("已尝试自动抓取最近论文，但暂未成功（可能被 Scholar 限流/验证码影响）。")
                                    st.rerun()
                                else:
                                    st.error(t("save_error"))
                    else:
                        with st.container():
                            current_db = load_db()
                            if not current_db:
                                st.warning(ui("导师数据库为空，请先在『新建导师记录』模式下创建！",
                                              "Professor DB is empty. Please create a new record first."))
                            else:
                                prof_options = {f"{r['导师/教授']} ({r.get('学校名称','未知')} - {r.get('院系','')})": i for i, r in enumerate(current_db)}
                                
                                preselect_prof_idx = 0
                                if default_prof_email:
                                    for i, r in enumerate(current_db):
                                        if r.get("导师邮箱") == default_prof_email or (default_prof_name and r.get("导师/教授") == default_prof_name):
                                            preselect_prof_idx = i
                                            break
                                            
                                sel_prof_str = st.selectbox("🎯 选择要同步关联的导师", list(prof_options.keys()), index=preselect_prof_idx, key=f"sel_prof_{mail_id}")
                                sel_idx = prof_options[sel_prof_str]
                                sel_r = current_db[sel_idx]
                                
                                val_email = default_prof_email if default_prof_email else sel_r.get("导师邮箱", "")
                                
                                status_choices = ["已发首封邮件", "收到积极回复", "收到中等回复", "收到消极回复", "面试预约阶段", "面试结束阶段", "口头offer"]
                                cur_stat = sel_r.get("阶段", "已发首封邮件")
                                if cur_stat not in status_choices: cur_stat = "已发首封邮件"
                                
                                cat_to_status_name = {
                                    1: "已发首封邮件", 2: "收到积极回复", 3: "收到消极回复", 
                                    4: "收到中等回复", 5: "面试预约阶段", 6: "面试结束阶段", 7: "口头offer"
                                }
                                new_status = cat_to_status_name.get(cat_id, cur_stat)
                                
                                st.info(f"📍 **同步后，导师当前申请阶段将自动更新为：** `{new_status}` (取自上方修正确认的邮件分类)")
                                
                                new_time = sel_r.get("面试时间", "")
                                if new_status == "面试预约阶段":
                                    dft_date, dft_time = get_interview_picker_defaults(new_time)
                                    c_dt1, c_dt2 = st.columns(2)
                                    with c_dt1:
                                        new_date = st.date_input("📅 面试日期", value=dft_date, key=f"new_intv_date_{mail_id}")
                                    with c_dt2:
                                        new_clock = st.time_input("⏰ 面试时间", value=dft_time, key=f"new_intv_time_{mail_id}")
                                    new_time = format_interview_time(new_date, new_clock)
                                
                                if st.button("🔄 同步更新进度并接管本邮件", use_container_width=True, key=f"upd_submit_{mail_id}", type="primary"):
                                    current_db[sel_idx]["阶段"] = new_status
                                    current_db[sel_idx]["面试时间"] = new_time
                                    if not sel_r.get("导师邮箱") and val_email:
                                        current_db[sel_idx]["导师邮箱"] = val_email
                                    current_db[sel_idx]["更新时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                    
                                    old_mail_id = str(current_db[sel_idx].get("关联邮件ID", ""))
                                    if mail_id not in old_mail_id:
                                        if old_mail_id and old_mail_id.strip() != "None":
                                            current_db[sel_idx]["关联邮件ID"] = f"{old_mail_id},{mail_id}"
                                        else:
                                            current_db[sel_idx]["关联邮件ID"] = mail_id
                                            
                                    save_db(current_db)
                                    st.success(f"✅ 成功! 已将本邮件同步关联至 {sel_r['导师/教授']}。")
                                    st.rerun()

    elif menu == t("menu_db"):
        st.title(ui("导师库管理", "Professor DB"))
        st.markdown(f"<p style='color: #9aa9ca; margin-bottom: 2rem;'>{ui('此处聚合所有套瓷与申请的导师信息。', 'This page aggregates all professor records and outreach status.')}</p>", unsafe_allow_html=True)
        
        current_db = load_db()
        if not current_db:
            st.info(ui("尚无导师数据，请前往【智能邮箱】中创建。", "No professor data yet. Create records from Email Center."))
        else:
            df = pd.DataFrame(current_db)
            
            # Ensure sorting/formatting of missing fields
            if "创建时间" not in df.columns:
                df["创建时间"] = df.get("更新时间", "未知")
            if "国家/地区" not in df.columns:
                df["国家/地区"] = "未知"
            if "学校名称" not in df.columns:
                df["学校名称"] = "未知"
            if "院系" not in df.columns:
                df["院系"] = "未知"
                
            df.fillna("-", inplace=True)
            df.fillna("-", inplace=True)
            
            # Create Filters
            col_f1, col_f2 = st.columns(2)
            countries = [ui("所有", "All")] + sorted(list(df["国家/地区"].unique()))
            selected_country = col_f1.selectbox(ui("🌐 筛选国家", "🌐 Filter Country"), countries)
            
            # Filter univs based on country
            if selected_country != ui("所有", "All"):
                univs_in_country = df[df["国家/地区"] == selected_country]["学校名称"].unique()
            else:
                univs_in_country = df["学校名称"].unique()
                
            univs = [ui("所有", "All")] + sorted(list(univs_in_country))
            selected_univ = col_f2.selectbox(ui("🏛️ 筛选学校", "🏛️ Filter University"), univs)
            
            # Apply Filters
            filtered_df = df
            if selected_country != ui("所有", "All"):
                filtered_df = filtered_df[filtered_df["国家/地区"] == selected_country]
            if selected_univ != ui("所有", "All"):
                filtered_df = filtered_df[filtered_df["学校名称"] == selected_univ]

            st.markdown(f"#### {ui('导师列表', 'Professor List')}")
            h1, h2, h3, h4, h5, h6, h7, h8 = st.columns([1.6, 1.3, 1.3, 1.2, 1.2, 1.2, 1.4, 0.5])
            h1.markdown(f"**{ui('导师/教授', 'Professor')}**")
            h2.markdown(f"**{ui('学校', 'University')}**")
            h3.markdown(f"**{ui('院系', 'Department')}**")
            h4.markdown(f"**{ui('国家/地区', 'Country/Region')}**")
            h5.markdown(f"**{ui('阶段', 'Stage')}**")
            h6.markdown(f"**{ui('更新时间', 'Updated')}**")
            h7.markdown(f"**{ui('当地时间', 'Local Time')}**")
            h8.markdown(f"**{ui('操作', 'Action')}**")
            for ridx, row in filtered_df.reset_index().iterrows():
                real_idx = int(row["index"])
                local_time = format_local_time(row.get("国家/地区", ""))

                c1, c2, c3, c4, c5, c6, c7, c8 = st.columns([1.6, 1.3, 1.3, 1.2, 1.2, 1.2, 1.4, 0.5])
                c1.write(str(row.get("导师/教授", "-")))
                c2.write(str(row.get("学校名称", "-")))
                c3.write(str(row.get("院系", "-")))
                c4.write(str(row.get("国家/地区", "-")))
                c5.write(str(row.get("阶段", "-")))
                c6.write(str(row.get("更新时间", "-")))
                c7.write(local_time)
                if c8.button("🗑️", key=f"db_del_btn_{real_idx}", help=ui("删除此导师记录", "Delete this record")):
                    st.session_state["db_delete_idx"] = real_idx

            if "db_delete_idx" in st.session_state:
                idx_to_del = st.session_state["db_delete_idx"]
                if 0 <= idx_to_del < len(current_db):
                    r = current_db[idx_to_del]
                    st.warning(ui(f"确认删除：{r.get('导师/教授', '未知')} - {r.get('学校名称', '未知')} ?",
                                  f"Confirm deletion: {r.get('导师/教授', 'Unknown')} - {r.get('学校名称', 'Unknown')} ?"))
                    d1, d2 = st.columns(2)
                    if d1.button(ui("取消", "Cancel"), key="db_del_cancel", use_container_width=True):
                        st.session_state.pop("db_delete_idx", None)
                        st.rerun()
                    if d2.button(ui("确认删除", "Confirm Delete"), key="db_del_confirm", use_container_width=True, type="primary"):
                        current_db.pop(idx_to_del)
                        save_db(current_db)
                        st.session_state.pop("db_delete_idx", None)
                        st.success(ui("已删除。", "Deleted."))
                        st.rerun()

    elif menu == t("menu_interview"):
        st.title(ui("面试准备舱", "Interview Prep"))
        st.markdown(f"<p style='color: #9aa9ca; margin-bottom: 1rem;'>{ui('可对任意导师提前准备面试；已预约面试会显示具体时间。', 'Prepare for interviews with any professor; scheduled interviews show exact time.')}</p>", unsafe_allow_html=True)

        current_db = load_db()
        scheduled_count = len([r for r in current_db if r.get("阶段") == "面试预约阶段"])
        c_m1, c_m2 = st.columns(2)
        c_m1.metric(ui("导师总数", "Total Professors"), len(current_db))
        c_m2.metric(ui("已预约面试导师数", "Scheduled Interviews"), scheduled_count)

        if not current_db:
            st.info(ui("当前没有导师记录。", "No professor records currently available."))
        else:
            scheduled_records = get_interview_records(current_db)
            scheduled_idx_set = {x.get("idx") for x in scheduled_records}
            scheduled_no_time = []
            unscheduled_records = []
            for idx, row in enumerate(current_db):
                if row.get("阶段") == "面试预约阶段":
                    if idx not in scheduled_idx_set:
                        scheduled_no_time.append({"idx": idx, "row": row, "raw_time": row.get("面试时间", "")})
                else:
                    unscheduled_records.append({"idx": idx, "row": row})

            def render_interview_item(idx, row):
                status_show = row.get("阶段", "未联系")
                intv = row.get("面试时间", "")
                if status_show == "面试预约阶段" and intv:
                    title = f"🎯 {row.get('导师/教授', '未知导师')} | {row.get('学校名称', '未知学校')} | 状态: {status_show} | 面试: {intv}"
                else:
                    title = f"🎯 {row.get('导师/教授', '未知导师')} | {row.get('学校名称', '未知学校')} | 状态: {status_show}"
                with st.expander(title, expanded=False):
                    st.write(f"**院系**: {row.get('院系', '未知')}")
                    st.write(f"**研究方向**: {row.get('研究方向', '未明确')}")
                    hp = row.get("主页链接", "")
                    if hp:
                        st.markdown(f"**{ui('主页链接', 'Homepage')}**: [{hp}]({hp})")
                    else:
                        st.warning(ui("该导师尚未保存主页链接，Scholar 命中率可能下降。",
                                      "Homepage URL is missing; Scholar hit rate may be lower."))

                    scholar_url = row.get("Scholar链接", "")
                    if scholar_url:
                        st.markdown(f"**Google Scholar**: [{scholar_url}]({scholar_url})")

                    cached_papers = row.get("最近论文", [])
                    if isinstance(cached_papers, list) and cached_papers:
                        st.caption(ui(f"最近论文更新时间: {row.get('最近论文更新时间', '未知')}",
                                      f"Papers updated: {row.get('最近论文更新时间', 'Unknown')}"))
                        st.dataframe(pd.DataFrame(cached_papers), use_container_width=True, hide_index=True)

                    cached_questions = row.get("面试问题", [])
                    if isinstance(cached_questions, list) and cached_questions:
                        with st.expander(ui("🧠 AI 面试问题（基于最近论文）", "🧠 AI Interview Questions (from recent papers)"), expanded=False):
                            st.caption(ui(f"问题更新时间: {row.get('面试问题更新时间', '未知')}",
                                          f"Questions updated: {row.get('面试问题更新时间', 'Unknown')}"))
                            for qi, q in enumerate(cached_questions, start=1):
                                q_col, mark_col = st.columns([8.8, 1.2])
                                with q_col:
                                    st.markdown(f"{qi}. {q}")
                                with mark_col:
                                    mark_clicked = st.button(ui("标记", "Mark"), key=f"mark_high_freq_{idx}_{qi}", help=ui("标记为高频考察点", "Mark as high-frequency question"))
                                if mark_clicked:
                                    cfg = load_config()
                                    resume_text = get_active_resume_text(cfg)
                                    if not resume_text:
                                        st.warning(ui("请先在【我的简历】上传并设置当前使用简历。", "Please upload and set an active resume first."))
                                    else:
                                        with st.spinner(ui("AI 正在生成该考察点的建议回答...", "AI is generating a suggested answer...")):
                                            ai_cfg = _ai_cfg_with_app_lang(cfg)
                                            ok_hf, hf_payload, hf_raw = generate_high_frequency_answer(
                                                question=str(q),
                                                prof_name=row.get("导师/教授", ""),
                                                univ_name=row.get("学校名称", ""),
                                                research_direction=row.get("研究方向", ""),
                                                homepage_url=hp,
                                                homepage_text=get_homepage_text_excerpt(hp, limit=3000) if hp else "",
                                                papers=cached_papers if isinstance(cached_papers, list) else [],
                                                resume_text=resume_text,
                                                config=ai_cfg,
                                            )
                                        if ok_hf:
                                            current_points = current_db[idx].get("高频考察点", [])
                                            if not isinstance(current_points, list):
                                                current_points = []

                                            existed = False
                                            for item in current_points:
                                                if isinstance(item, dict) and str(item.get("question", "")).strip() == str(q).strip():
                                                    item["ai_answer"] = hf_payload.get("suggested_answer", "")
                                                    item["key_points"] = hf_payload.get("key_points", [])
                                                    item["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                                    existed = True
                                                    break
                                            if not existed:
                                                current_points.append(
                                                    {
                                                        "question": str(q).strip(),
                                                        "ai_answer": hf_payload.get("suggested_answer", ""),
                                                        "key_points": hf_payload.get("key_points", []),
                                                        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                                    }
                                                )
                                            current_db[idx]["高频考察点"] = current_points
                                            save_db(current_db)
                                            st.success(ui("已收录到高频考察点，并生成 AI 建议回答。", "Saved to high-frequency list and generated AI answer."))
                                            st.rerun()
                                        else:
                                            st.error(ui(f"生成建议回答失败：{hf_raw}", f"Failed to generate suggested answer: {hf_raw}"))

                    high_freq_points = row.get("高频考察点", [])
                    if isinstance(high_freq_points, list) and high_freq_points:
                        with st.expander(ui("🔥 高频考察点", "🔥 High-Frequency Questions"), expanded=False):
                            for pi, item in enumerate(high_freq_points, start=1):
                                q_text = ""
                                a_text = ""
                                k_points = []
                                updated_at = ""
                                if isinstance(item, dict):
                                    q_text = str(item.get("question", "")).strip()
                                    a_text = str(item.get("ai_answer", "")).strip()
                                    raw_k = item.get("key_points", [])
                                    if isinstance(raw_k, list):
                                        k_points = [str(x).strip() for x in raw_k if str(x).strip()]
                                    updated_at = str(item.get("updated_at", "")).strip()
                                elif isinstance(item, str):
                                    q_text = item.strip()

                                if not q_text:
                                    continue
                                st.markdown(f"**{pi}. {q_text}**")
                                if updated_at:
                                    st.caption(ui(f"更新时间: {updated_at}", f"Updated: {updated_at}"))
                                if a_text:
                                    st.markdown(f"**{ui('AI建议回答：', 'AI Suggested Answer:')}** {a_text}")
                                if k_points:
                                    st.markdown(f"**{ui('答题要点：', 'Key Points:')}** {'；'.join(k_points)}")
                                st.markdown("---")

                    cached_advice = row.get("面试建议", [])
                    if isinstance(cached_advice, list) and cached_advice:
                        with st.expander(ui("🧭 面试建议（简历 + 导师主页 + Scholar）", "🧭 Interview Advice (Resume + Homepage + Scholar)"), expanded=False):
                            st.caption(ui(f"建议更新时间: {row.get('面试建议更新时间', '未知')}",
                                          f"Advice updated: {row.get('面试建议更新时间', 'Unknown')}"))
                            for ai, adv in enumerate(cached_advice, start=1):
                                st.markdown(f"{ai}. {adv}")

                    b1, b2 = st.columns(2)
                    with b1:
                        if st.button(ui("🎭 模拟面试", "🎭 Mock Interview"), key=f"open_mock_interview_{idx}", use_container_width=True):
                            show_mock_interview_dialog(idx, row)

                    with b2:
                        gen_q = st.button(ui("✨ 生成5个高频面试问题", "✨ Generate 5 High-Frequency Questions"), key=f"gen_questions_{idx}", use_container_width=True)
                    if gen_q:
                        with st.spinner(ui("AI 正在生成高频综合面试问题...", "AI is generating high-frequency interview questions...")):
                            cfg = load_config()
                            ai_cfg = _ai_cfg_with_app_lang(cfg)
                            ok, questions, raw = generate_interview_questions(
                                prof_name=row.get("导师/教授", ""),
                                univ_name=row.get("学校名称", ""),
                                research_direction=row.get("研究方向", ""),
                                papers=cached_papers if isinstance(cached_papers, list) else [],
                                config=ai_cfg,
                            )
                        if ok:
                            current_db[idx]["面试问题"] = questions
                            current_db[idx]["面试问题更新时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            save_db(current_db)
                            st.success(ui(f"✅ 已生成 {len(questions)} 条高频问题", f"✅ Generated {len(questions)} high-frequency questions"))
                            st.rerun()
                        else:
                            st.error(ui(f"生成失败：{raw}", f"Generation failed: {raw}"))

                    if st.button(ui("🧭 生成面试建议（结合简历+导师）", "🧭 Generate Interview Advice (Resume + Professor)"), key=f"gen_advice_{idx}", use_container_width=True):
                        cfg = load_config()
                        ai_cfg = _ai_cfg_with_app_lang(cfg)
                        resume_text = get_active_resume_text(cfg)
                        if not resume_text:
                            st.warning(ui("请先在【我的简历】上传并设置当前使用简历。", "Please upload and set an active resume first."))
                        else:
                            with st.spinner(ui("AI 正在基于简历与导师研究方向生成面试建议...", "AI is generating interview advice from resume and professor profile...")):
                                homepage_text = get_homepage_text_excerpt(hp, limit=3000) if hp else ""
                                ok, advice, raw = generate_interview_advice(
                                    prof_name=row.get("导师/教授", ""),
                                    univ_name=row.get("学校名称", ""),
                                    research_direction=row.get("研究方向", ""),
                                    homepage_url=hp,
                                    homepage_text=homepage_text,
                                    papers=cached_papers if isinstance(cached_papers, list) else [],
                                    resume_text=resume_text,
                                    config=ai_cfg,
                                )
                            if ok:
                                current_db[idx]["面试建议"] = advice
                                current_db[idx]["面试建议更新时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                save_db(current_db)
                                st.success(ui(f"✅ 已生成 {len(advice)} 条面试建议", f"✅ Generated {len(advice)} interview advice items"))
                                st.rerun()
                            else:
                                st.error(ui(f"生成失败：{raw}", f"Generation failed: {raw}"))

            st.markdown(f"### {ui('已预约面试的老师', 'Scheduled Interview Professors')}")
            if not scheduled_records and not scheduled_no_time:
                st.info(ui("当前没有已预约面试的老师。", "No scheduled interview professors."))
            else:
                for rec in scheduled_records:
                    render_interview_item(rec.get("idx"), rec.get("row", {}))
                for rec in scheduled_no_time:
                    render_interview_item(rec.get("idx"), rec.get("row", {}))

            st.markdown(f"### {ui('还没预约面试的老师', 'Not Yet Scheduled')}")
            if not unscheduled_records:
                st.info(ui("当前没有未预约面试的老师。", "No unscheduled professors."))
            else:
                for rec in unscheduled_records:
                    render_interview_item(rec.get("idx"), rec.get("row", {}))

    elif menu == settings_menu_label:
        st.title(settings_menu_label)
        st.markdown(f"<p style='color: #9aa9ca; margin-bottom: 2rem;'>{ui('配置你的邮箱与系统连接参数，数据仅保存在本地文件中。', 'Configure email and system connection settings. Data is stored locally only.')}</p>", unsafe_allow_html=True)
        
        config = load_config()
        
        st.subheader(ui("📧 邮箱账号绑定", "📧 Email Account Binding"))
        st.info(ui("""**常用邮箱配置指南：**
- **Gmail**: IMAP 为 `imap.gmail.com`，SMTP 为 `smtp.gmail.com`。(密码须用 [应用专用密码](https://myaccount.google.com/apppasswords))
- **个人版 Outlook/Hotmail**: IMAP 为 `outlook.office365.com`。(需生成 [个人应用密码](https://account.live.com/proofs/manage/additional))
- **大学/机构邮箱 (如 connect.polyu.hk)**: 
  - IMAP: `outlook.office365.com`
  - SMTP: `smtp.office365.com`
  - 🔑 **密码获取**: 这是组织账户，请务必前往专属安全页：[https://mysignins.microsoft.com/security-info](https://mysignins.microsoft.com/security-info) 登录并添加“应用密码(App Password)”。
  - ⚠️ **如果连接一直被拒绝**: 学校可能禁用了IMAP基础认证。建议在网页版Outlook中设置【自动转发】到你个人的Gmail邮箱，然后在本系统绑定该Gmail，不仅稳定而且不用去学校后台折腾安全策略！
""", """**Common Email Setup Guide:**
- **Gmail**: IMAP `imap.gmail.com`, SMTP `smtp.gmail.com` (use [App Password](https://myaccount.google.com/apppasswords)).
- **Personal Outlook/Hotmail**: IMAP `outlook.office365.com` (set [App Password](https://account.live.com/proofs/manage/additional)).
- **University/Organization mailbox (e.g., connect.polyu.hk)**:
  - IMAP: `outlook.office365.com`
  - SMTP: `smtp.office365.com`
  - 🔑 **Password setup**: For org accounts, visit [https://mysignins.microsoft.com/security-info](https://mysignins.microsoft.com/security-info) and add an App Password.
  - ⚠️ **If IMAP is rejected**: Your institution may disable basic auth. A practical fallback is auto-forwarding to Gmail and binding Gmail here.
"""))
        
        with st.form("email_config_form"):
            col1, col2 = st.columns(2)
            with col1:
                email = st.text_input(ui("邮箱地址", "Email Address"), value=config.get("email", ""))
                imap_server = st.text_input(ui("IMAP 服务器 (收件)", "IMAP Server (Inbox)"), value=config.get("imap_server", "imap.gmail.com"))
            with col2:
                password = st.text_input(ui("应用密码 (App Password)", "App Password"), value=config.get("password", ""), type="password")
                smtp_server = st.text_input(ui("SMTP 服务器 (发件)", "SMTP Server (Outbox)"), value=config.get("smtp_server", "smtp.gmail.com"))
            submit_btn = st.form_submit_button(ui("💾 保存邮箱配置", "💾 Save Email Config"))
            
        if submit_btn:
            if email and password:
                config["email"] = email
                config["password"] = password
                config["imap_server"] = imap_server
                config["smtp_server"] = smtp_server
            save_config(config)
            st.success(ui("✅ 邮箱及网络配置已成功保存！配置将持久化保留在本地。", "✅ Email and network settings saved locally."))

        st.subheader(ui("🤖 AI 模型配置", "🤖 AI Model Config"))
        ai_provider = st.selectbox(
            ui("选择 AI 分析引擎", "Select AI Engine"),
            ["通义千问 (Qwen)", "Google Gemini"],
            index=0 if config.get("ai_provider", "通义千问 (Qwen)") == "通义千问 (Qwen)" else 1,
            key="settings_ai_provider",
            on_change=_save_ai_settings_from_state,
        )
        qwen_api_key_stored = st.session_state.get("settings_qwen_api_key", config.get("qwen_api_key", ""))
        gemini_api_key_stored = st.session_state.get("settings_gemini_api_key", config.get("gemini_api_key", ""))
        if ai_provider == "通义千问 (Qwen)":
            st.text_input(
                ui("通义千问 API Key (sk-...)", "Qwen API Key (sk-...)"),
                type="password",
                value=qwen_api_key_stored,
                key="settings_qwen_api_key",
                on_change=_save_ai_settings_from_state,
            )
        else:
            st.text_input(
                "Gemini API Key (AIzaSy...)",
                type="password",
                value=gemini_api_key_stored,
                key="settings_gemini_api_key",
                on_change=_save_ai_settings_from_state,
            )

        # === 💎 AI API 连通性测试模块 ===
        provider = ai_provider
        st.markdown(f"### {ui('🧪', '🧪')} {provider} {ui('连通性测试', 'Connectivity Test')}")
        
        test_api_key = qwen_api_key_stored if provider == "通义千问 (Qwen)" else gemini_api_key_stored
        
        if test_api_key:
            if st.button(ui(f"🚀 测试 {provider} 接口", f"🚀 Test {provider} API"), type="secondary"):
                with st.spinner(ui("正在连接 AI 服务器进行测试...", "Connecting to AI server for testing...")):
                    try:
                        if provider == "通义千问 (Qwen)":
                            client = OpenAI(
                                api_key=test_api_key, 
                                base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                            )
                            completion = client.chat.completions.create(
                                model="qwen-plus",
                                messages=[{'role': 'user', 'content': "Please strictly reply: 'API is working!' in English without any other words."}]
                            )
                            resp_text = completion.choices[0].message.content
                            st.success(ui(f"🎉 **测试通过！** 模型回复内容: `{resp_text.strip()}`", f"🎉 **Test passed!** Response: `{resp_text.strip()}`"))
                            
                        else: # Gemini
                            genai.configure(api_key=test_api_key)
                            resp, used_model = _gemini_generate_content_with_fallback(
                                "Please strictly reply: 'API is working!' in English without any other words.",
                                stream=False,
                            )
                            resp_text = (getattr(resp, "text", "") or "").strip()
                            st.success(
                                ui(
                                    f"🎉 **测试通过！** 模型 `{used_model}` 回复内容: `{resp_text}`",
                                    f"🎉 **Test passed!** Model `{used_model}` response: `{resp_text}`",
                                )
                            )
                    
                    except Exception as e:
                        st.error(ui("❌ 连通失败！", "❌ Connectivity test failed: ") + str(e))
        else:
            st.info(ui(f"请先在上方填入 {provider} API Key。", f"Please enter your {provider} API key above first."))
        # ==================================
                
        # 始终为已配置的邮箱显示最新 5 封邮件
        if config.get("email") and config.get("password"):
            st.subheader(ui("📡 连接状态与最新邮件", "📡 Connection Status & Latest Emails"))
            with st.spinner(ui("🔄 正在挂载 IMAP 协议并拉取近期邮件...", "🔄 Mounting IMAP and fetching recent emails...")):
                success, result = test_imap_connection(config["email"], config["password"], config["imap_server"])
                if success:
                    st.success(ui("🎉 连接成功！您的网络与邮箱均状态良好。", "🎉 Connection successful. Network and mailbox are healthy."))
                    st.markdown(f"#### {ui('📫 最近收到的 5 封邮件：', '📫 Latest 5 Emails:')}")
                    st.dataframe(pd.DataFrame(result), use_container_width=True)
                else:
                    st.error(ui(f"❌ 连接被拒绝。这通常是因为密码错误、未开启 IMAP，或被安全组拦截。\\n\\n**错误详情：** `{result}`",
                                  f"❌ Connection rejected. Usually caused by wrong password, IMAP disabled, or security policy.\\n\\n**Error:** `{result}`"))
                    st.info(ui("💡 **自救指南：**\\n1. **Gmail 用户**: 必须使用 [App Password](https://myaccount.google.com/apppasswords) 代替登录密码。\\n2. **Outlook 用户**: 确保在设置中开启了 POP/IMAP 选项。\\n3. 检查 IMAP 服务器地址是否正确。",
                               "💡 **Troubleshooting:**\\n1. **Gmail**: Use [App Password](https://myaccount.google.com/apppasswords) instead of account password.\\n2. **Outlook**: Ensure POP/IMAP is enabled.\\n3. Verify IMAP server address is correct."))

    else:
        st.title(menu)
        st.write(ui("该功能模块正在基于 UI UX Pro Max 设计规范开发中...", "This module is under development based on UI UX Pro Max design guidelines..."))

if __name__ == "__main__":
    main()
