"""
ui/components.py — 从 app.py 拆分生成（函数体逐字保留）。
"""
from __future__ import annotations

# ---- 第三方 / 标准库 ----
from contextlib import contextmanager
from datetime import datetime
from sample_data import PRIORITY_COLORS, RATING_COLORS, STATUS_COLORS
import os
import streamlit as st

# ---- 项目内部 ----
from config import NAV_STATE_KEY, TODAY
from services.cache import _cached_pdf_b64, _cached_table_preview
from services.people import _owner_names
from services.requirements import get_requirements



_CARD_SEQ = {"n": 0}


def badge(text: str, color: str) -> str:
    return f'<span style="background:{color};color:white;padding:2px 8px;border-radius:10px;font-size:12px;font-weight:600;">{text}</span>'


def status_badge(status: str) -> str:
    return badge(status, STATUS_COLORS.get(status, "#9E9E9E"))


def priority_badge(priority: str) -> str:
    return badge(priority, PRIORITY_COLORS.get(priority, "#9E9E9E"))


def rating_badge(label: str, rating: str, source: str = "") -> str:
    """价值/复杂度评分徽章。source='AI' 时用虚线边框视觉区分。"""
    color = RATING_COLORS.get(rating, "#BDBDBD")
    border = "border:1px dashed #999;" if source == "AI" else ""
    suffix = f'<span style="font-size:9px;color:#999;margin-left:3px;">AI</span>' if source == "AI" else ""
    return f'<span style="background:{color}22;color:{color};padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;{border}">{label}:{rating}{suffix}</span>'


def tag_chip(tag: str) -> str:
    return f'<span style="background:#ECEFF1;color:#37474F;padding:2px 8px;border-radius:10px;font-size:11px;margin-right:4px;">#{tag}</span>'


def _card(title: str = "", tag: str = ""):
    """卡片容器（t31；t34 F-01 改 keyed 挂钩）：st.container(border=True, key="kb_card_N")
    会生成 class="st-key-kb_card_N"，CSS 据此挂钩白底圆角卡片（旧 testid 挂钩在
    Streamlit 1.61.1 中不存在，属静默失效，见 21 号审查）。序号由 _CARD_SEQ 每轮归零。"""
    _CARD_SEQ["n"] += 1
    with st.container(border=True, key=f"kb_card_{_CARD_SEQ['n']}"):
        if title:
            st.markdown(
                f'<div class="kb-card-hd">{title}'
                + (f'<span class="kb-tag">{tag}</span>' if tag else '')
                + '</div>',
                unsafe_allow_html=True,
            )
        yield


def _page_header(title: str, sub: str = "", action_label: str = "", action_target: str = "") -> None:
    """统一页头（t31）：标题 + 副标题 + 右侧主操作按钮（点击跳转目标页）。

    版式参考客诉系统页面标题区（标题 + 灰字说明 + 右侧主操作）。对齐说明：
    - 标题行用 flex + align-items:baseline，让 19px 标题与 12px 副标题**按基线**对齐，
      而不是各自行盒居中（后者会让小字看起来偏高/偏低）；
    - 标题列与按钮列都交给 vertical_alignment="center" 居中——
      原来靠 st.markdown 塞一个 18px 高的占位块把按钮「压」下来，
      是固定像素猜测，字号一变就错位。
    """
    c1, c2 = st.columns([4.6, 1], vertical_alignment="center")
    with c1:
        st.markdown(
            '<div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;">'
            f'<span class="kb-page-title">{title}</span>'
            + (f'<span class="kb-page-sub">{sub}</span>' if sub else '') + '</div>',
            unsafe_allow_html=True,
        )
    with c2:
        if action_label:
            if st.button(action_label, type="primary", use_container_width=True,
                         key=f"kb_act_{action_target or title}"):
                if action_target:
                    st.session_state[NAV_STATE_KEY] = action_target
                    st.rerun()


def _date_picker(label: str, value_str: str = "", prefix: str = "due") -> str:
    """年/月/日三个输入框选择期望完成日期（原生控件，样式与其它字段一致）"""
    y, m, d = "", 0, ""
    if value_str:
        try:
            _y, _m, _d = (int(x) for x in value_str.split("-"))
            y, m, d = str(_y), _m, str(_d)
        except Exception:
            y, m, d = "", 0, ""

    lab_col, y_col, m_col, d_col = st.columns([1.1, 1, 1, 1], gap="small")
    with lab_col:
        st.markdown(f"<div style='font-size:14px;color:#31333F;margin-top:26px;white-space:nowrap;'>{label}</div>", unsafe_allow_html=True)
    with y_col:
        year_val = st.text_input("年", value=y, key=f"{prefix}_year")
    with m_col:
        month_val = st.selectbox(
            "月",
            options=["（请选择）"] + [f"{i}月" for i in range(1, 13)],
            index=(0 if m == 0 else m),
            key=f"{prefix}_month",
        )
    with d_col:
        day_val = st.text_input("日", value=d, key=f"{prefix}_day")

    _y = int(year_val) if str(year_val).strip().isdigit() else 0
    _d = int(day_val) if str(day_val).strip().isdigit() else 0
    _m = 0 if month_val == "（请选择）" else int(month_val.replace("月", ""))
    if 2010 <= _y <= 2040 and 1 <= _m <= 12 and 1 <= _d <= 31:
        return f"{_y:04d}-{_m:02d}-{_d:02d}"
    return ""


def _star_html(score: int, size: int = 15) -> str:
    """5 星制评分展示（实心 ★ + 空心 ☆）"""
    n = max(0, min(5, int(score or 0)))
    return (f'<span style="color:#FB8C00;font-size:{size}px;letter-spacing:1px;">'
            f'{"★" * n}{"☆" * (5 - n)}</span>')


def _render_bar_chart(rows: list[tuple], total: int, show_value=None) -> None:
    """横向条形图（手写 HTML/CSS，与「按部门分布」区块风格一致）。

    rows: [(标签, 数值, 颜色)]；total 为满格基准；show_value(i) 可覆盖右侧显示文本。
    """
    html = []
    for i, (label, value, color) in enumerate(rows):
        pct = min(value / max(total, 1), 1.0)
        text = show_value(i) if show_value else str(value)
        html.append(
            f'<div style="margin-bottom:6px;">'
            f'<span style="display:inline-block;width:170px;font-size:13px;">{label}</span>'
            f'<span style="display:inline-block;background:{color};height:14px;'
            f'width:{max(pct * 260, 2):.0f}px;border-radius:3px;vertical-align:middle;margin:0 8px;"></span>'
            f'<span style="font-weight:600;font-size:13px;">{text}</span></div>'
        )
    st.markdown("".join(html), unsafe_allow_html=True)


def _file_ext(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower().lstrip(".")


def _render_file_preview(data: bytes, filename: str):
    """在详情页为已落盘文件提供内联预览（按扩展名选择预览方式）。

    注意：st.expander 折叠时内部代码依然会执行，所以这里的解析/编码结果全部走缓存，
    否则每次 rerun 都会重新解析 Excel、重新 base64 整份 PDF。
    """
    ext = _file_ext(filename)
    with st.expander(f"👁 预览「{filename}」", expanded=False):
        # 图片
        if ext in {"png", "jpg", "jpeg", "gif", "bmp", "webp"}:
            st.image(data, use_container_width=True)
        # Excel / CSV 表格
        elif ext in {"xlsx", "xls", "csv"}:
            try:
                df = _cached_table_preview(data, ext)
                st.dataframe(df, use_container_width=True)
            except Exception as e:
                st.caption(f"⚠️ 无法解析该表格文件：{e}")
        # PDF
        elif ext == "pdf":
            b64 = _cached_pdf_b64(data)
            st.components.v1.html(
                f'<iframe src="data:application/pdf;base64,{b64}" width="100%" height="620" '
                f'style="border:none;"></iframe>',
                height=640,
                scrolling=True,
            )
        # 音频（浏览器原生播放器）
        elif ext in {"mp3", "wav", "m4a", "aac", "ogg", "amr", "wma", "flac"}:
            st.audio(data)
        # 文本类
        elif ext in {"txt", "md", "log", "eml", "json", "xml", "py"}:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = data.decode("gbk", errors="replace")
            st.code(text, language=None)
        # 其它（docx 等）暂不支持内联预览
        else:
            st.caption("该文件类型暂不支持在线预览，请点击上方「⬇下载」查看。")


def render_priority_quadrant(reqs=None, render_matrix=True):
    """紧急度 × 重要度 四象限衍生视图
    - 重要度：P0/P1 = 重要，P2/P3 = 不重要
    - 紧急度：requested_due_date 距今天 ≤ 7 天 = 紧急
    - 不增加用户录入负担，纯看板衍生
    - reqs：可选，传入需求列表则只对这些需求分析（如「我的看板」）；默认全局
    - render_matrix：是否连带渲染「价值×复杂度」矩阵
    """
    st.markdown("---")
    st.markdown("### 🎯 紧急度 × 重要度 四象限")
    st.caption("重要度由 priority 推导（P0/P1=重要）；紧急度由期望完成日期距今天数推导（≤7天=紧急）。仅统计在跑需求。")

    source = reqs if reqs is not None else get_requirements()
    active = [r for r in source
              if r.get("status") in ("需求池", "待启动", "进行中", "暂停")]

    q1, q2, q3, q4 = [], [], [], []  # Q1=紧急且重要 / Q2=重要不紧急 / Q3=紧急不重要 / Q4=不重要不紧急
    for r in active:
        important = r.get("priority", "P2") in ("P0", "P1")
        days_left = None
        urgent = False
        due_str = r.get("requested_due_date", "")
        if due_str:
            try:
                due = datetime.strptime(due_str, "%Y-%m-%d").date()
                days_left = (due - TODAY).days
                urgent = days_left <= 7
            except Exception:
                pass
        # 未指定 due_date 的，按"不紧急"处理
        if important and urgent:
            q1.append((r, days_left))
        elif important and not urgent:
            q2.append((r, days_left))
        elif not important and urgent:
            q3.append((r, days_left))
        else:
            q4.append((r, days_left))

    def render_card(r, days_left):
        """渲染单个需求卡片"""
        owner = _owner_names(r)
        due_text = ""
        if days_left is not None:
            due_text = f' · 剩 <b style="color:{("#E53935" if days_left < 0 else "#FB8C00")}">{days_left} 天</b>'
        elif r.get("requested_due_date"):
            due_text = f' · 到期 {r["requested_due_date"]}'
        else:
            due_text = ' · <span style="color:#999;">无截止日</span>'
        return (
            f'<div style="background:white;border:1px solid #ECEFF1;border-radius:6px;padding:8px;margin:6px 0;">'
            f'{priority_badge(r.get("priority", "未知"))}'
            f' &nbsp;<b>{r["id"]}</b>'
            f'<div style="font-size:13px;margin-top:4px;">{r["title"]}</div>'
            f'<div style="color:#666;font-size:11px;margin-top:4px;">👤 {owner}{due_text}</div>'
            f'</div>'
        )

    # 表头：列方向标注（重要 / 不重要）
    head_cols = st.columns([1, 8, 1, 8])
    head_cols[0].markdown("")
    head_cols[1].markdown(
        '<div style="background:#FFEBEE;text-align:center;padding:6px;border-radius:4px;font-weight:600;color:#C62828;">'
        '⚠️ 重要（P0 / P1）</div>',
        unsafe_allow_html=True,
    )
    head_cols[2].markdown("")
    head_cols[3].markdown(
        '<div style="background:#F3E5F5;text-align:center;padding:6px;border-radius:4px;font-weight:600;color:#6A1B9A;">'
        '不重要（P2 / P3）</div>',
        unsafe_allow_html=True,
    )

    # 第一行：紧急
    row1 = st.columns([1, 8, 1, 8])
    row1[0].markdown(
        '<div style="background:#FFCDD2;writing-mode:vertical-rl;text-orientation:upright;text-align:center;'
        'padding:10px 4px;border-radius:4px;font-weight:600;color:#B71C1C;font-size:12px;">'
        '紧急（≤7天）</div>',
        unsafe_allow_html=True,
    )
    with row1[1]:
        st.markdown(
            f'<div style="background:#FFF5F5;border-left:3px solid #E53935;padding:8px;border-radius:4px;">'
            f'<b>Q1 · 紧急且重要（{len(q1)}）</b></div>',
            unsafe_allow_html=True,
        )
        with st.container(height=420):
            if q1:
                for r, d in sorted(q1, key=lambda x: (x[1] if x[1] is not None else 999, x[0]["priority"])):
                    st.markdown(render_card(r, d), unsafe_allow_html=True)
            else:
                st.caption("（空）")
    row1[2].markdown("")
    with row1[3]:
        st.markdown(
            f'<div style="background:#FAFAFA;border-left:3px solid #9E9E9E;padding:8px;border-radius:4px;">'
            f'<b>Q3 · 紧急但不重要（{len(q3)}）</b></div>',
            unsafe_allow_html=True,
        )
        with st.container(height=420):
            if q3:
                for r, d in sorted(q3, key=lambda x: (x[1] if x[1] is not None else 999, x[0]["priority"])):
                    st.markdown(render_card(r, d), unsafe_allow_html=True)
            else:
                st.caption("（空）")

    # 第二行：不紧急
    row2 = st.columns([1, 8, 1, 8])
    row2[0].markdown(
        '<div style="background:#BBDEFB;writing-mode:vertical-rl;text-orientation:upright;text-align:center;'
        'padding:10px 4px;border-radius:4px;font-weight:600;color:#1565C0;font-size:12px;">'
        '不紧急（>7天 / 未指定）</div>',
        unsafe_allow_html=True,
    )
    with row2[1]:
        st.markdown(
            f'<div style="background:#FFF8E1;border-left:3px solid #FB8C00;padding:8px;border-radius:4px;">'
            f'<b>Q2 · 重要但不紧急（{len(q2)}）</b></div>',
            unsafe_allow_html=True,
        )
        with st.container(height=420):
            if q2:
                for r, d in sorted(q2, key=lambda x: (x[1] if x[1] is not None else 999, x[0]["priority"])):
                    st.markdown(render_card(r, d), unsafe_allow_html=True)
            else:
                st.caption("（空）")
    row2[2].markdown("")
    with row2[3]:
        st.markdown(
            f'<div style="background:#F1F8E9;border-left:3px solid #7CB342;padding:8px;border-radius:4px;">'
            f'<b>Q4 · 不重要不紧急（{len(q4)}）</b></div>',
            unsafe_allow_html=True,
        )
        with st.container(height=420):
            if q4:
                for r, d in sorted(q4, key=lambda x: (x[1] if x[1] is not None else 999, x[0]["priority"])):
                    st.markdown(render_card(r, d), unsafe_allow_html=True)
            else:
                st.caption("（空）")

    st.caption("💡 使用提示：Q1 是必须最先处理的；Q2 是要主动规划的；Q3 常常是「插队需求」，要警惕；Q4 是可以延后的。")

    # ===== 价值 × 复杂度 矩阵 =====
    if render_matrix:
        render_value_complexity_matrix()


def render_value_complexity_matrix():
    """价值 × 复杂度 3×3 矩阵衍生视图（v0.2.2 / PRD v1.2 §F9.2）
    仅显示已评分的需求，未评分需求不出现在矩阵中。
    """
    st.markdown("---")
    st.markdown("### 💎 价值 × 复杂度 矩阵")
    st.caption("仅显示已评分需求。纵轴价值（高/中/低），横轴复杂度（低/中/高）。评分由项目负责人或 AI 给出，标记来源。")

    all_reqs = get_requirements()
    rated = [r for r in all_reqs
             if r.get("value_rating") and r["value_rating"] != "未评"
             and r.get("complexity_rating") and r["complexity_rating"] != "未评"]
    unrated_count = len(all_reqs) - len(rated)
    st.caption(f"已评分 {len(rated)} 条 · 未评分 {unrated_count} 条（不显示在矩阵中）")

    # 9 个格子
    matrix = {
        ("高", "低"): ("速胜（Quick Win）", "#E8F5E9", "#2E7D32", "建议优先做"),
        ("高", "中"): ("战略项目", "#FFF8E1", "#F57F17", "主动规划"),
        ("高", "高"): ("重大项目", "#FFEBEE", "#C62828", "重点保障"),
        ("中", "低"): ("顺手做", "#E3F2FD", "#1565C0", "资源有空隙时"),
        ("中", "中"): ("正常推进", "#F5F5F5", "#616161", "—"),
        ("中", "高"): ("审慎评估", "#FFF3E0", "#E65100", "投入产出比要算"),
        ("低", "低"): ("填空任务", "#FAFAFA", "#9E9E9E", "资源紧张可弃"),
        ("低", "中"): ("鸡肋", "#FBE9E7", "#BF360C", "建议考虑终止"),
        ("低", "高"): ("钱坑", "#FFEBEE", "#B71C1C", "建议主动终止"),
    }

    def render_cell(v, c):
        label, bg, fg, hint = matrix[(v, c)]
        items = [r for r in rated if r.get("value_rating") == v and r.get("complexity_rating") == c]
        cards_html = f'<div style="background:{bg};border-radius:6px;padding:8px;">'
        cards_html += f'<div style="color:{fg};font-weight:700;font-size:13px;">{label}</div>'
        cards_html += f'<div style="color:#666;font-size:10px;margin-bottom:6px;">{hint} · {len(items)} 条</div>'
        if items:
            for r in items[:5]:  # 每格最多展示 5 条
                src_tag = "🤖 " if r.get("rating_source") == "AI" else ""
                status = r.get("status", "")
                status_color = STATUS_COLORS.get(status, "#999")
                cards_html += (
                    f'<div style="background:white;padding:6px;margin:4px 0;border-radius:4px;font-size:11px;">'
                    f'{priority_badge(r.get("priority", "未知"))}'
                    f' &nbsp;<b>{r["id"]}</b>'
                    f' &nbsp;<span style="color:{status_color};">[{status}]</span>'
                    f'<div style="margin-top:3px;">{src_tag}{r["title"][:30]}</div>'
                    f'</div>'
                )
            if len(items) > 5:
                cards_html += f'<div style="text-align:center;color:#888;font-size:10px;padding:4px;">还有 {len(items)-5} 条…</div>'
        else:
            cards_html += '<div style="text-align:center;color:#AAA;font-size:11px;padding:8px;">（空）</div>'
        cards_html += '</div>'
        return cards_html

    # 表头：复杂度
    head = st.columns([1, 5, 5, 5])
    head[0].markdown("")
    head[1].markdown('<div style="text-align:center;background:#E1BEE7;color:#4A148C;padding:6px;border-radius:4px;font-weight:600;">复杂度：低</div>', unsafe_allow_html=True)
    head[2].markdown('<div style="text-align:center;background:#BA68C8;color:#FFFFFF;padding:6px;border-radius:4px;font-weight:600;">复杂度：中</div>', unsafe_allow_html=True)
    head[3].markdown('<div style="text-align:center;background:#7B1FA2;color:#FFFFFF;padding:6px;border-radius:4px;font-weight:600;">复杂度：高</div>', unsafe_allow_html=True)

    # 三行：价值高 / 中 / 低
    for v, v_label, v_bg, v_fg in [
        ("高", "价值：高", "#FFEBEE", "#C62828"),
        ("中", "价值：中", "#FFF8E1", "#F57F17"),
        ("低", "价值：低", "#ECEFF1", "#455A64"),
    ]:
        row = st.columns([1, 5, 5, 5])
        row[0].markdown(
            f'<div style="background:{v_bg};color:{v_fg};writing-mode:vertical-rl;text-orientation:upright;'
            f'text-align:center;padding:10px 4px;border-radius:4px;font-weight:700;font-size:12px;">{v_label}</div>',
            unsafe_allow_html=True,
        )
        row[1].markdown(render_cell(v, "低"), unsafe_allow_html=True)
        row[2].markdown(render_cell(v, "中"), unsafe_allow_html=True)
        row[3].markdown(render_cell(v, "高"), unsafe_allow_html=True)

    st.caption("💡 管理层提示：关注左上角「速胜」机会；关注右下角「钱坑」「鸡肋」，主动终止可释放资源。AI 推断的评分（带🤖）仅供参考，重要决策前请人工复核。")
