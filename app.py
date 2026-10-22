"""
需求看板 AI 助手 POC - 主应用
Streamlit 单文件应用，演示 PRD 第五章 A1~A6 全部 AI 能力
"""
from __future__ import annotations
import os
import re
import sys
import json
from datetime import datetime, timedelta
from collections import Counter
from contextlib import contextmanager

import streamlit as st
import streamlit.components.v1 as components  # 仅用于写登录 Cookie（Streamlit 无原生写 Cookie API）
import psycopg2  # 仅用于 init_state 区分「连接类异常」与「代码类异常」（t24 F-文案），无新依赖

# 同目录下导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sample_data import (
    SEED_USERS, USER_ROLES,
    PROJECTS as SAMPLE_PROJECTS, SAMPLE_REQUIREMENTS,
    STATUS_COLORS, PRIORITY_COLORS,
    SOURCE_LABELS, STATUS_LABELS, PRIORITY_LABELS, ALL_TAGS,
    EVIDENCE_TYPES, RATING_LABELS, RATING_COLORS,
)
from llm_gateway import PROVIDERS, parse_json_response
import ai_helper
import auth
import db
import dingtalk
import email_notify


# ============================================================
# 配置
# ============================================================

APP_TITLE = "需求看板 AI 助手"
APP_SUBTITLE = "需求全流程管理 · AI 辅助"

TODAY = datetime.now().date()
TODAY_STR = TODAY.strftime("%Y-%m-%d")
MONTH_START = TODAY.replace(day=1)                    # 本月 1 号
WEEK_START = TODAY - timedelta(days=TODAY.weekday())  # 本周一
MONTH_START_STR = MONTH_START.strftime("%Y-%m-%d")
WEEK_START_STR = WEEK_START.strftime("%Y-%m-%d")

# 项目阶段（关联项目时需选择）
PROJECT_STAGES = [
    "技术预研/需求确认",
    "研发设计",
    "物料下单",
    "生产装配",
    "厂内调试",
    "厂外调试",
    "售后处理",
]

# 大项目（固定分组，不允许自由命名；新建/修改项目时从此列表中选择）
# 注意用「其他」（与存量数据一致的标准写法），不要写成「其它」，否则会分裂成两张卡
PROJECT_GROUPS = ["EQ-200", "EQ-400", "EQ-700", "研发AI赋能", "其他"]


# ============================================================
# 评价体系配置
# ============================================================

# 需求方满意度（一期）：需求完成并由提出人确认后，由提出人给出 1~5 星。
# 0 表示未评价——与「已终止」「未完成」的需求区分开，统计时不计入分母。
SATISFACTION_LABELS = {5: "非常满意", 4: "满意", 3: "一般", 2: "不满意", 1: "很不满意"}
SATISFACTION_COLORS = {5: "#43A047", 4: "#7CB342", 3: "#FB8C00", 2: "#F4511E", 1: "#E53935"}

# 系统使用体验问卷（二期）：自研短问卷，替代标准 SUS 量表。
# 原版 SUS 是 10 题学术量表（含反向题计分、68 分基线），对几十人的内部团队没有决策价值，
# 且反向题容易填错；这里取「同样能判断系统好不好用、哪里要改」的最小题目集。
SURVEY_COOLDOWN_DAYS = 3  # 同一用户两次被触达问卷的最短间隔
SURVEY_QUESTIONS = [
    "我在这个系统里能顺畅地完成我的工作",
    "我需要查找的信息（需求、进度、附件）很容易找到",
    "AI 助手对我的实际工作有帮助",
    "我愿意继续使用这个系统",
]
# 常用功能多选（不计分，用于了解功能使用面）
SURVEY_FEATURES = [
    "新建需求",
    "我的看板",
    "需求详情 / 状态流转",
    "AI 搜索",
    "中心总览",
    "项目视图",
    "需求管理 / 批量导入",
    "周报",
    "用户管理",
]
SURVEY_SCORE_LABELS = {5: "非常同意", 4: "同意", 3: "一般", 2: "不同意", 1: "很不同意"}

# ---- 评估体系三期增强（KUI + 改进闭环，见 设计文档）----
# 小样本预警阈值（03 蓝图 §3.2/M15）：内部团队仅 12 人，客诉系统的 30 永远不可达，
# 预警会常驻失效，因此取 5——不足 5 份提交时明确提示「结论仅供参考」。
SURVEY_MIN_SAMPLE = 5
# KUI（看板可用性指数）自建 4 档内部基线（03 蓝图 §4.2）：仅用于纵向自比（本期 vs 上期），
# 不引入 SUS 的 ×2.5 换算与 68/51/80.3 分档——那套阈值只对标准 10 题量表成立。
# 阈值调整须在文档与本注释留痕（体系演进纪律）。
KUI_BANDS = [
    ("A · 良好可用", 4.0, None, "#43A047"),   # KUI ≥ 4.0
    ("B · 基本可用", 3.0, 4.0, "#1E88E5"),    # 3.0 ≤ KUI < 4.0
    ("C · 体验不佳", 2.0, 3.0, "#FB8C00"),    # 2.0 ≤ KUI < 3.0
    ("D · 需重点改进", None, 2.0, "#E53935"),  # KUI < 2.0
]
KUI_NOTE = "KUI 为自研 4 题短问卷指数（1.0-5.0），不可与标准 SUS（0-100）对比"
# 问卷题干版本（题干冻结纪律，03 蓝图 §4.3）：v1 四题题干冻结；改题干=换指标，
# 必须递增本版本号并按版本分组统计，禁止跨版本混算均分。
SURVEY_QUESTION_SET = "v1"
# 改进项归类（管理层填写，03 蓝图 §5.2）
IMPROVEMENT_CATEGORIES = ["功能", "体验", "AI", "数据", "其他"]


# 用户数据的内存缓存与查找表（每次 init_state 从 DB 重建）
USERS: list[dict] = []
USER_NAME_TO_ID: dict[str, str] = {}
USER_ID_TO_NAME: dict[str, str] = {}


# ============================================================
# 缓存层
# ============================================================
# Streamlit 每次 UI 交互都会重跑整个脚本，下面这些昂贵操作原本每次都重复执行。
# 全部用 st.cache_data 兜住：缓存跨会话共享，交互行为与之前完全一致。

@st.cache_data(ttl=60, show_spinner=False)
def _cached_db_stats() -> dict:
    """侧边栏统计。内含 pg_database_size 与附件目录递归扫描，实测约 287ms。

    这行统计文字容忍最多 60 秒的陈旧度。
    """
    return db.db_stats()


@st.cache_data(max_entries=64, show_spinner=False)
def _cached_attachment(req_id: str, filename: str, _size: int = 0) -> bytes | None:
    """附件字节。详情页原本每次 rerun 都把所有附件全量读盘。

    _size 参与缓存 key，文件被替换（大小变化）时自然失效。
    """
    return db.read_attachment(req_id, filename)


@st.cache_data(max_entries=32, show_spinner=False)
def _cached_pdf_b64(data: bytes) -> str:
    """PDF 的 base64 编码结果。一份 5MB 的 PDF 每次 rerun 都重编码代价很高。"""
    import base64
    return base64.b64encode(data).decode("utf-8")


@st.cache_data(max_entries=32, show_spinner=False)
def _cached_table_preview(data: bytes, ext: str):
    """Excel/CSV 预览的解析结果（pandas 解析原本每次 rerun 都重跑）。"""
    import io
    import pandas as pd
    if ext == "csv":
        return pd.read_csv(io.BytesIO(data), dtype=str).fillna("")
    return pd.read_excel(io.BytesIO(data), sheet_name=0, dtype=str).fillna("")


@st.cache_data(max_entries=8, show_spinner=False)
def _cached_excel_parse(raw: bytes, default_requester_id: str):
    """导入文件的逐行校验结果。文件上传后跨 rerun 持久化，原本每次 rerun 都重跑
    pd.read_excel + 整表 iterrows + 每行 _excel_row_to_req，这里按文件内容缓存。

    只缓存「与库内容无关」的解析结果；ID 冲突检查依赖当前库与「允许覆盖」勾选，
    必须在渲染时实时计算，不能进缓存。
    """
    import io
    import pandas as pd
    df = pd.read_excel(io.BytesIO(raw), sheet_name=0, dtype=str).fillna("")
    preview_rows = df.head(5).to_dict("records")
    parsed = []
    for _idx, row in df.iterrows():
        req, errs = _excel_row_to_req(row, default_requester_id=default_requester_id)
        parsed.append((req, errs))
    return preview_rows, parsed


def _invalidate_data_caches() -> None:
    """写库后清掉与统计相关的缓存，保证操作者立刻看到最新结果。"""
    _cached_db_stats.clear()
    _project_name_by_id.clear()
    _user_task_count_map.clear()
    _user_load_map.clear()


def _invalidate_attachment_cache() -> None:
    """附件增删后调用。删除再上传同名文件可能命中同一缓存 key，必须显式清理。"""
    _cached_attachment.clear()
    _cached_db_stats.clear()


def _mark_own_write() -> None:
    """记录「本会话刚刚写过库」，用于跳过一次立即的数据版本比对。

    写库后下一次 rerun 的数据库版本必然已变（含本会话自己的写入），
    先标记为已知，避免在加载/重载之间产生不一致的瞬时状态。
    """
    st.session_state["_own_write_pending"] = True


def _sync_from_db() -> None:
    """检测其它会话是否改动了数据，若有则重新加载（修复多人看不到彼此改动的问题）。

    只在版本指纹变化时重载；本会话自己刚写入的版本标记为已知，不重复重载。
    """
    ver = db.data_version()
    if st.session_state.get("_data_version") == ver:
        return
    # 首次进入，仅记录版本不重载（数据已由 init_state 加载）
    if "_data_version" not in st.session_state:
        st.session_state["_data_version"] = ver
        return
    if st.session_state.pop("_own_write_pending", False):
        st.session_state["_data_version"] = ver
        return
    st.session_state["_data_version"] = ver
    st.session_state.requirements = _normalize_requirements(db.load_all_requirements())
    st.session_state.projects = db.load_all_projects()
    st.session_state.users = db.load_all_users()
    _rebuild_user_maps()
    _invalidate_data_caches()


# ============================================================
# 状态徽章渲染（用 markdown HTML）
# ============================================================

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


# ============================================================
# 初始化 Session State
# ============================================================

def init_state():
    """初始化：先确保 DB 存在并预填种子数据，再加载到 session_state 缓存"""
    try:
        db.init_db(SAMPLE_REQUIREMENTS, SAMPLE_PROJECTS, SEED_USERS)
    except (psycopg2.OperationalError, RuntimeError) as e:
        # 连接类异常（连不上/认证失败/库不存在，db._connect 的 GBK 提示也是 RuntimeError）：
        # 保留原有排查指引
        st.error(f"数据库连接失败，请检查 .env 中的 DB_* 配置（DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD）。\n\n错误详情：{e}")
        st.stop()
    except Exception as e:
        # 代码类异常（如 DDL/SQL 与代码不匹配——典型场景：代码更新后进程热重载，表结构未跟上）：
        # 不误导用户去查 .env，指明真实方向（t24 F-文案修复，源于真实故障中 IndexError 被误报为连接失败）
        st.error(f"数据库初始化失败（{type(e).__name__}）：{e}\n\n"
                 "这通常与代码/表结构不一致有关（例如更新代码后进程未重启）。请重启应用；"
                 "若重启后仍报同样错误，再按连接问题检查 .env 中的 DB_* 配置。")
        st.stop()
    if "users" not in st.session_state:
        st.session_state.users = db.load_all_users()
    # 先建好姓名映射：下方加载需求时的「子需求自动拆分」默认标题需要执行人姓名
    _rebuild_user_maps()
    if "requirements" not in st.session_state:
        raw = db.load_all_requirements()
        # 迁移：历史「待分配」状态并入「需求池」（落库，仅首次）
        for _r in raw:
            if _r.get("status") == "待分配":
                _r["status"] = "需求池"
                db.save_requirement(_r)
        st.session_state.requirements = _normalize_requirements(raw)
    if "projects" not in st.session_state:
        st.session_state.projects = db.load_all_projects()
    if "provider" not in st.session_state:
        st.session_state.provider = "deepseek"
    if "api_key" not in st.session_state:
        st.session_state.api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if "model" not in st.session_state:
        st.session_state.model = ""  # 空表示用默认
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
    _rebuild_user_maps()


def _rebuild_user_maps():
    """从 session_state 重建用户名 <-> ID 查找表（用户增删改后调用）"""
    global USERS, USER_NAME_TO_ID, USER_ID_TO_NAME
    USERS = st.session_state.get("users", [])
    USER_NAME_TO_ID = {u["name"]: u["id"] for u in USERS}
    USER_ID_TO_NAME = {u["id"]: u["name"] for u in USERS}


def get_users() -> list[dict]:
    return st.session_state.get("users", [])


def get_current_user() -> dict | None:
    uid = st.session_state.get("current_user_id")
    if not uid:
        return None
    return next((u for u in get_users() if u["id"] == uid), None)


# ============================================================
# 登录态保持（浏览器刷新不掉线）
# ============================================================
# 链路：登录成功 → auth.issue_token 发随机令牌 + 写 Cookie（components.html
# 零高 iframe 内的 JS 直写 document.cookie，Streamlit 无原生写 Cookie API，
# 包内自证组件 iframe 带 allow-same-origin+allow-scripts，可写本站 Cookie）→
# 刷新后浏览器带 Cookie → st.context.cookies 读回 → 查令牌表还原登录态。
# ⚠️ 令牌表必须放 auth 模块：主脚本每次 rerun 从头重执行，模块级状态每轮
# 归零（首版把表放本文件，登录轮写入、刷新轮被清空，还原永远失败）；
# auth 经 import 只执行一次，表跨 rerun 存活。服务重启即清空（重登一次）。
# 还原时校验用户仍存在（改名/删除后旧令牌即失效）。
# 权衡（内网工具口径）：Cookie 经 JS 写入、无法 HttpOnly，拿到 Cookie 即可冒登；
# 内网部署 + 令牌随机 32 字节 + 退出即作废，风险可接受。
_AUTH_COOKIE = "kb_token"
_AUTH_TOKEN_TTL = 30 * 86400  # 30 天（与 auth._TOKEN_TTL 同值，改时两处同步）


def _emit_auth_cookie_js(tok: str | None) -> None:
    """零高 iframe 写/清登录 Cookie。tok=None 时置 max-age=0 即清除。
    组件 iframe 与主文档同源，document.cookie 直写本站 Cookie。"""
    val = (f"{_AUTH_COOKIE}=; max-age=0; path=/" if tok is None else
           f"{_AUTH_COOKIE}={tok}; max-age={_AUTH_TOKEN_TTL}; path=/; SameSite=Lax")
    components.html(f"<script>document.cookie={json.dumps(val)};</script>", height=0)


def _try_restore_login_from_cookie() -> bool:
    """未登录时尝试从 Cookie 还原登录态（浏览器刷新场景），成功返回 True。"""
    try:
        tok = st.context.cookies.get(_AUTH_COOKIE)
    except Exception:
        return False
    uid = auth.consume_token(tok)
    if not uid or not any(u["id"] == uid for u in get_users()):
        return False
    st.session_state.current_user_id = uid
    st.session_state.logged_in = True
    # 与登录成功同口径补一条 login 埋点：刷新=新会话，DAU/WAU 口径需要它；
    # 失败只打日志，绝不阻塞还原（吸收客诉「埋点失败不抛异常」纪律）
    u = get_current_user()
    if u:
        try:
            db.log_user_activity(u["id"], u.get("name", ""), u.get("role", ""), "", "login")
        except Exception as e:
            print(f"[activity] 刷新还原登录埋点写入失败（不影响登录）：{e}")
    return True


def reload_users():
    """用户增删改后，重新加载并重建查找表"""
    st.session_state.users = db.load_all_users()
    _rebuild_user_maps()
    _invalidate_data_caches()
    _mark_own_write()


def _user_notify_channel(u: dict) -> str:
    """用户当前可达的提醒渠道（用户管理列表展示用）：钉钉群@ / 邮件可叠加，均无则警示。"""
    chans = []
    if u.get("notify_by_group", True) and (u.get("phone") or u.get("dingtalk_userid")):
        chans.append("钉钉群@")
    if u.get("email"):
        chans.append("邮件")
    return " + ".join(chans) or "⚠ 无"


def _default_user_email(name: str) -> str:
    """公司邮箱规则：姓名拼音全小写 + @公司域名（.env 的 USER_EMAIL_DOMAIN，默认 example.com）。
    未装 pypinyin 或含无法转换的字符时返回空串（调用方据此保留空值）。"""
    try:
        from pypinyin import lazy_pinyin
        prefix = "".join(lazy_pinyin((name or "").strip())).lower()
    except Exception:
        return ""
    if not prefix or not prefix.isascii():
        return ""
    domain = os.getenv("USER_EMAIL_DOMAIN", "example.com").strip()
    return f"{prefix}@{domain}" if domain else ""


def _biz_groups_from_req(req: dict | None) -> list[str]:
    """从需求标题解析业务群标签：返回「已配置 webhook」且在标题中出现（不分大小写）的标签，
    按出现顺序去重。标题未命中任何已配置业务群时返回 []（调用方回落默认群）。

    只认已配置的标签——新增/停用业务群只改 .env（DINGTALK_WEBHOOK_<标签>），无需改代码；
    标题无标签、或标签对应群尚未配置的需求一律走默认群（DINGTALK_WEBHOOK）。
    """
    if not req:
        return []
    title = (req.get("title") or "").upper()
    if not title:
        return []
    hits = sorted((title.find(k), k) for k in dingtalk.group_keys() if k in title)
    return [k for _, k in hits]


def _notify(user_ids: list[str], text: str, req: dict | None = None) -> dict:
    """双通道提醒：群消息按需求标题路由到对应业务群，邮件则私发给每位填了邮箱的相关人。

    优先级门控：传入 req 时仅 P0/P1/P2 推送；P3 与未填（未知/空）整套流程静默不发。
    通道规则（并行，可叠加）：
    - 群消息路由：需求标题含已配置的业务标签（如 A/B/C）→ 发对应业务群（命中几个发几个）；
      未命中 → 发默认群（DINGTALK_WEBHOOK）。无 req 的提醒（问卷/评价等）始终走默认群；
    - 在提醒群中（档案勾选「在钉钉提醒群中」且填了手机号/钉钉 userid）→ 群消息 @ 到人
      （@ 仅对该群成员高亮；不在路由群内的人靠邮件兜底）；
    - 填了邮箱（且 .env 配置了 SMTP）→ 邮件私发，与是否在群无关——在群里被 @ 的人同样收邮件；
    - 群消息只点名群内可达者——不在群的人不出现在群消息里；全部接收人都不可达群渠道时，
      干脆不发群消息，避免打扰群内无关用户；
    - 两条路都走不通的（不在群 + 无邮箱 / SMTP 未配置）：打印日志提示管理员补联系方式；
    - 任何发送失败只打印日志，不阻塞主流程。

    返回送达回执（三期新增，既有调用方可忽略）：{"delivered": [...], "dead": [...]}。
    delivered=已交由钉钉群@或邮件通道受理的 user_id（双通道可达者只记一次）；dead=两条通道都不可达者。
    改进闭环据此决定是否写 notified_at——外发失败不得把改进项标记为「已回告」，
    站内提醒条另由 ack 独立控制（见 db.pending_improvement_notice）。
    """
    # 优先级门控：P3 / 未填优先级的需求，所有场景都不提醒
    if req is not None:
        pr = (req.get("priority") or "").strip() or "未知"
        if pr not in ("P0", "P1", "P2"):
            return
    ids = list(dict.fromkeys(uid for uid in user_ids if uid))
    by_id = {u["id"]: u for u in get_users()}
    group_ok = dingtalk.any_group_configured()
    email_ok = email_notify.configured()

    group_ids, email_ids, dead_ids = [], [], []
    for uid in ids:
        u = by_id.get(uid)
        if not u:
            continue
        in_group = u.get("notify_by_group", True) and (u.get("phone") or u.get("dingtalk_userid")) and group_ok
        by_email = bool(u.get("email")) and email_ok
        if in_group:
            group_ids.append(uid)
        if by_email:
            email_ids.append(uid)
        if not in_group and not by_email:
            dead_ids.append(uid)

    if group_ids:
        phones = [by_id[i]["phone"] for i in group_ids if by_id[i].get("phone")]
        userids = [by_id[i]["dingtalk_userid"] for i in group_ids if by_id[i].get("dingtalk_userid")]
        names = "、".join(USER_ID_TO_NAME.get(i, i) for i in group_ids)
        # 业务群路由：标题命中已配置业务标签 → 逐个发对应业务群；未命中 → 默认群兜底
        for key in _biz_groups_from_req(req) or [None]:
            dingtalk.send_text(f"【需求看板】{names}：{text}", phones, userids, group=key)
    if email_ids:
        emails = [by_id[i]["email"] for i in email_ids]
        names = "、".join(USER_ID_TO_NAME.get(i, i) for i in email_ids)
        email_notify.send_mail(emails, "需求看板提醒", f"【需求看板】{names}：\n{text}")
    if dead_ids:
        names = "、".join(USER_ID_TO_NAME.get(i, i) for i in dead_ids)
        print(f"[notify] 无法送达（不在提醒群且无邮箱或 SMTP 未配置）：{names} — {text}")
    # 送达回执：调用方（改进闭环回告/催办）据此判断是否可标记「已回告」
    return {"delivered": list(dict.fromkeys(group_ids + email_ids)), "dead": dead_ids}


def reload_from_db():
    """从数据库重新加载到 session_state（增删改后调用）"""
    st.session_state.requirements = _normalize_requirements(db.load_all_requirements())
    st.session_state.projects = db.load_all_projects()


def get_projects() -> list[dict]:
    return st.session_state.get("projects", [])


def _project_display_name(p: dict) -> str:
    """项目显示名：有「核心亮点/定制需求」时显示「名称（亮点）」，否则只显示名称"""
    hl = (p.get("highlight") or "").strip()
    return f"{p['name']}（{hl}）" if hl else p["name"]


def _req_project_name(r: dict) -> str:
    """需求关联项目的显示名（含亮点后缀），未关联返回空串"""
    pid = r.get("project_id", "")
    if not pid:
        return ""
    return _project_name_by_id().get(pid, "")


@st.cache_data(ttl=30, show_spinner=False)
def _project_name_by_id() -> dict:
    """project_id -> 显示名。筛选/行渲染会逐条需求调用，预先建表避免 O(n×m)。"""
    return {p["id"]: _project_display_name(p) for p in get_projects()}


def _owner_names(r: dict, default: str = "未指派") -> str:
    """执行人显示名（多人用顿号连接），空则返回 default"""
    ids = [i for i in r.get("owner_ids", []) if i]
    if not ids:
        return default
    return "、".join(USER_ID_TO_NAME.get(i, "未知") for i in ids)


# ============================================================
# 子需求（多执行人自动拆分：n 个执行人 ↔ n 条子需求，一一对应独立流转）
# ============================================================

def _append_system_comment(req: dict, text: str) -> None:
    """系统自动留言（如执行人调整时为被移除的子需求留档）。"""
    req.setdefault("comments", []).append({"user": "系统", "time": TODAY_STR, "text": text})


def _record_status_change(req: dict, from_status: str, to_status: str, user_label: str) -> None:
    """父需求状态流转留痕（「系统评估 → 阶段效率」的数据源）。

    与子需求 sub["history"] 完全同构：{time, user, from, to}，time 精确到分钟。
    只在 from != to 时记录；字段存进 requirements.data JSON，db 层零改动。
    user_label 传操作人姓名；系统自动联动传「（子需求联动）」。
    """
    if from_status == to_status:
        return
    req.setdefault("status_history", []).append({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "user": user_label,
        "from": from_status,
        "to": to_status,
    })


def _sync_sub_requirements(req: dict) -> None:
    """保持「子需求 ↔ 执行人」一一对应（多执行人时自动拆分）。

    - 多执行人：新执行人自动补建子需求（默认标题=父需求标题-执行人名，负责人可再编辑细化）；
      执行人被移除时其子需求随之删除，若已有流转记录则在评论时间线留档；
    - 单执行人 / 无执行人：不拆分，既有子需求全部移除（有流转记录的留档）；
    - 末尾按子需求状态联动父需求（全量联动）。
    """
    owners = list(dict.fromkeys(i for i in req.get("owner_ids", []) if i))  # 去重保序，保证 1:1
    subs = req.setdefault("sub_requirements", [])
    children_changed = False
    if len(owners) <= 1:
        if subs:
            for sub in subs:
                if sub.get("history"):
                    _append_system_comment(
                        req, f"子需求「{sub.get('title')}」（{sub['id']}）已随执行人调整移除，最终状态「{sub.get('status')}」。"
                    )
            req["sub_requirements"] = []
            children_changed = True
    else:
        # 1) 删除执行人已被移除的子需求（有流转记录的留档）
        kept = []
        for sub in subs:
            if sub.get("owner_id") in owners:
                kept.append(sub)
            elif sub.get("history"):
                gone = USER_ID_TO_NAME.get(sub.get("owner_id", ""), sub.get("owner_id", ""))
                _append_system_comment(
                    req, f"子需求「{sub.get('title')}」（{sub['id']}，执行人 {gone}）已随执行人移除而删除，最终状态「{sub.get('status')}」。"
                )
        if len(kept) != len(subs):
            children_changed = True
        req["sub_requirements"] = kept
        # 2) 为尚无子需求的执行人补建（编号沿用历史最大序号，保证 ID 不重复）
        seq_nums = []
        for s in subs:
            m = re.search(r"-S(\d+)$", str(s.get("id", "")))
            if m:
                seq_nums.append(int(m.group(1)))
        seq = max(seq_nums, default=0)
        have = {s.get("owner_id") for s in kept}
        for uid in owners:
            if uid in have:
                continue
            have.add(uid)  # 防重复执行人导致同人多条子需求
            seq += 1
            children_changed = True
            req["sub_requirements"].append({
                "id": f"{req['id']}-S{seq}",
                "title": f"{req['title']}-{USER_ID_TO_NAME.get(uid, uid)}",
                "note": "",
                "owner_id": uid,
                "status": "待启动",
                "history": [],
                "created_at": TODAY_STR,
            })
    # 3) 仅当子需求集合实际变化时联动父状态（如唯一的「进行中」子需求被移除、其余已全部完成时自愈）；
    #    执行人未变时不干预，避免覆盖负责人刚手动设置的父状态
    if children_changed:
        agg = _aggregate_parent_status(req)
        if agg and agg != req.get("status"):
            _record_status_change(req, req.get("status", ""), agg, "（子需求联动）")
            req["status"] = agg
            if agg in ("已完成", "已终止"):
                req["actual_completion_date"] = TODAY_STR


def _aggregate_parent_status(req: dict) -> str | None:
    """由子需求状态推导父需求状态（全量联动）。

    - 全部子需求到达终态 → 「已完成」（若全部为「已终止」则为「已终止」）；
    - 任一子需求「进行中」→「进行中」；
    - 其余情况返回 None（不干预父需求当前状态）。
    """
    subs = req.get("sub_requirements") or []
    if not subs:
        return None
    sts = [s.get("status") for s in subs]
    if all(s in ("已完成", "已终止") for s in sts):
        return "已完成" if "已完成" in sts else "已终止"
    if "进行中" in sts:
        return "进行中"
    return None


def gen_req_id() -> str:
    yyyymm = TODAY.strftime("%Y%m")
    seq = db.next_seq(yyyymm)
    return f"REQ-{yyyymm}-{seq:04d}"


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


# ============================================================
# 三段式布局（t31 界面改版一期）：深色侧边栏 + 顶部 Header + 灰底卡片化内容区
# 布局语言取自用户确认的草图 原型草图
# ============================================================

# 侧边栏导航分组（t31）：分组只影响展示顺序与分区标题；
# 页面可见性仍由 main() 的角色过滤决定（pages.pop("📋 系统评估")）。
NAV_GROUPS = [
    ("", ["📊 中心总览", "📋 我的看板"]),
    ("需求", ["📦 需求管理", "📂 项目视图", "➕ 新建需求", "📄 需求详情", "🔍 AI 搜索", "📅 周报"]),
    ("管理与评估", ["📋 系统评估", "👥 用户管理"]),
]

# 当前页会话键（t31 起取代旧 nav_radio；nav_radio 为历史残留键，登出时一并清理）
NAV_STATE_KEY = "_nav_page"

# 卡片序号发生器（t34 F-01）：每轮脚本运行在 _inject_global_css() 里归零，
# _card() 依次取号生成稳定 key（kb_card_0..N）——供 CSS 以 [class*="st-key-kb_card_"] 挂钩。
# 背景说明：t31 曾挂钩的旧 border 容器 testid，经 21 号审查在 Streamlit 1.61.1 包内
# 静态自证不存在（命中 0），属静默失效选择器，故改为 key 方案
#（st-key- 前缀类由 Streamlit 对带 key 的容器自动生成，包内静态自证命中 27 个文件 > 0）。
_CARD_SEQ = {"n": 0}


def _inject_global_css():
    """全局样式注入（t31 界面改版；t34 修挂钩）：纯 <style> 注入，不引任何外部框架/CDN。

    选择器原则：① 只用「已在已安装 Streamlit 包内静态自证存在」的挂钩（21 号审查要求，
    防止静默失效选择器）——卡片用 st.container(key=...) 生成的 [class*="st-key-kb_card_"]，
    主区留白用 [data-testid="stMain"]（包内命中 > 0）；② 自绘元素用 kb-* 自有类；
    ③ 导航态规则限定在 keyed 导航容器 [class*="st-key-kb_nav"] 作用域，不误伤问卷 CTA
    （问卷 CTA 单独 keyed 容器还原草图实心蓝）。
    取值对照草图：深色侧边栏 #1E2A3A / 文字 #C7D0DC、主题蓝 #409EFF、内容区灰底 #F5F6F8、
    白卡片 #FFF 圆角 10px / 边框 #EDEFF2、危险红 #F56C6C。
    """
    _CARD_SEQ["n"] = 0  # 每轮运行归零，保证 _card() 的 key 在 rerun 间稳定
    st.markdown("""
    <style>
    /* ===== 0) 全局盒模型归一 =====
       Streamlit 组件自带的默认外边距会打破「纵向节奏」：例如按钮内的 <p> 自带上下
       margin 会把按钮撑高、且文字不再垂直居中。统一清零，各处再按需显式设置，
       避免出现「文字与分隔线重合」这类由默认外边距引起的错位。 */
    [data-testid="stMarkdownContainer"] p { margin: 0; }
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p { margin: 0; }
    [data-testid="stSidebar"] hr { border-color: rgba(255,255,255,.10); margin: .5rem 0; }
    /* ===== 1) 深色侧边栏（参考客诉系统 AppSidebar）===== */
    [data-testid="stSidebar"] { background: #1E2A3A; }
    [data-testid="stSidebar"] h3 { color: #FFFFFF; }
    /* 侧边栏宽度：Streamlit 1.61 把它做成 React state 内联在 <section> 上
       （默认 300px，可拖拽 200-600px），且没有主题配置项可改，故只能用 !important
       压过内联宽度并锁死 min/max，使拖拽不再生效。
       224px = 客诉系统侧边栏 w-56（14rem）。
       宽度值同时以变量 --kb-sidebar-w 暴露，供第 6 节的 fixed 顶栏消费——
       这样「侧边栏宽度」只有一处事实，不会出现顶栏左偏移与侧边栏不同步的错位。 */
    /* 侧边栏宽度与顶栏高度是两个「跨区块共享」的尺寸，统一提为变量：
       --kb-sidebar-w 供顶栏左偏移消费；--kb-header-h 供顶栏自身、主区占位、侧边栏品牌区消费。
       这样「品牌标题与顶栏标题同一水平线」「内容紧贴顶栏下缘」都是由同一个数推出来的，
       不再需要在几处手写同样的像素值（v4 时代的两处耦合就是这么来的）。 */
    :root { --kb-sidebar-w: 224px; --kb-header-h: 57px; }
    [data-testid="stSidebar"] {
        width: var(--kb-sidebar-w) !important;
        min-width: var(--kb-sidebar-w) !important;
        max-width: var(--kb-sidebar-w) !important;
        flex: 0 0 var(--kb-sidebar-w) !important;
    }
    /* 侧边栏顶部那一行（Streamlit 的 stSidebarHeader：logo 占位 + 收起按钮）
       会把品牌区整体推低约 64px，于是「需求看板 AI 助手」比顶栏标题低一截。
       侧边栏宽度已被 !important 锁死 224px，收起按钮实际失去意义，
       故整行隐藏，改由品牌区自己占「与顶栏等高」的一条，使两侧标题落在同一水平线上。 */
    [data-testid="stSidebarHeader"] { display: none !important; }
    /* 稳定滚动条槽位会在两侧各留一条空槽（本主题下约 0.5rem），
       加上它侧边栏内容实际只有 ~208px 可用，视觉上明显偏窄；置为 auto 去掉。
       （选择器取值自 Streamlit 包内 eelgd2m2 组件定义） */
    [data-testid="stSidebarContent"] { scrollbar-gutter: auto !important; }
    /* 底部 sidebarTopSpace（Streamlit 侧边栏用户区自带 6rem 下内边距）清零：
       v5 高度链（见下方贴底实现）已让用户区吃满滚动容器，这里每留 1rem 底内边距，
       footer（个人信息/退出登录）就会被从底边顶开同样距离；置 0 才是真正贴住最下端。 */
    [data-testid="stSidebarUserContent"] { padding-bottom: 0 !important; }
    /* 侧边栏内元素纵向间距：默认 gap 1rem 太散，收窄到 .25rem（行高由按钮自身撑） */
    [data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: .25rem; }
    /* 品牌区：与顶栏**等高**，内容纵向居中 → 品牌标题与顶栏标题精确落在同一水平线
       （顶栏 57px 内容居中在 28.5px；品牌区 57px 内容居中也在 28.5px，由同一个变量推出）。
       高度用 box-sizing:border-box，让底部分隔线含在 57px 内，不额外撑高。 */
    .kb-brand {
        height: var(--kb-header-h); box-sizing: border-box;
        display: flex; flex-direction: column; justify-content: center;
        padding: 0 6px; margin-bottom: 6px;
        border-bottom: 1px solid rgba(255,255,255,.10);
    }
    .kb-brand .t { font-size: 15px; color: #FFF; font-weight: 600; letter-spacing: .3px; line-height: 1.35; }
    .kb-brand .s { font-size: 11px; color: #7C8CA0; margin-top: 4px; }
    .kb-nav-group { font-size: 10.5px; color: #66788E; letter-spacing: .5px; padding: 6px 6px 2px; }
    /* 导航区与上方品牌区分隔线之间留一段呼吸空间（用户要求「按钮往下移一点」）。
       放在导航容器自身的 padding-top 上，而不是加在品牌区的 margin-bottom：
       分隔线属于品牌区，改它的外边距会把线也推走；改这里只让按钮下移，线的位置不动。 */
    [data-testid="stSidebar"] [class*="st-key-kb_nav"] { padding-top: .625rem; }
    /* 导航按钮：几何取自客诉系统 .nav-item（gap 10px / 内边距 5px 10px / 13.5px / 行高 1.4）。
       Streamlit 按钮会给文字套一层 <p>，p 自带上下 margin 会把按钮撑高且不居中，
       故显式清零（margin/line-height），让按钮盒高 = 文字高 + 2×5px。 */
    [data-testid="stSidebar"] [class*="st-key-kb_nav"] button[kind="secondary"] {
        background: transparent; color: #C7D0DC; border: none;
        border-radius: 6px; text-align: left; justify-content: flex-start;
        box-shadow: none; padding: 5px 10px; font-size: 13.5px; font-weight: 400;
        min-height: 0; line-height: 1.4;
    }
    [data-testid="stSidebar"] [class*="st-key-kb_nav"] button[kind="secondary"] p,
    [data-testid="stSidebar"] [class*="st-key-kb_nav"] button[kind="primary"] p {
        margin: 0 !important; padding: 0 !important; font-size: inherit;
        line-height: inherit; text-align: left;
    }
    [data-testid="stSidebar"] [class*="st-key-kb_nav"] button[kind="secondary"]:hover {
        background: rgba(255,255,255,.08); color: #FFFFFF;
    }
    /* 导航按钮（当前页）：主题蓝半透明底 + 白字加粗（客诉系统 active-class !bg-white/12） */
    [data-testid="stSidebar"] [class*="st-key-kb_nav"] button[kind="primary"] {
        background: rgba(64,158,255,.16); color: #FFFFFF; border: none;
        border-radius: 6px; text-align: left; justify-content: flex-start;
        font-weight: 600; padding: 5px 10px; font-size: 13.5px;
        min-height: 0; line-height: 1.4;
    }
    [data-testid="stSidebar"] [class*="st-key-kb_nav"] button[kind="primary"]:hover {
        background: rgba(64,158,255,.28);
    }
    /* 侧边栏内输入控件适配深色底（LLM 配置 expander 内） */
    [data-testid="stSidebar"] input,
    [data-testid="stSidebar"] [data-baseweb="select"] > div {
        background: rgba(255,255,255,.06); color: #E2E8F0 !important; border-color: #334155;
    }
    /* 侧边栏说明文字 */
    [data-testid="stSidebar"] [data-testid="stCaptionContainer"],
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] li { color: #93A1B5; }
    /* 底部用户区（参考客诉系统 AppSidebar：用户卡片 + 退出入口钉在侧边栏最底部）。
       分隔线用容器自身的 border-top，不再用 st.markdown("---")：
       ① <hr> 是独立元素、位置相对容器会漂移；② 容器的 padding-top 会让 <hr> 下移，
       而 sticky 的不透明背景又会盖住它的上半部分，结果就是那条线被切一半、
       正好压在下方用户名上（用户截图可见）。
       贴底实现（v5，v1–v4 都没真正到底，教训见下）：
       v1 `position: sticky; bottom: 0` —— 空操作：sidebar 的 <section> 被 react-resizable
          内联设成 height:auto，内容不满时没有粘附余量，用户区停在内容末尾。
       v2 `margin-top: auto` + 一条「section → stSidebarContent → stSidebarUserContent →
          纵向块」的 flex 高度链 —— 仍不到底：该链任一环没有确定高度（或中间还夹了一层
          我没预料到的 div），flex-grow 就分不到余量，auto 外边距也无从计算。
       v3/v4 给纵向块写死 min-height:calc(100dvh - 7rem) —— 机制对但常量错：顶部真实占用
          只有收起按钮行（包内 defaultHeaderHeight≈2.19rem）+ 其下边距 1rem ≈ 3.2rem，
          加上当时用户区的 1rem 底内边距共约 4.2rem。7rem 多减的 ~2.8rem 让纵向块
          永远比可视区矮一截——footer 沉到块底也碰不到侧边栏底边，换主题/换字号还会再失准。
       v5（当前）不再「视口高减手估常量」，而是从全链唯一高度确定的地方——滚动容器——
          把高度传导下来：stSidebarContent 自带 height:100%（包内 eelgd2m2 定义，随 <section>
          拉满视口），拉成 flex 列后：用户区 flex-grow 吃满剩余高度 → 纵向块再 flex-grow →
          footer 沉到真正的底，全程无像素常量。内容超出时 flex-basis:auto 保底退化回文档流，
          footer 排在内容之后可滚动（不会盖住内容）。
       ⚠️ 注意区分两个名字相近的容器：
         stSidebarContent     = 整个侧边栏内容（滚动容器，height:100%，v5 的高度源头）
         stSidebarUserContent = 用户内容区（内部含品牌区/导航/问卷/LLM/用户区） */
    [data-testid="stSidebar"] { min-height: 100dvh !important; }
    /* 高度源头：滚动容器拉成 flex 列（子级 = stSidebarHeader + stSidebarUserContent） */
    [data-testid="stSidebarContent"] {
        display: flex !important; flex-direction: column !important;
    }
    /* 收起按钮行锁死不压缩：内容超高滚动时不被 flex 挤扁 */
    [data-testid="stSidebarHeader"] { flex: 0 0 auto; }
    /* 用户区吃满滚动容器剩余高度，纵向块（用户区唯一子 div，包内 eelgd2m5）再吃满用户区。
       这两条取代了 v3/v4 的 min-height:calc(100dvh - 7rem)。 */
    [data-testid="stSidebarUserContent"] {
        flex: 1 0 auto !important;
        display: flex !important; flex-direction: column !important;
    }
    [data-testid="stSidebarUserContent"] > div {
        flex: 1 0 auto !important;
        display: flex !important; flex-direction: column !important;
    }
    /* footer 是侧边栏最后一个元素（render_sidebar 第 6 段），其包装层 :last-child 可命中。
       v4 结论（保留）：st.container 的 div 外面还包着一层 stElementContainer（包内 JS 自证），
       footer 是纵向块的「孙级」而非「子级」——包装层也要拉成 flex 列并 flex-grow 吃满余量，
       footer 的 auto 外边距才能真正沉底。 */
    /* footer 的包装层：用 :has() **按内容**命中，不再依赖「它是第几个子 div、嵌了几层」。
       v5 用 `> div > div:last-child` 的位置选择器，只要 DOM 层级/顺序与假设不符，整条
       高度链就静默失效——表现正是 footer 停在半空、下方留一大片（用户截图所见）。
       :has() 只认「谁的直接子级是 footer 容器」，与位置和嵌套深度无关。 */
    [data-testid="stSidebarUserContent"] div:has(> [class*="st-key-kb_sidebar_footer"]) {
        display: flex !important; flex-direction: column !important;
        flex: 1 0 auto !important;
        justify-content: flex-end;   /* 双保险：即使 auto 外边距失效也把 footer 压到底 */
    }
    /* 内容超出视口时（导航+两个折叠区已比早期长），flex 分不到余量，
       此时靠 sticky 让 footer 钉在可视区底部——两种场景互补：
         · 内容不满一屏 → margin-top:auto + justify-content:flex-end 沉到最底
         · 内容超一屏   → sticky bottom:0 保持可见（不再被滚走） */
    [class*="st-key-kb_sidebar_footer"] {
        position: sticky; bottom: 0;
        background: #1E2A3A;
        border-top: 1px solid rgba(255,255,255,.10);
        padding-top: 10px;
        margin-top: auto !important;
    }
    [class*="st-key-kb_sidebar_footer"] button[kind="secondary"] {
        background: transparent; color: #C7D0DC;
        border: 1px solid rgba(255,255,255,.15); border-radius: 6px;
        margin-top: 8px; padding: 5px 10px; font-size: 12.5px; line-height: 1.4;
        min-height: 0;
    }
    [class*="st-key-kb_sidebar_footer"] button[kind="secondary"] p {
        margin: 0 !important; padding: 0 !important; font-size: inherit; line-height: inherit;
    }
    [class*="st-key-kb_sidebar_footer"] button[kind="secondary"]:hover {
        background: rgba(255,255,255,.08); color: #FFFFFF;
    }
    /* 底部用户块（参考客诉系统 AppSidebar 用户卡片：w-8 圆头像 + 姓名/角色两行，
       姓名 12.5px 白、角色 11px 暗）。显式定行高，避免继承 1.6 导致两行过散。 */
    .kb-sb-user { display: flex; align-items: center; gap: 9px; padding: 2px 0 0; }
    .kb-sb-user-meta { line-height: 1.25; min-width: 0; }
    .kb-sb-user-name {
        font-size: 12.5px; color: #E2E8F0; font-weight: 500;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .kb-sb-user-role {
        font-size: 11px; color: #7C8CA0; margin-top: 1px;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    /* 底部用户区里的 markdown 段落不能带上下外边距，否则会把头像行推离分隔线 */
    [class*="st-key-kb_sidebar_footer"] [data-testid="stMarkdownContainer"] { margin: 0; }

    /* ===== 2) 灰底内容区 + 白卡片（参考客诉系统 bg-gray-50 + 白卡）===== */
    .stApp, [data-testid="stAppViewContainer"] { background: #F5F6F8; }
    /* 主区留白（top 占位 + 左右对齐，两条规则解决两个实测错位）：
       ① padding-top = 顶栏占位块。顶栏总高由构造确定：10+36+10+1 = 57px
          （行高已锁 36px，见第 6 节），占位取 61px（57 + 4px 呼吸空隙）——
          内容紧贴冻结栏下缘（用户要求「主面上移，与冻结栏靠近对齐」）。
          ⚠️ 耦合提醒：改第 6 节顶栏的上下内边距或行高锁值时，必须同步改这里。
       ② padding-left/right 固定 1rem：Streamlit wide 布局有一条媒体查询
          （min-width: calc(736px + 2*(5rem - 1rem)) = 864px）会把内容左右内边距
          提到 5rem（wideSidePadding），而顶栏固定 1rem——于是 ≥864px 的视口上，
          页面标题比顶栏标题**右错 64px**（用户截图所见）。统一压回 1rem 后，
          所有页面内容与顶栏标题共用同一条左基线（对齐左上角），且任何视口宽度下一致。
          代价：超宽屏上内容不再有 5rem 大边沟，表格/卡片可铺满——对管理工具是收益。 */
    [data-testid="stMainBlockContainer"] {
        padding-top: calc(var(--kb-header-h) + 4px) !important;
        padding-left: 1rem !important; padding-right: 1rem !important;
    }
    /* 白卡片挂钩（t34 F-01）：_card() 的 st.container(key="kb_card_N") 会生成
       class="st-key-kb_card_N"（包内自证 st-key- 类存在，命中 27 文件）；
       border 来自 emotion 内联样式，须 !important 覆盖；background 内联未设、直接生效 */
    [class*="st-key-kb_card_"] {
        background: #FFFFFF; border: 1px solid #EDEFF2 !important; border-radius: 10px;
        box-shadow: 0 1px 3px rgba(15,23,42,.05);
    }
    /* 嵌套规则：卡片内部的 metric 去掉独立白框，避免「白底套白底」双边框
       （D2/D3/用户活跃等区块的 st.metric 位于卡片内） */
    [class*="st-key-kb_card_"] [data-testid="stMetric"] {
        background: transparent; border: none; box-shadow: none;
    }
    /* 卡片标题条：参考客诉系统卡片头（整宽分隔线 + 文字与正文同基线）。
       边框容器 st.container(border=True) 的实际内缩是「1px 边框 + 15px 内边距」= 16px，
       故用 -16px 负外边距顶到卡片边框内沿、再用 16px 内边距把文字放回 16px 基线。
       （实测：用 -15px 时标题条左右各差 0.6px，即少了那 1px 边框。） */
    .kb-card-hd {
        font-size: 14px; font-weight: 600; color: #1F2329; line-height: 1.4;
        margin: 0 -16px 12px; padding: 0 16px 12px;
        border-bottom: 1px solid #F0F2F5;
    }
    .kb-card-hd .kb-tag { float: right; font-size: 11.5px; font-weight: 400; color: #8A94A6; }

    /* ===== 3) KPI 块（参考客诉系统 KpiCard）：st.metric 白卡片化
       （作用于页面顶层裸 metric；卡片内 metric 由上方嵌套规则去框、不用内边距） ===== */
    [data-testid="stMetric"] {
        background: #FFFFFF; border: 1px solid #EDEFF2; border-radius: 10px;
        padding: 15px 17px; box-shadow: 0 1px 3px rgba(15,23,42,.05);
    }
    /* 指标名：12px 灰字，与数值之间留 7px（客诉系统 .kpi .lbl） */
    [data-testid="stMetric"] [data-testid="stMetricLabel"] {
        color: #8A94A6; font-size: 12px; line-height: 1.4; margin: 0 0 7px;
    }
    [data-testid="stMetric"] [data-testid="stMetricLabel"] p { margin: 0; font-size: 12px; }
    /* 指标值：25px / 700，略收字距（客诉系统 .kpi .val） */
    [data-testid="stMetric"] [data-testid="stMetricValue"] {
        font-size: 25px; font-weight: 700; letter-spacing: -.5px; line-height: 1.15;
        color: #1F2329;
    }
    /* 卡片内 metric 去掉内边距与白框，保持区块原有节奏 */
    [class*="st-key-kb_card_"] [data-testid="stMetric"] { padding: 0; }

    /* ===== 4) 表头（草图 table th）===== */
    [data-testid="stTable"] thead th, [data-testid="stTable"] th {
        background: #FAFBFC; color: #4E5969; font-weight: 600;
        border-bottom: 1px solid #EBEDF0; font-size: 12px;
    }

    /* ===== 5) 统一页头 / Header 胶囊 / 角标（参考客诉系统页面标题区）=====
       行高必须显式定死：Streamlit 正文继承 line-height 1.6，19px 标题的行盒会被撑到
       30.4px，而右侧主操作按钮只有 40px 高——两者各自「按行盒居中」时中心就差了
       7.6px（真实浏览器实测：按钮中心 212.5 vs 标题文字中心 220.1）。
       把标题/副标题行高收到 1.15 后，行盒约 22px，与文字高度一致，中心即可对齐。 */
    .kb-page-title { font-size: 19px; font-weight: 600; color: #1F2329; line-height: 1.15; }
    .kb-page-sub { font-size: 12px; color: #8A94A6; line-height: 1.15; }
    /* ---- 页面标题归一（用户要求：所有页标题与「系统评估」一致）----
       三个卡片化页走 _page_header（.kb-page-title 19px/600），其余 8 页直接
       st.markdown("## X")——Streamlit 原生 h2 明显更大，同一系统两种页头字号并存。
       这里把主区 h2 归一到与 .kb-page-title 完全相同的规格（19px/600/#1F2329/1.15），
       不改各页调用点，一处 CSS 全局生效。作用域限定 stMainBlockContainer，
       侧边栏与弹窗内的标题不受影响。
       下边距收到 2px：让 h2 下方的 st.caption 紧贴标题，观感与
       _page_header 的「标题+副标题」排布一致。 */
    [data-testid="stMainBlockContainer"] h2 {
        font-size: 19px !important; font-weight: 600 !important;
        color: #1F2329 !important; line-height: 1.15 !important;
        letter-spacing: .2px; margin: 0 0 2px 0 !important;
    }
    .kb-avatar {
        width: 28px; height: 28px; border-radius: 50%; background: #409EFF; color: #FFF;
        font-size: 12px; display: inline-flex; align-items: center; justify-content: center;
        font-weight: 600;
    }
    .kb-who { font-size: 12.5px; color: #1F2329; }
    .kb-dot {
        display: inline-block; width: 7px; height: 7px; background: #F56C6C;
        border-radius: 50%; margin-left: -4px; vertical-align: top;
    }

    /* ===== 5b) 横向标签菜单（版式参考客诉系统 SystemEval 的 Tab 切换条）=====
       参考实现的令牌逐条对应：
         容器 flex gap-1 border-b border-gray-200
         标签 px-4 py-2.5 / text-sm(14px) / font-medium(500) / -mb-px(下移1px压住容器线)
         当前项：主题色字 + border-b-2 主题色
         非当前：灰字(#6B7280) + border-b-2 transparent（保留占位，切换时不抖动）
       Streamlit 的按钮默认有底色/圆角/边框，这里全部去掉，只保留「文字 + 下边框」。
       容器自身画底部分隔线，按钮用负下外边距压上去，形成连续的一条线。 */
    [class*="st-key-kb_tabs_"] {
        border-bottom: 1px solid #E5E7EB;
        margin-bottom: 14px;
    }
    [class*="st-key-kb_tabs_"] [data-testid="stHorizontalBlock"] { gap: 4px !important; }
    [class*="st-key-kb_tabs_"] button {
        background: transparent !important; box-shadow: none !important;
        border: none !important; border-bottom: 2px solid transparent !important;
        border-radius: 0 !important;
        padding: 10px 16px !important; min-height: 0 !important;
        font-size: 14px !important; font-weight: 500 !important; line-height: 1.4;
        margin-bottom: -1px !important;      /* 压住容器那条线，避免 1px 缝 */
    }
    [class*="st-key-kb_tabs_"] button p {
        font-size: 14px !important; font-weight: 500 !important;
        margin: 0 !important; padding: 0 !important; line-height: inherit;
    }
    /* 非当前项：灰字（hover 加深） */
    [class*="st-key-kb_tabs_"] button[kind="secondary"] { color: #6B7280 !important; }
    [class*="st-key-kb_tabs_"] button[kind="secondary"]:hover {
        color: #374151 !important; background: transparent !important;
    }
    /* 当前项：主题色字 + 底部 2px 主题色横线（不硬编码色值，跟随全局主题） */
    [class*="st-key-kb_tabs_"] button[kind="primary"] {
        color: var(--primary-color, #409EFF) !important;
        border-bottom-color: var(--primary-color, #409EFF) !important;
        font-weight: 600 !important;
    }
    [class*="st-key-kb_tabs_"] button[kind="primary"] p { font-weight: 600 !important; }
    [class*="st-key-kb_tabs_"] button[kind="primary"]:hover {
        background: transparent !important;
    }

    /* 对话头像（st.chat_message）：emoji 头像（🧑‍💻/🤖）在 Windows Segoe UI Emoji 下
       渲染偏大、会溢出固定尺寸的头像框（用户反馈「框没把图标完整框住」）；且 emoji
       头像容器在包内没有专属 testid，无法可靠修正。改用 Streamlit 内置 Material 图标
       头像（avatar="user"/"assistant"）——SVG 尺寸由库保证恰好容纳；默认红/橙底色
       与系统配色冲突，统一改为主题色（用户=强调蓝，助手=侧边栏深蓝灰）。 */
    [data-testid="stChatMessageAvatarUser"] { background: #409EFF !important; }
    [data-testid="stChatMessageAvatarAssistant"] { background: #1E2A3A !important; }

    /* ===== 6) 顶栏（参考客诉系统 AppHeader：白底 + 下边框 + 左右内边距）=====
       要求：① 滚动时钉在顶部（冻结）；② 文字与页面内容左对齐。

       演进过程（记录在此避免回退）：
       v1 position:sticky —— 判定在 1.61 滚动结构下不生效。
       v2 position:fixed + left:240px + 手工补占位 —— 冻结做到了，但左偏移是硬编码的
          第二份事实（侧边栏宽度一改就错位），且底色必须用内容区灰。
       v3 position:sticky + align-self:stretch —— 理论上最优（无需偏移），但
          **实测仍未冻结**（用户浏览器验证），说明 stMain 与顶栏之间存在
          overflow:hidden/clip 的中间层，sticky 无从生效。
       v4（当前）position:fixed，但修掉 v2 的两个硬伤：
          ① 左偏移不再写死像素，改为消费 --kb-sidebar-w 变量（与侧边栏同源）；
          ② 横向内边距与内容区同源（1rem，等于 stMainBlockContainer 的左右 padding），
             于是顶栏文字与页面内容左边界由构造保证一致，而不是靠调数值对齐。
       代价（如实记录）：fixed 脱离文档流，需要手工补位。这里用
       stMainBlockContainer 的 padding-top 充当占位块——其值取自顶栏实测高度，
       属于「两处需同步」的耦合，改动顶栏内边距时须一并核对本段的 padding-top。 */
    [class*="st-key-kb_app_header"] {
        position: fixed; top: 0; right: 0; z-index: 1000;
        left: var(--kb-sidebar-w);
        width: auto !important; max-width: none !important;
        height: var(--kb-header-h); box-sizing: border-box;  /* 高度由变量定死，不再靠内容撑 */
        background: #FFFFFF;
        border-bottom: 1px solid #EDEFF2;
        box-shadow: 0 1px 3px rgba(15,23,42,.04);
        padding: .625rem 1rem;
    }
    /* 顶栏内分栏的水平间距 + 行高锁定：
       gap 收窄（Streamlit 各列自带 1rem，比客诉系统散）；
       height 锁 36px 是占位值能「由构造确定」的前提——行高不锁，
       最高那件（铃铛按钮默认 ~38px）决定行高，顶栏总高就随组件默认值漂移，
       下方占位只能靠估。锁死后顶栏总高恒为 10+36+10+1 = 57px。 */
    [class*="st-key-kb_app_header"] [data-testid="stHorizontalBlock"] {
        gap: .75rem; align-items: center !important; height: 36px;
    }
    /* 铃铛按钮压缩到与搜索框同高（~32px < 行高 36px），不再把行撑高 */
    [class*="st-key-kb_app_header"] button {
        padding: 6px 10px !important; min-height: 0 !important; font-size: 14px !important;
    }
    [class*="st-key-kb_app_header"] button p { margin: 0 !important; line-height: 1.2; }
    /* ---- 顶栏内所有件锚到同一条垂直中线（用户反馈「铃铛与用户没对齐」）----
       只靠 st.columns(vertical_alignment="center") 不够：它对齐的是**每一列的列盒**，
       而列盒高度由各自内容决定；只要某列的 widget 包装层比内容高（例如 markdown
       包装层继承 line-height 1.6、或按钮自带上下内边距），该列内容就会在列盒里
       偏上/偏下，于是铃铛与用户文字看起来不在一条线上。
       改法：列盒拉满高度 → 列内纵向块 flex 居中，于是每个件由浏览器统一算中心，
       与该件自身盒高无关。
       ⚠️ 选择器说明：Streamlit 1.61 里列**没有**自己的 testid
       （[data-testid="stVerticalBlock"] 才是），行容器 testid 是 stHorizontalBlock，
       列是它的直接子 div——故列用 > div 结构选择器，并整体限定在顶栏作用域内。
       ⚠️ 这里只居中、**不**拉伸子元素（不用 flex:1）：搜索框要保住自身 ~32px 的矮样式，
       拉伸会让它变高。 */
    [class*="st-key-kb_app_header"] [data-testid="stHorizontalBlock"] > div { align-self: stretch; }
    [class*="st-key-kb_app_header"] [data-testid="stHorizontalBlock"] > div > [data-testid="stVerticalBlock"] {
        height: 100%; display: flex; flex-direction: column;
        align-items: stretch; justify-content: center;
    }
    [class*="st-key-kb_app_header"] [data-testid="stElementContainer"] { width: 100%; }
    /* 注意：不要试图用 [class*="st-key-kb_bell"] 给铃铛挂钩——Streamlit 只为
       **容器**生成 st-key-* 类，st.button(key=...) 不会生成，那样写是死选择器。
       铃铛的居中由上面「列内纵向块 flex 居中」统一负责，无需单独规则。 */
    [class*="st-key-kb_app_header"] [data-testid="stMarkdownContainer"] { margin: 0 !important; }
    /* 用户块的行高来源要掐死：正文继承 line-height 1.6，会在 .kb-who 文字上下各留空，
       而头像（inline-flex，28px）按自身盒高参与居中，两者中心就会差一点。
       处理：把「包含 .kb-who 的那个 markdown 容器」设为 line-height:1 + 相对定位，
       内部 div 绝对定位到 50% 再平移——彻底不依赖行盒高度怎么算。
       用 :has() 精确命中含用户名的容器，而不是 :last-child：
       铃铛列为空时 Streamlit 可能不渲染该列，位置选择器会失准。 */
    [class*="st-key-kb_app_header"] [data-testid="stMarkdownContainer"]:has(.kb-who) {
        line-height: 1; position: relative;
    }
    [class*="st-key-kb_app_header"] [data-testid="stMarkdownContainer"]:has(.kb-who) > div {
        position: absolute; top: 50%; transform: translateY(-50%);
        left: 0; right: 0;
    }
    /* 搜索框：客诉系统为偏矮的 12.5px 输入框；Streamlit 默认更高更圆，收一下 */
    [class*="st-key-kb_app_header"] input {
        font-size: 12.5px; min-height: 0; padding: 6px 10px;
    }
    /* 用户胶囊左侧竖分隔线（客诉系统 .header .who 的 border-left） */
    .kb-who { border-left: 1px solid #E5E7EB; padding-left: 14px; }
    /* 顶栏与首个内容块的间距不再单独加 margin：占位块（第 2 节 padding-top: 61px）
       已含 4px 呼吸空隙，再叠 Streamlit 默认元素间距会重新变松。 */
    [class*="st-key-kb_app_header"] + div { margin-top: 0; }
    /* 顶栏内 markdown 默认 1rem 段落外边距会把白条撑高、并让标题略微偏高 */
    [class*="st-key-kb_app_header"] [data-testid="stMarkdownContainer"] p { margin: 0 !important; }
    [class*="st-key-kb_app_header"] [data-testid="stMarkdownContainer"] > *:first-child { margin-top: 0; }
    [class*="st-key-kb_app_header"] [data-testid="stMarkdownContainer"] > *:last-child { margin-bottom: 0; }

    /* ===== 7) help 浮窗（? 图标悬浮提示）完整显示 =====
       症状①：hover 问号浮窗「显示不全」——浮窗内容盒 stTooltipContent 自带
       max-height: 18.75rem(300px) + overflow:auto（包内自证 Tooltip.DJg7muty.js），
       文案超 300px 变「内滚小窗」，下半截看不见。
       症状②：放开上限后又出现「框没有框住文字」——内容盒 max-width 只有
       calc(736px - 2*2.3rem)≈662px，而 help 文案里的长英文串
       （如 mean(closed_at − created_at)）是不可折行 token：原来被 overflow 裁掉，
       改 visible 后直接戳出框外。
       处理：max-height 放宽到 min(80vh,48rem)（正常 help 文案远够）；
       overflow-wrap:anywhere 让长 token 在框内折行——框永远框得住文字；
       溢出保持 visible（不复活内滚小窗）。
       安全性：浮窗外壳 position:fixed + z-index 1000060（theme popup 值），
       远高于本系统 fixed 顶栏的 1000；视口边缘翻转/平移由 floating-ui 的
       flip/shift 中间件负责（包内 vr() 默认开启）。错误态浮窗同体系一并处理。 */
    [data-testid="stTooltipContent"],
    [data-testid="stTooltipErrorContent"] {
        max-height: min(80vh, 48rem) !important;
        overflow: visible !important;
        overflow-wrap: anywhere !important;
    }
    </style>
    """, unsafe_allow_html=True)


def _resolve_nav(pages: dict) -> str:
    """解析当前页（t31 导航迁移，取代 nav_radio 三件套）。

    状态机：① 消费 _pending_nav/_pending_nav_target 跳转标记（跳转机制原样保留）；
    ② 当前页不在可选页（如角色不可见/换人登录残留）时回落到第一页。
    返回页键（= 导航项中文名，与页面显示名一致，供埋点与分发共用）。
    """
    keys = list(pages.keys())
    if st.session_state.pop("_pending_nav", None):
        target = st.session_state.pop("_pending_nav_target", keys[0])
        if target in keys:
            st.session_state[NAV_STATE_KEY] = target
    if st.session_state.get(NAV_STATE_KEY) not in keys:
        st.session_state[NAV_STATE_KEY] = keys[0]
    return st.session_state[NAV_STATE_KEY]


def _do_logout():
    """退出登录（t31：从侧边栏内联代码提取为函数，供顶部用户胶囊复用，清理逻辑逐字保留）。

    刷新不掉线的配套清理（本函数其余逻辑不动）：服务端令牌立即作废，
    并标记清除浏览器 Cookie——只清 session 不清 Cookie 的话，刷新又会被还原回来。
    """
    try:
        auth.revoke_token(st.context.cookies.get(_AUTH_COOKIE))
    except Exception:
        pass  # 读不到 Cookie（极端环境）也不阻塞登出
    st.session_state["_clear_auth_cookie"] = True
    st.session_state.pop("logged_in", None)
    st.session_state.pop("current_user_id", None)
    # 评价体系的本会话标记要一并清掉：否则同一浏览器换人登录时，
    # 下一个人会继承「本会话已检查/已弹过问卷」的状态而收不到问卷
    for k in ("_survey_checked", "_survey_pending", "_survey_rec", "_rating_req"):
        st.session_state.pop(k, None)
    # 改进闭环操作台与用户活跃埋点的会话状态同款处理（C10：新增 session 键必须纳入
    # 登出清理，否则换人登录会串状态——改进闭环控件键以 imp_/_imp 前缀命名；
    # _activity_last_page 若不清理，换人登录后首个页面切换会被误判为「未变化」而漏记；
    # t34 F-04：_global_search 前缀覆盖 Header 全局搜索的 _global_search_q/_global_search_last 两键，
    # 残留键虽经审查实测无可见后果，仍纳入清扫保持清单完整）
    for k in [k for k in list(st.session_state)
              if k.startswith("imp_") or k.startswith("_imp") or k.startswith("_activity")
              or k.startswith("_global_search")]:
        st.session_state.pop(k, None)
    # t31 导航迁移：清理旧 radio 键与当前页键，换人登录从第一页重新开始
    st.session_state.pop("nav_radio", None)
    st.session_state.pop(NAV_STATE_KEY, None)
    # A5C 对话搜索：聊天历史含上一个用户的提问与检索结果，换人登录必须清掉
    st.session_state.pop("chat_msgs", None)
    st.rerun()


@contextmanager
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


def _render_app_header(page_title: str, todo_count: int):
    """顶部 Header（t31）：当前页标题 + 全局搜索入口 + 通知铃铛 + 用户胶囊。

    - 搜索为「入口+联动」语义：输入后回车跳转 AI 搜索页，查询词经 _search_prefill
      预填到目标页输入框并自动执行一次 AI 解析（page_search 消费后 pop，不残留）；
    - 铃铛角标接现有待办总数（_pending_notices_all 七元组之和），点击前往「我的看板」；
    - 退出登录入口在侧边栏底部（参考客诉系统 AppSidebar），顶栏不再放退出。
    """
    # 整段包进 keyed 容器 kb_app_header，供全局 CSS 挂钩成白底顶栏。
    # 版式：标题 | 搜索 | 弹性占位 | 铃铛 | 用户胶囊。弹性占位列把右侧三件推成一组，
    # 避免「搜索框」和「铃铛」之间出现一段说不清来由的大空档（原来的列比就是这个效果）。
    with st.container(key="kb_app_header"):
        # 纵向对齐统一交给 vertical_alignment="center"；水平间距由 CSS 收窄（见第 6 节）。
        t_col, s_col, _fill, b_col, u_col = st.columns(
            [3.2, 2.6, 1.2, 0.7, 2.0], vertical_alignment="center", gap="small"
        )
        with t_col:
            # line-height 必须显式定：正文继承 1.6 会把 15.5px 标题的行盒撑到 ~24.8px，
            # 该行盒按列居中时文字重心比搜索框/铃铛高 8px（真实浏览器实测：
            # 标题中心 148 vs 搜索/铃铛 140）。收到 1.2 后行盒≈18.6px，三者中心齐平。
            st.markdown(
                f'<div style="font-size:15.5px;font-weight:600;color:#1F2329;'
                f'line-height:1.2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
                f'{page_title}</div>',
                unsafe_allow_html=True,
            )
        with s_col:
            # 提交后清空输入框（bug1 修复）。Streamlit 禁止在 widget 实例化之后改写其
            # session_state 键，所以不能在下面直接置空 _global_search_q——必须用
            # 「本轮立旗标 → rerun → 下一轮创建 widget 之前清空」的标准配方。
            # 旗标键走 _global_search 前缀，登出时的前缀清扫自动覆盖。
            if st.session_state.pop("_global_search_clear", False):
                st.session_state["_global_search_q"] = ""
            q = st.text_input("全局搜索", placeholder="🔍 搜索需求 / 编号 / 关键词",
                              label_visibility="collapsed", key="_global_search_q")
            # 仅在输入值「发生变化」时跳转一次（回车/提交触发重跑）：避免输入残留导致后续
            # 切到别的页又被弹回搜索页；查询词经 _search_prefill 带给搜索页成为对话第一句。
            if q != st.session_state.get("_global_search_last", ""):
                if q.strip():
                    st.session_state[NAV_STATE_KEY] = "🔍 AI 搜索"
                    st.session_state["_search_prefill"] = q
                    st.session_state["_global_search_last"] = ""   # 基准同步为清空后的状态
                    st.session_state["_global_search_clear"] = True  # 下一轮开头清空输入框
                    st.rerun()
                else:
                    st.session_state["_global_search_last"] = q
        with b_col:
            if st.button(f"🔔{'+' + str(todo_count) if todo_count else ''}", key="kb_bell",
                         use_container_width=True,
                         help=f"您有 {todo_count} 项待办，点击前往「我的看板」处理"):
                st.session_state[NAV_STATE_KEY] = "📋 我的看板"
                st.rerun()
        with u_col:
            cur = get_current_user()
            if cur:
                initial = (cur.get("name", "?") or "?")[:1]
                # 末尾放一个零宽字符：让用户块的 markdown 包装层高度等于整行行盒，
                # 否则包装层只包住头像行，flex 居中时会把整块往上带偏。
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:8px;'
                    f'white-space:nowrap;overflow:hidden;">'
                    f'<span class="kb-avatar">{initial}</span>'
                    f'<span class="kb-who">{cur["name"]} · {cur["role"]}</span></div>'
                    f'&#8203;',
                    unsafe_allow_html=True,
                )


def _nav_tabs(tabs: list[tuple[str, str]], state_key: str) -> str:
    """横向标签菜单（版式参考客诉系统 SystemEval 的 Tab 切换条）。

    tabs：[(键, 显示文案), ...]；state_key：记住当前项的 session 键。
    返回当前选中的键；点击其它项时写状态并 st.rerun（与本文件既有跳转机制一致）。

    为什么用一组 st.button 而不是 st.tabs：
    ① st.tabs 是「同时渲染全部面板」的语义，本文件多处是「只渲染当前视图」，
       用它会白白执行另外两个分支（含各自的数据查询），既慢又可能触发副作用；
    ② 参考系统的样式是「当前项底部一道短横线」，靠 kind 区分 primary/secondary 即可
       精确命中，不需要给 st.tabs 的内部 DOM 打补丁（那种做法随版本升级极易失效）。
    键名统一前缀 kb_tab_，CSS 据此挂钩（与 kb_nav / kb_card_ 同一套路）。
    """
    if not tabs:
        return ""
    keys = [k for k, _ in tabs]
    if st.session_state.get(state_key) not in keys:
        st.session_state[state_key] = keys[0]
    with st.container(key=f"kb_tabs_{state_key}"):
        cols = st.columns(len(tabs), gap="small")
        for col, (key, label) in zip(cols, tabs):
            with col:
                if st.button(
                    label,
                    key=f"kb_tab_{state_key}_{key}",
                    use_container_width=True,
                    type="primary" if key == st.session_state[state_key] else "secondary",
                ):
                    if key != st.session_state[state_key]:
                        st.session_state[state_key] = key
                        st.rerun()
    return st.session_state[state_key]


def render_sidebar(pages: dict, current_page: str, my_todo: int = 0):
    """三段式布局之侧边栏（t31）：品牌区 → 分组导航 → 底部问卷入口 + LLM 配置。

    pages：角色过滤后的页字典（不可见页已由 main() pop）；
    current_page：当前页键（active 高亮）；my_todo：待办总数（「我的看板」角标）。
    导航改为按钮 + session_state（NAV_STATE_KEY），取代旧 nav_radio。
    """
    with st.sidebar:
        # ---- 1) 品牌区（副标题按要求去掉，只留主标题）----
        st.markdown(
            f'<div class="kb-brand"><div class="t">🛠️ {APP_TITLE}</div></div>',
            unsafe_allow_html=True,
        )

        # ---- 2) 分组导航（同一页面值不触发重跑；跳转由 _resolve_nav 统一消费）。
        # t34 F-03：整段包进 keyed 容器 kb_nav，导航态 CSS 只作用于该作用域，不误伤问卷 CTA ----
        with st.container(key="kb_nav"):
            # 分组标题（需求 / 管理与评估）按要求去掉，NAV_GROUPS 仍决定条目顺序
            for _group, items in NAV_GROUPS:
                for label in items:
                    if label not in pages:
                        continue  # 角色不可见（如系统评估）——过滤逻辑在 main() 完成
                    shown = label
                    if label == "📋 我的看板" and my_todo:
                        # 草图红点角标在 st.button 内无法内嵌 HTML，一期以「· N」文本计数近似
                        shown = f"{label} · {my_todo}"
                    if st.button(shown, key=f"nav_btn_{label}", use_container_width=True,
                                 type="primary" if label == current_page else "secondary"):
                        if label != current_page:
                            st.session_state[NAV_STATE_KEY] = label
                            st.rerun()

        # ---- 3) 分隔线（原「系统问卷入口」已按要求删除；问卷机制本身保留：
        #      自动弹窗仍由 main() 的 _maybe_show_survey() 按冷却期触发，
        #      数据与统计（系统评估页 D2 区块）不受影响） ----
        st.markdown("---")

        # ---- 4) LLM 配置收进折叠 expander（session_state 键名与读取行为不变）----
        with st.expander("⚙️ LLM 配置", expanded=False):
            provider_options = {k: v["name"] for k, v in PROVIDERS.items()}
            st.selectbox(
                "服务商",
                options=list(provider_options.keys()),
                format_func=lambda x: provider_options[x],
                key="provider",
            )

            provider_cfg = PROVIDERS[st.session_state.provider]
            api_key_env = provider_cfg["api_key_env"]
            env_value = os.environ.get(api_key_env, "")

            st.text_input(
                f"API Key（环境变量 {api_key_env}）",
                value=st.session_state.api_key or env_value,
                type="password",
                placeholder=f"前往 {provider_cfg['website']} 申请",
                key="api_key",
            )

            st.text_input(
                "模型（留空用默认）",
                value=st.session_state.model,
                placeholder=f"默认：{provider_cfg['default_model']}",
                key="model",
            )

            model_used = st.session_state.model or provider_cfg["default_model"]
            st.caption(f"当前模型：`{model_used}`")

            connected = bool(st.session_state.api_key)
            if connected:
                st.success("✅ LLM 已就绪")
            else:
                st.warning("⚠️ 未配置 API Key，AI 调用将失败但可演示流程")

        # ---- 5) 系统信息（按用户要求收纳）：数据统计 + 问卷冷却提示
        # 这两行小字原先平铺在侧边栏底部，与导航/问卷按钮/LLM 挤在一起，视觉嘈杂。
        # 收进一个默认折叠的「ℹ️ 系统信息」区块后，底部只剩：导航 → 问卷按钮 → LLM 配置 → 用户区。
        stats = _cached_db_stats()
        with st.expander("ℹ️ 系统信息", expanded=False):
            if stats["exists"]:
                st.caption(
                    f"已保存：需求 {stats['req_count']} 条 · 项目 {stats.get('proj_count', 0)} 个 "
                    f"· 附件 {stats['attach_count']} 份"
                )
            else:
                st.caption("数据库未初始化")

        # ---- 6) 底部用户区 + 退出登录（参考客诉系统 AppSidebar：用户卡片 + 退出入口在最底部）
        # 分隔线由容器 CSS 的 border-top 提供，不再用 st.markdown("---")——
        # 那个 <hr> 会被容器的 padding-top 下移，再被 sticky 的不透明背景切掉上半部分，
        # 结果就是一条残线压在用户名上（用户截图可见的那处重合）。
        me_sb = get_current_user()
        if me_sb:
            with st.container(key="kb_sidebar_footer"):
                st.markdown(
                    f'<div class="kb-sb-user">'
                    f'<span class="kb-avatar">{(me_sb.get("name", "?") or "?")[:1]}</span>'
                    f'<div class="kb-sb-user-meta">'
                    f'<div class="kb-sb-user-name">{me_sb["name"]}</div>'
                    f'<div class="kb-sb-user-role">{me_sb["role"]}</div>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )
                if st.button("🚪 退出登录", key="kb_logout_sb", use_container_width=True):
                    _do_logout()


# ============================================================
# 数据查询辅助
# ============================================================

# 旧「来源」分类 → 新「部门」分类 的一次性映射
_SOURCE_DEPT_MIGRATION = {
    "战略需求": "总经办",
    "订单需求": "销售",
    "打样需求": "售前",
    "售后需求": "客服",
    "技术攻关": "研发",
    "设计变更": "工程",
}


def _normalize_requirements(reqs: list[dict]) -> list[dict]:
    """把历史字段归一化到当前结构（原地修改，返回同一列表）。

    迁移规则：
    1. 历史「待分配」状态并入「需求池」；
    2. 旧 owner_id 单值 → owner_ids 列表（多执行人）；
    3. 旧「来源」分类映射到新「部门」分类；
    4. 多执行人需求自动拆分子需求（n 执行人 ↔ n 子需求，含历史数据补齐）。
    该逻辑原本在 get_requirements() 里每次调用都全量重跑（全文件调用 30+ 次），
    现在只在数据加载点统一做一次。
    """
    for r in reqs:
        if r.get("status") == "待分配":
            r["status"] = "需求池"
        if "owner_ids" not in r:
            r["owner_ids"] = [r["owner_id"]] if r.get("owner_id") else []
            r.pop("owner_id", None)
        old_source = _SOURCE_DEPT_MIGRATION.get(r.get("source", ""))
        if old_source:
            r["source"] = old_source
        # 多执行人：自动拆分子需求（含本功能上线前的历史数据；在内存生成，随该需求下次保存持久化）
        if len(r.get("owner_ids", [])) > 1:
            _sync_sub_requirements(r)
    return reqs


def get_requirements() -> list[dict]:
    return st.session_state.requirements


def _pending_confirmations(user_id: str) -> list[dict]:
    """当前用户作为「建议责任人」需要确认的需求（待确认 + 待责任人确认）"""
    return [
        r for r in get_requirements()
        if r.get("requested_owner_id") == user_id
        and r.get("status") == "待确认"
        and r.get("review_state", "待责任人确认") == "待责任人确认"
    ]


def _pending_feedback(user_id: str) -> list[dict]:
    """当前用户作为「提出人」需确认主管反馈/调整的需求（待确认 + 待提出人确认）"""
    return [
        r for r in get_requirements()
        if r.get("requester_id") == user_id
        and r.get("status") == "待确认"
        and r.get("review_state") == "待提出人确认"
    ]


def _pending_completions(user_id: str) -> list[dict]:
    """当前用户作为「提出人」或「建议负责人」需被告知的需求（已完成 + 未确认）"""
    return [
        r for r in get_requirements()
        if r.get("status") == "已完成"
        and (r.get("requester_id") == user_id or r.get("requested_owner_id") == user_id)
        and user_id not in r.get("completion_seen_by", [])
    ]


def _pending_pool_notices(user_id: str, role: str) -> list[dict]:
    """进入需求池的提醒。role='owner' 为建议负责人（需处理），role='requester' 为提出人（已被采纳）。"""
    key = "requested_owner_id" if role == "owner" else "requester_id"
    return [
        r for r in get_requirements()
        if r.get("status") == "需求池"
        and r.get(key) == user_id
        and user_id not in r.get("pool_seen_by", [])
    ]


def _pending_notices_all(user_id: str) -> tuple[list, list, list, list, list, list, list]:
    """一次性汇总当前用户的全部待办提醒，避免在 main() 里对全量需求扫多遍。

    返回顺序与 main() 中的展示顺序一致：
    (confirmations, feedback, rejects, pool_owner, pool_requester, assignments, completions)
    """
    pending, feedback, rejects = [], [], []
    pool_owner, pool_requester, assignments, done = [], [], [], []
    for r in get_requirements():
        status = r.get("status")
        requested_owner = r.get("requested_owner_id")
        requester = r.get("requester_id")

        if status == "待确认":
            if requested_owner == user_id and r.get("review_state", "待责任人确认") == "待责任人确认":
                pending.append(r)
            if requester == user_id and r.get("review_state") == "待提出人确认":
                feedback.append(r)
        elif status == "已终止":
            # 负责人驳回：告知提出人
            if requester == user_id and r.get("review_state") == "已驳回" and user_id not in r.get("reject_seen_by", []):
                rejects.append(r)
        elif status == "已完成":
            if (requester == user_id or requested_owner == user_id) and user_id not in r.get("completion_seen_by", []):
                done.append(r)
        elif status == "需求池":
            if requested_owner == user_id and user_id not in r.get("pool_seen_by", []):
                pool_owner.append(r)
            if requester == user_id and user_id not in r.get("pool_seen_by", []):
                pool_requester.append(r)
        if status in ("待启动", "进行中", "暂停"):
            # 新指派的执行任务：告知执行人
            if user_id in r.get("owner_ids", []) and user_id not in r.get("assign_seen_by", []):
                assignments.append(r)
    return pending, feedback, rejects, pool_owner, pool_requester, assignments, done


# ============================================================
# 评价体系 · 需求评价（一期）
# ============================================================

def _star_html(score: int, size: int = 15) -> str:
    """5 星制评分展示（实心 ★ + 空心 ☆）"""
    n = max(0, min(5, int(score or 0)))
    return (f'<span style="color:#FB8C00;font-size:{size}px;letter-spacing:1px;">'
            f'{"★" * n}{"☆" * (5 - n)}</span>')


def _ratings(req: dict) -> dict:
    """取一条需求的全部评价，key 为评价人 user_id，值为 {score, comment, at}。

    兼容历史单值格式：早期版本一条需求只有一个 `satisfaction` 整数 + `satisfaction_by`，
    这里读时统一归一化成 {user_id: {...}}；写时（_set_rating）一律写新格式。
    """
    s = req.get("satisfaction")
    if isinstance(s, dict):
        return s
    if s is None:
        return {}
    by = req.get("satisfaction_by")
    if not by:
        return {}
    return {by: {
        "score": int(s),
        "comment": req.get("satisfaction_comment", ""),
        "at": req.get("satisfaction_at", ""),
    }}


def _set_rating(req: dict, user_id: str, score: int, comment: str) -> None:
    """写入/覆盖某人的评价，并清掉历史单值字段，避免两种格式并存。"""
    ratings = dict(_ratings(req))
    ratings[user_id] = {
        "score": int(score),
        "comment": (comment or "").strip(),
        "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    req["satisfaction"] = ratings
    for legacy in ("satisfaction_by", "satisfaction_comment", "satisfaction_at"):
        req.pop(legacy, None)


def _req_raters(req: dict) -> list[str]:
    """本条需求需要评价的关联人：提出人 + 建议责任人 + 全体执行人（去重、保序）。

    与「已完成」通知的接收范围保持一致，避免出现「收到完成通知却没资格评价」的人。
    """
    ids = [req.get("requester_id"), req.get("requested_owner_id")]
    ids += list(req.get("owner_ids") or [])
    return list(dict.fromkeys(i for i in ids if i))


def _rater_role(req: dict, user_id: str) -> str:
    """某人在本条需求里的身份（用于按角色分组统计）。

    一人可能身兼多职（如既是建议责任人又在执行人里），按此优先级只归一类。
    """
    if user_id == req.get("requester_id"):
        return "提出人"
    if user_id == req.get("requested_owner_id"):
        return "建议责任人"
    if user_id in (req.get("owner_ids") or []):
        return "执行人"
    return "其他"


def _can_view_rating_detail() -> bool:
    """能否查看「逐人评价明细」（谁给这条需求打了几分）。

    口径：只有管理层。主管可以进「系统评估」页看汇总数字（用于推动组员填问卷），
    但看不到逐人的星级与评论——那是对同事的点名式数据。
    """
    return (get_current_user() or {}).get("role") == "管理层"


def satisfaction_stats(reqs: list[dict] | None = None) -> dict:
    """需求评价统计（按**评价条目**计，一条需求有 N 个关联人就可能产生 N 条评价）。

    口径：只统计「已完成」需求上的评价——在跑的需求不该被评价，「已终止」的也不算交付质量。
    同时按角色分组，保留「提出人视角 vs 执行人视角」的差异（这个差异本身有洞察价值）。
    """
    reqs = get_requirements() if reqs is None else reqs
    done = [r for r in reqs if r.get("status") == "已完成"]
    scores, by_role = [], {}
    rated_reqs = expected = 0
    for r in done:
        expected += len(_req_raters(r))
        ratings = _ratings(r)
        if ratings:
            rated_reqs += 1
        for uid, rec in ratings.items():
            sc = int(rec.get("score") or 0)
            if sc <= 0:
                continue
            scores.append(sc)
            by_role.setdefault(_rater_role(r, uid), []).append(sc)
    dist = Counter(scores)
    positive = dist.get(5, 0) + dist.get(4, 0)
    return {
        "done": len(done), "rated_reqs": rated_reqs, "unrated_reqs": len(done) - rated_reqs,
        "rated": len(scores), "expected": expected,
        "missing": max(expected - len(scores), 0),
        "avg": (sum(scores) / len(scores)) if scores else 0.0,
        "dist": dist,
        "positive_rate": (positive / len(scores) * 100) if scores else 0.0,
        "by_role": by_role,
    }


def all_rating_records() -> list[dict]:
    """展开全部评价记录（一条需求 × 一个评价人 = 一条），供管理层明细与导出使用。"""
    rows = []
    for r in get_requirements():
        if r.get("status") != "已完成":
            continue
        for uid, rec in _ratings(r).items():
            rows.append({
                "req_id": r["id"], "title": r.get("title", ""),
                "user_id": uid, "name": USER_ID_TO_NAME.get(uid, uid),
                "role": _rater_role(r, uid),
                "score": int(rec.get("score") or 0),
                "comment": rec.get("comment", ""), "at": rec.get("at", ""),
            })
    rows.sort(key=lambda x: (x["at"], x["req_id"]), reverse=True)
    return rows


def _pending_ratings(user_id: str) -> list[dict]:
    """当前用户作为关联人、需求已完成但本人尚未评价的条目。

    与 completion_seen_by（「知道了」已读标记）解耦：点过「知道了」之后依然可以评价。
    """
    out = []
    for r in get_requirements():
        if r.get("status") != "已完成":
            continue
        if user_id not in _req_raters(r):
            continue
        if user_id in _ratings(r):
            continue
        out.append(r)
    return out


def _render_rating_notice(user_id: str) -> None:
    """主区提醒条 + 评价入口（仅提出人本人可见）。"""
    pending = _pending_ratings(user_id)
    if not pending:
        return
    n = len(pending)
    titles = "、".join(f"「{r['title']}」" for r in pending[:3])
    more = " 等" if n > 3 else ""
    st.info(f"⭐ 您有 {n} 条已完成需求待评价：{titles}{more}。"
            f"您是本需求的关联人，评价用于衡量协作与交付质量。")
    options = {f"{r['id']} · {r['title']}": r["id"] for r in pending}
    opts = list(options.keys())
    # 评价完一条后待选列表会变，残留的选中值不在新选项中会导致组件报错，这里先清掉
    if st.session_state.get("rate_pick") not in opts:
        st.session_state.pop("rate_pick", None)
    c1, c2 = st.columns([4, 1])
    with c1:
        pick = st.selectbox("选择要评价的需求", options=opts, key="rate_pick")
    with c2:
        st.markdown('<div style="height:28px;"></div>', unsafe_allow_html=True)
        if st.button("⭐ 去评价", type="primary", use_container_width=True, key="rate_go"):
            st.session_state["_rating_req"] = options[pick]
            st.rerun()


def _render_improvement_notice(user_id: str) -> None:
    """改进建议进展回告提醒条（仅提出人本人可见，03 蓝图 §5.5 + 队长 t11 硬要求）。

    与 _render_rating_notice 同构的独立渲染路径（不并入 _pending_notices_all 的 7 元组，C9）。
    站内可见性只由「需回告终态 + 未确认(ack)」决定——即使钉钉/邮件未配置或外发失败，
    提出人登录后也一定在主区看到进展，闭环不会因外发通道缺失而断掉。
    """
    items = db.pending_improvement_notice(user_id)
    if not items:
        return
    for it in items[:3]:
        note = f"说明：{it['resolution_note']}" if it.get("resolution_note") else ""
        st.success(f"📢 您提出的改进建议有进展：「{it['summary']}」已{it['status']}。{note}")
        if st.button("✅ 知道了", key=f"imp_ack_{it['id']}"):
            ok, msg = db.ack_improvement(it["id"], user_id)
            if not ok:
                st.error(msg)
            st.rerun()
    if len(items) > 3:
        st.info(f"还有 {len(items) - 3} 条改进建议的进展待确认，见下方「我提交的改进建议」。")


def _render_my_improvements(user_id: str) -> None:
    """「我的反馈与进展」个人视图（03 蓝图 §3.3：每个用户仅本人）。

    与提醒条互补：提醒条只报终态，这里展示本人全部改进项的当前状态——
    让「提了有用」在处理过程中就可见，而不必等到终态。
    """
    mine = db.load_improvements(proposer_id=user_id, with_proposer=False)
    if not mine:
        return
    with st.expander(f"🧭 我提交的改进建议（{len(mine)} 条）"):
        for it in mine:
            flags = []
            if it["notified_at"]:
                flags.append(f"已回告 {it['notified_at']}")
            if it["proposer_ack_at"]:
                flags.append("您已确认")
            st.markdown(
                f'<div style="border-left:3px solid #00897B;padding:4px 10px;margin-bottom:6px;">'
                f'<div>「{it["summary"]}」 · <b>{it["status"]}</b>'
                + (f' —— {it["resolution_note"]}' if it["resolution_note"] else '')
                + '</div>'
                f'<div style="font-size:12px;color:#666;">提出于 {it["created_at"]}'
                + (f' · {" · ".join(flags)}' if flags else '')
                + '</div></div>',
                unsafe_allow_html=True,
            )


def _render_improvement_feed_public() -> None:
    """🌟 改进动态（全员公示，07 审查 F-05 裁决 A）：所有登录用户在主区可见的只读公示。

    03 蓝图 §5/§5.5 的「向全员展示已落地成果」落地于此（评估页内的同名区块仍仅管理层+主管）；
    只显示摘要与上线时间，不新增敏感信息（取数即不查提出人姓名，with_proposer=False）。
    """
    live = db.load_improvements(status="已上线", with_proposer=False)
    if not live:
        return
    with st.expander(f"🌟 改进动态（已上线 {len(live)} 条）"):
        for it in live[:10]:
            st.markdown(
                f'<div style="border-left:3px solid #43A047;padding:3px 10px;margin-bottom:4px;">'
                f'🎉 「{it["summary"]}」已上线'
                f'<span style="color:#666;font-size:12px;margin-left:8px;">'
                f'{it["closed_at"] or it["updated_at"] or ""}</span></div>',
                unsafe_allow_html=True,
            )


@st.dialog("⭐ 需求评价")
def _rating_dialog(req_id: str):
    """评价弹窗。保持「未提交就持续渲染」——Streamlit 每次交互重跑脚本，
    若在打开时就把 _rating_req 消费掉，用户在文本框里打字触发重跑会把弹窗刷掉。"""
    req = get_requirement(req_id)
    me = st.session_state.current_user_id
    if not req:
        st.session_state.pop("_rating_req", None)
        st.rerun()
    if me not in _req_raters(req):
        st.error("您不是本需求的关联人，无法评价。")
        if st.button("关闭", key=f"rate_close_{req_id}"):
            st.session_state.pop("_rating_req", None)
            st.rerun()
        return

    existing = _ratings(req).get(me, {})
    st.markdown(f"**{req['id']}** · {req['title']}")
    st.caption(f"您在本需求中的身份：**{_rater_role(req, me)}**。"
               f"请对协作过程与交付结果做评价，用于衡量需求全流程质量。")

    # 进度条：只报人数，不泄露任何人的具体分数（可见性口径 = 仅管理层看全部）
    raters = _req_raters(req)
    done_n = len(_ratings(req))
    st.caption(f"本需求需 {len(raters)} 人评价，目前已完成 {done_n} 人"
               + ("，其中包含您" if me in _ratings(req) else ""))

    st.markdown("**满意度**")
    star_key = f"rate_stars_{req_id}"
    # 已评过的人重新打开时，星级预填上次的选择（重选即覆盖）
    if existing.get("score") and star_key not in st.session_state:
        st.session_state[star_key] = int(existing["score"]) - 1
    idx = st.feedback("stars", key=star_key)
    score = (idx + 1) if idx is not None else 0
    if score:
        note = "（覆盖原评价）" if existing.get("score") and int(existing["score"]) != score else ""
        st.caption(f"已选择 {score} 星 —— {SATISFACTION_LABELS.get(score, '')}{note}")
    elif existing.get("score"):
        st.caption(f"当前评价：{_star_html(existing['score'])} {existing['score']}/5")

    comment = st.text_area(
        "补充说明（可选）",
        value=existing.get("comment", ""),
        placeholder="例如：交付及时、实现与预期一致；或：功能与预期有偏差、沟通不畅……",
        max_chars=500,
        key=f"rate_comment_{req_id}",
    )

    c1, c2 = st.columns(2)
    with c1:
        if st.button("✅ 提交评价", type="primary", use_container_width=True, key=f"rate_submit_{req_id}"):
            if not score:
                st.error("请先选择星级")
            else:
                _set_rating(req, me, score, comment)
                save_requirement(req)
                # 不群发通知：一条需求有 N 个关联人，逐条评价广播会变成刷屏。
                # 未评价者的提醒由主区常驻提醒条承担（见 _render_rating_notice）。
                st.session_state.pop("_rating_req", None)
                st.session_state["_flash"] = {
                    "type": "success",
                    "text": f"✅ 感谢评价：{'⭐' * score}（{SATISFACTION_LABELS.get(score, '')}）",
                }
                st.rerun()
    with c2:
        if st.button("稍后再说", use_container_width=True, key=f"rate_cancel_{req_id}"):
            st.session_state.pop("_rating_req", None)
            st.rerun()


# ============================================================
# 评价体系 · 系统使用体验问卷（二期）
# ============================================================

def _survey_due(user_id: str) -> bool:
    """是否已过冷却期（从未填过 → 可弹）。"""
    last = db.last_survey_at(user_id)
    if not last:
        return True
    try:
        last_dt = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    return datetime.now() - last_dt >= timedelta(days=SURVEY_COOLDOWN_DAYS)


def _maybe_show_survey() -> None:
    """登录后按冷却期自动弹出问卷。

    先落一条 dismissed 记录再弹——Streamlit 捕获不到弹窗被 X 关闭的动作，
    把「已触达」前置到打开时刻，用户点 X 后冷却期依然生效（口径见 db.new_survey_dismissed）。
    """
    if not st.session_state.get("_survey_pending"):
        if st.session_state.get("_survey_checked"):
            return
        st.session_state["_survey_checked"] = True
        me = get_current_user()
        if not me or not _survey_due(me["id"]):
            return
        st.session_state["_survey_rec"] = db.new_survey_dismissed(me)
        st.session_state["_survey_pending"] = True
    rec_id = st.session_state.get("_survey_rec")
    if rec_id:
        _survey_dialog(rec_id)


@st.dialog("📋 系统使用体验调查", width="large")
def _survey_dialog(rec_id: str):
    st.caption(
        f"占用您约 1 分钟。结果仅用于改进系统，{SURVEY_COOLDOWN_DAYS} 天内不会再打扰您。"
    )

    with st.form("survey_form", clear_on_submit=False):
        st.markdown("**请为以下说法选择同意程度（1=很不同意，5=非常同意）**")
        answers = {}
        for i, q in enumerate(SURVEY_QUESTIONS):
            answers[q] = st.radio(
                q,
                options=[1, 2, 3, 4, 5],
                index=2,
                horizontal=True,
                format_func=lambda x: f"{x} · {SURVEY_SCORE_LABELS.get(x, '')}",
                key=f"sv_q{i}",
            )
        features = st.multiselect(
            "您经常使用的功能（可多选，不计分）",
            options=SURVEY_FEATURES,
            key="sv_features",
        )
        comment = st.text_area(
            "改进建议（可选）",
            placeholder="哪里用起来别扭、希望增加什么……",
            max_chars=500,
            key="sv_comment",
        )
        c1, c2 = st.columns(2)
        with c1:
            submitted = st.form_submit_button("✅ 提交", type="primary", use_container_width=True)
        with c2:
            dismissed = st.form_submit_button("稍后再说", use_container_width=True)

    if submitted:
        score = sum(answers.values()) / len(answers) if answers else 0.0
        db.submit_survey(rec_id, round(score, 2), answers, features, comment,
                         question_set=SURVEY_QUESTION_SET)
        # 改进建议自动进入闭环（03 蓝图 §5.1）：comment 非空即自动建项；
        # 建项失败只打日志、绝不阻断问卷提交（吸收客诉「埋点失败不抛异常」纪律）。
        if (comment or "").strip():
            try:
                _u = get_current_user()
                if _u:
                    db.create_improvement(
                        source_type="survey", source_id=rec_id,
                        proposer_id=_u["id"], proposer_name=_u.get("name", ""),
                        detail=(comment or "").strip(),
                    )
                else:
                    print("[eval] 问卷建议未自动建改进项：拿不到当前用户")
            except Exception as e:
                print(f"[eval] 问卷建议自动建改进项失败（不影响提交）：{e}")
        st.session_state["_survey_pending"] = False
        st.session_state["_flash"] = {"type": "success", "text": "✅ 感谢反馈，已记录"}
        st.rerun()
    if dismissed:
        # 记录已在弹窗打开时落为 dismissed，这里只需关闭
        st.session_state["_survey_pending"] = False
        st.rerun()


def _survey_stats() -> dict:
    """问卷统计。口径：提交数/均分只用 submitted 记录；dismissed 只用于算触达。

    三期新增键（03 蓝图 §3.2/§4，既有键全部保留）：
    - per_question：逐题均分，按 question_set 版本分组（旧行 NULL 视作 v1），禁止跨版本混算；
    - kui_dist：KUI 自建 4 档计数（内部基线，仅纵向自比）；
    - min_sample / low_sample：小样本预警（阈值 5）。
    逐题聚合在 Python 层完成（不写 SQL JSON 查询，规避 PostgreSQL 版本差异，零新依赖）。
    """
    rows = db.load_survey_responses()
    submitted = [r for r in rows if r.get("response_type") == "submitted"]
    scores = [r["score"] for r in submitted if r.get("score") is not None]
    dist = Counter(int(round(s)) for s in scores)
    by_role = {}
    for r in submitted:
        by_role.setdefault(r.get("role") or "未知", []).append(r["score"] or 0)
    feat = Counter()
    for r in submitted:
        for f in (r.get("features") or []):
            feat[f] += 1
    # 逐题均分：按题干版本分组聚合（题干=answers 的键，题干冻结纪律见 SURVEY_QUESTION_SET）
    per_q_raw: dict[str, dict[str, list[float]]] = {}
    for r in submitted:
        answers = r.get("answers")
        if not isinstance(answers, dict):
            continue
        ver = r.get("question_set") or "v1"
        agg = per_q_raw.setdefault(ver, {})
        for q, v in answers.items():
            try:
                agg.setdefault(q, []).append(float(v))
            except (TypeError, ValueError):
                continue
    per_question = {}
    for ver, agg in per_q_raw.items():
        # 按 SURVEY_QUESTIONS 的既定顺序输出，未登记的题干排最后（未来新版本题干）
        ordered = sorted(agg.items(), key=lambda kv: (
            SURVEY_QUESTIONS.index(kv[0]) if kv[0] in SURVEY_QUESTIONS else len(SURVEY_QUESTIONS), kv[0]))
        per_question[ver] = [(q, sum(v) / len(v), len(v)) for q, v in ordered]
    # KUI 分档计数（区间口径见 KUI_BANDS 注释）
    kui_dist = [
        (name, sum(1 for s in scores
                   if (lo is None or s >= lo) and (hi is None or s < hi)), color)
        for name, lo, hi, color in KUI_BANDS
    ]
    return {
        "reached": len(rows),
        "submitted": len(submitted),
        "avg": (sum(scores) / len(scores)) if scores else 0.0,
        "dist": dist,
        "by_role": by_role,
        "features": feat,
        "comments": [r for r in submitted if r.get("comment")],
        "rows": rows,
        # ---- 三期新增（不改既有键，兼容约束 C8）----
        "per_question": per_question,
        "kui_dist": kui_dist,
        "min_sample": SURVEY_MIN_SAMPLE,
        "low_sample": len(submitted) < SURVEY_MIN_SAMPLE,
    }


def _render_eval_rating_section():
    """D1 需求评价维度（03 蓝图 §3.1）：沿用现有指标 + 低分追因清单 + 评价率催办。"""
    s = satisfaction_stats()
    if s["done"] == 0:
        st.info("暂无已完成需求")
    else:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("平均分", f"{s['avg']:.2f} / 5" if s["rated"] else "—")
        m2.metric("已评价 / 应评价", f"{s['rated']} / {s['expected']}")
        m3.metric("待评价", s["missing"])
        m4.metric("已完成需求", s["done"],
                  f"{s['unrated_reqs']} 条无人评价" if s["unrated_reqs"] else None)
        if s["rated"]:
            st.markdown(
                f'<div style="margin:6px 0;">满意率（4~5 星）'
                f'<b style="color:#43A047;"> {s["positive_rate"]:.0f}%</b></div>',
                unsafe_allow_html=True,
            )
            left, right = st.columns(2)
            with left:
                st.markdown("**星级分布**")
                _render_bar_chart(
                    [(f"{k} 星 {SATISFACTION_LABELS.get(k, '')}",
                      s["dist"].get(k, 0),
                      SATISFACTION_COLORS.get(k, "#9E9E9E")) for k in [5, 4, 3, 2, 1]],
                    total=max(s["rated"], 1),
                )
            with right:
                st.markdown("**按角色平均分**")
                role_rows = []
                for role in ("提出人", "建议责任人", "执行人"):
                    vals = s["by_role"].get(role)
                    if vals:
                        role_rows.append((f"{role}（{len(vals)} 条）", sum(vals) / len(vals)))
                if role_rows:
                    _render_bar_chart(
                        [(label, round(avg * 10), "#7E57C2") for label, avg in role_rows],
                        total=50,
                        show_value=lambda i: f"{role_rows[i][1]:.2f}",
                    )

        # 逐人明细（谁给几分）只对管理层开放；主管看得到上面的汇总与分布
        if _can_view_rating_detail():
            recs = all_rating_records()
            st.markdown("**全部评价明细**")
            for rec in recs:
                st.markdown(
                    f'<div style="border-left:3px solid #FB8C00;padding:4px 10px;margin-bottom:8px;">'
                    f'<div style="font-size:12px;color:#666;">'
                    f'{rec["name"]}（{rec["role"]}） · {rec["at"]} · {rec["req_id"]}</div>'
                    f'<div>{_star_html(rec["score"], 14)} <b>{rec["score"]}/5</b>'
                    f'<span style="margin-left:8px;color:#444;">{rec["title"]}</span></div>'
                    + (f'<div style="font-size:13px;color:#555;">　└ {rec["comment"]}</div>'
                       if rec["comment"] else "")
                    + '</div>',
                    unsafe_allow_html=True,
                )

            # 低分追因清单（03 蓝图 §3.1 新增）：≤2 星评价一键转改进项，把差评变成可跟踪的待办
            low = [r for r in recs if r["score"] <= 2]
            if low:
                with st.expander(f"🩺 低分追因清单（≤2 星，{len(low)} 条）"):
                    for rec in low:
                        st.markdown(
                            f'<div style="border-left:3px solid #E53935;padding:4px 10px;margin-bottom:6px;">'
                            f'<div style="font-size:12px;color:#666;">'
                            f'{rec["name"]}（{rec["role"]}） · {rec["at"]} · {rec["req_id"]}</div>'
                            f'<div>{_star_html(rec["score"], 13)} <b>{rec["score"]}/5</b>'
                            f'<span style="margin-left:8px;color:#444;">{rec["title"]}</span></div>'
                            + (f'<div style="font-size:13px;color:#555;">　└ {rec["comment"]}</div>'
                               if rec["comment"] else "")
                            + '</div>',
                            unsafe_allow_html=True,
                        )
                        # 转改进项：source 唯一索引防重，重复点击安全（03 蓝图 §5.1/M12）
                        if st.button("➕ 转改进项", key=f"low2imp_{rec['req_id']}_{rec['user_id']}"):
                            detail = (f"需求 {rec['req_id']}「{rec['title']}」获 {rec['score']} 星评价"
                                      f"（{rec['role']}）")
                            if rec["comment"]:
                                detail += f"：{rec['comment']}"
                            imp_id = db.create_improvement(
                                source_type="rating_comment",
                                source_id=f"{rec['req_id']}::{rec['user_id']}",
                                proposer_id=rec["user_id"], proposer_name=rec["name"],
                                detail=detail)
                            st.session_state["_flash"] = {
                                "type": "success",
                                "text": f"✅ 已转入改进闭环（{imp_id}），可在下方「🔁 改进闭环」推进与回告",
                            }
                            st.rerun()

            # 评价率催办（03 蓝图 §3.1/§6 新增，仅管理层）：对全部待评价关联人发提醒。
            # req=None 绕过 _notify 的 P0-P2 优先级门控（改进/催办类消息无优先级概念）。
            if s["missing"] > 0 and st.button("🛎 提醒未评价", key="eval_remind_rating",
                                              help="向全部已完成需求中尚未评价的关联人发送提醒"):
                ids = []
                for r in get_requirements():
                    if r.get("status") != "已完成":
                        continue
                    for uid in _req_raters(r):
                        if uid not in _ratings(r):
                            ids.append(uid)
                ids = list(dict.fromkeys(ids))
                if ids:
                    res = _notify(ids, f"您有 {s['missing']} 条已完成需求待评价，请登录需求看板完成 1-5 星评价。",
                                  req=None)
                    st.session_state["_flash"] = {
                        "type": "success" if res["delivered"] else "warning",
                        "text": (f"已提醒 {len(res['delivered'])} 人"
                                 + (f"，另有 {len(res['dead'])} 人无可达渠道（未配置联系方式），建议补档案"
                                    if res["dead"] else "")),
                    }
                else:
                    st.session_state["_flash"] = {"type": "info", "text": "当前没有待评价的关联人。"}
                st.rerun()

            with st.expander("📥 导出原始评价记录"):
                import pandas as pd
                st.download_button(
                    "下载 CSV",
                    data=pd.DataFrame([{
                        "需求编号": r["req_id"], "需求标题": r["title"],
                        "评价人": r["name"], "身份": r["role"],
                        "星级": r["score"], "补充说明": r["comment"], "评价时间": r["at"],
                    } for r in recs]).to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"需求评价明细_{TODAY_STR}.csv",
                    mime="text/csv",
                )
        else:
            st.caption("🔒 逐人评价明细仅管理层可见。" + (
                f" 本需求共 {s['rated_reqs']} 条已有评价。" if s["rated_reqs"] else ""))


def _render_eval_survey_section():
    """D2 系统使用体验问卷维度（03 蓝图 §3.2/§4）：沿用现有 + KUI 分档/逐题均分/小样本预警/口径统一。"""
    q = _survey_stats()
    if q["reached"] == 0:
        st.info("暂无问卷记录")
        return
    reach_rate = q["submitted"] / q["reached"] * 100 if q["reached"] else 0
    m1, m2, m3 = st.columns(3)
    m1.metric("触达", q["reached"])
    m2.metric("提交", q["submitted"], f"{reach_rate:.0f}% 提交率")
    m3.metric("KUI 均分", f"{q['avg']:.2f} / 5" if q["submitted"] else "—")
    # 提交率口径脚注（03 蓝图 §3.2-M3）：mvp「打开即写 dismissed」使触达完全可观测，提交率为精确口径
    st.caption("提交率 = 提交 / 触达（精确口径：问卷弹窗打开即记录触达，含打开后未填者）")

    # 管理层催填（F-02 / 03 蓝图 §5.5②/§6「数字→动作」）：对冷却期外的全体用户发邀请。
    # 逻辑镜像 D1 的评价催办；req=None 绕过 _notify 的 P0-P2 优先级门控（催办类消息无优先级概念）；
    # 面向 _survey_due=True（冷却期外）的用户，不写库、不新增依赖。
    if _can_view_rating_detail():
        if st.button("📢 提醒填写问卷", key="eval_remind_survey",
                     help="向冷却期已过（当前可填）的全部用户发送填写邀请"):
            ids = [u["id"] for u in get_users() if _survey_due(u["id"])]
            if ids:
                res = _notify(ids, "邀请填写系统使用体验问卷（约 1 分钟）：登录需求看板即可填写，"
                                   "您的改进建议会进入跟踪与回告闭环。", req=None)
                st.session_state["_flash"] = {
                    "type": "success" if res["delivered"] else "warning",
                    "text": (f"已邀请 {len(res['delivered'])} 人填写问卷"
                             + (f"，另有 {len(res['dead'])} 人无可达渠道（未配置联系方式），建议补档案"
                                if res["dead"] else "")),
                }
            else:
                st.session_state["_flash"] = {"type": "info", "text": "当前所有用户都在问卷冷却期内，无需催填。"}
            st.rerun()

    if not q["submitted"]:
        st.caption("尚无提交记录（触达记录不计入统计口径）")
        return

    left, right = st.columns(2)
    with left:
        st.markdown("**总分分布**")
        _render_bar_chart(
            [(f"{k:.0f} 分", q["dist"].get(k, 0), "#1E88E5") for k in [5, 4, 3, 2, 1]],
            total=max(q["submitted"], 1),
        )
    with right:
        st.markdown("**按角色平均分**")
        role_rows = sorted(
            ((role, sum(v) / len(v), len(v)) for role, v in q["by_role"].items()),
            key=lambda x: -x[1],
        )
        _render_bar_chart(
            [(f"{role}（{cnt} 人）", round(avg * 10), "#7E57C2") for role, avg, cnt in role_rows],
            total=50,
            show_value=lambda i, row=role_rows: f"{row[i][1]:.2f}",
        )

    # KUI 自建 4 档分布（03 蓝图 §4.2）+ 逐题均分（§4.3，定位「哪一题拖低总分」）
    left2, right2 = st.columns(2)
    with left2:
        st.markdown("**KUI 分档分布**")
        _render_bar_chart(
            [(name, cnt, color) for name, cnt, color in q["kui_dist"]],
            total=max(q["submitted"], 1),
        )
        st.caption(KUI_NOTE)
    with right2:
        st.markdown("**逐题均分**")
        per_q = q["per_question"].get(SURVEY_QUESTION_SET)
        if per_q is None and q["per_question"]:
            # 当前版本暂无数据时退而展示已有版本（未来改题干后的过渡期）
            per_q = next(iter(q["per_question"].values()))
        if per_q:
            _render_bar_chart(
                [(f"Q{i+1}", round(avg * 10), "#F4511E") for i, (_ques, avg, _cnt) in enumerate(per_q)],
                total=50,
                show_value=lambda i, rows=per_q: f"{rows[i][1]:.2f}",
            )
            st.caption("；".join(f"Q{i+1}＝{ques}" for i, (ques, _avg, _cnt) in enumerate(per_q)))
        else:
            st.caption("暂无逐题数据（历史记录未留存逐题答案）")

    # 小样本预警（03 蓝图 §3.2/M15：内部 12 人团队阈值取 5，而非客诉系统的 30）
    if q["low_sample"]:
        st.warning(f"⚠ 样本不足（提交 {q['submitted']} 份 < {q['min_sample']} 份），结论仅供参考。")

    if q["features"]:
        st.markdown("**常用功能排行**")
        top = q["features"].most_common()
        _render_bar_chart(
            [(name, cnt, "#00897B") for name, cnt in top],
            total=max(top[0][1], 1),
        )

    if q["comments"]:
        st.markdown("**改进建议**")
        if _can_view_rating_detail():
            # 点名式原文仅管理层（口径统一裁决，03 蓝图 §5.4/C5——与逐人评价明细同为点名式同事数据）
            for r in q["comments"]:
                st.markdown(
                    f'<div style="border-left:3px solid #1E88E5;padding:4px 10px;margin-bottom:8px;">'
                    f'<div style="font-size:12px;color:#666;">{r.get("username", "")}'
                    f'（{r.get("role", "")}） · {r.get("submitted_at") or r.get("created_at", "")}</div>'
                    f'<div>{r.get("comment", "")}</div></div>',
                    unsafe_allow_html=True,
                )
        else:
            # 主管：匿名化卡片（保留角色/部门与文本，不含姓名——满足推动组员填问卷的管理诉求）
            for r in q["comments"]:
                st.markdown(
                    f'<div style="border-left:3px solid #90A4AE;padding:4px 10px;margin-bottom:8px;">'
                    f'<div style="font-size:12px;color:#666;">（{r.get("role", "")} · {r.get("department", "")}）'
                    f' · {r.get("submitted_at") or r.get("created_at", "")}</div>'
                    f'<div>{r.get("comment", "")}</div></div>',
                    unsafe_allow_html=True,
                )
            st.caption("🔒 按统一口径，建议点名原文仅管理层可见。")

    # 逐题图旁「查看相关建议」（03 蓝图 §6：数字 → 动作）：关键词过滤，只读
    if q["comments"]:
        with st.expander("🔎 查看相关改进建议（按关键词过滤，只读）"):
            kw = st.text_input("关键词", key="imp_kw_lookup", placeholder="例如：AI、搜索、附件、导出……")
            hits = [c for c in q["comments"] if (not kw.strip()) or kw.strip() in (c.get("comment") or "")]
            if not hits:
                st.caption("没有匹配的建议。")
            for c in hits:
                who = c.get("username", "") if _can_view_rating_detail() else f'（{c.get("role", "")}）'
                st.markdown(
                    f'<div style="border-left:3px solid #B0BEC5;padding:4px 10px;margin-bottom:6px;">'
                    f'<div style="font-size:12px;color:#666;">{who} · '
                    f'{c.get("submitted_at") or c.get("created_at", "")}</div>'
                    f'<div>{c.get("comment", "")}</div></div>',
                    unsafe_allow_html=True,
                )

    st.markdown("---")
    # 导出按角色分流（F-01）：管理层导出点名原文；主管导出匿名版——与屏上卡片口径一致，
    # 否则 C5 的收紧会被同一页面的下载按钮绕过（记录ID 形如 SV-{user_id}-{时间}，同源泄漏 user_id）。
    if _can_view_rating_detail():
        with st.expander("📥 导出原始问卷记录"):
            export_rows = [{
                "记录ID": r["id"], "姓名": r["username"], "角色": r["role"], "部门": r["department"],
                "类型": "已提交" if r["response_type"] == "submitted" else "仅触达",
                "总分": r["score"], "题干版本": r.get("question_set") or "v1",
                "常用功能": "、".join(r.get("features") or []),
                "改进建议": r.get("comment", ""), "触达时间": r["created_at"],
                "提交时间": r["submitted_at"] or "",
            } for r in q["rows"]]
            import pandas as pd
            st.download_button(
                "下载 CSV",
                data=pd.DataFrame(export_rows).to_csv(index=False).encode("utf-8-sig"),
                file_name=f"系统使用体验问卷_{TODAY_STR}.csv",
                mime="text/csv",
            )
    else:
        # 主管匿名导出：无「姓名」键；「记录ID」内嵌 user_id，替换为不含 user_id 的纯序号
        with st.expander("📥 导出问卷记录（匿名，无姓名与用户ID）"):
            export_rows = [{
                "序号": i + 1, "角色": r["role"], "部门": r["department"],
                "类型": "已提交" if r["response_type"] == "submitted" else "仅触达",
                "总分": r["score"], "题干版本": r.get("question_set") or "v1",
                "常用功能": "、".join(r.get("features") or []),
                "改进建议": r.get("comment", ""), "触达时间": r["created_at"],
                "提交时间": r["submitted_at"] or "",
            } for i, r in enumerate(q["rows"])]
            import pandas as pd
            st.download_button(
                "下载 CSV",
                data=pd.DataFrame(export_rows).to_csv(index=False).encode("utf-8-sig"),
                file_name=f"系统使用体验问卷_匿名_{TODAY_STR}.csv",
                mime="text/csv",
            )
            st.caption("🔒 匿名版不含姓名与用户ID（记录ID 已替换为序号），与您屏上可见的匿名卡片信息量一致。")


def improvement_health(imps: list[dict]) -> dict:
    """D3 改进闭环健康度（03 蓝图 §3.3）。纯函数：入参为 db.load_improvements() 的行，便于无头自检。

    口径：
    - 待处理积压 = status=待处理 计数（>3 视为积压预警）；
    - 平均处理时长 = mean(closed_at − created_at)，仅统计「已上线/不采纳」（有明确处置结论的终态）；
    - 回告率 = notified_at 非空的终项 / 终项总数（终项=已上线+不采纳；已搁置默认不回告不计入）；
    - 已确认率 = proposer_ack_at 非空的终项 / 终项总数（站内触达的观察口径，队长 t11 补充）。
    """
    by_status = Counter(i.get("status") for i in imps)
    backlog = by_status.get("待处理", 0)
    terminal = [i for i in imps if i.get("status") in ("已上线", "不采纳")]
    days = []
    for i in terminal:
        try:
            c = datetime.strptime(i.get("created_at") or "", "%Y-%m-%d %H:%M:%S")
            d = datetime.strptime(i.get("closed_at") or "", "%Y-%m-%d %H:%M:%S")
            days.append(max((d - c).total_seconds() / 86400.0, 0.0))
        except ValueError:
            continue
    notified = sum(1 for i in terminal if i.get("notified_at"))
    acked = sum(1 for i in terminal if i.get("proposer_ack_at"))
    n = len(terminal)
    return {
        "total": len(imps),
        "by_status": by_status,
        "backlog": backlog,
        "backlog_warn": backlog > 3,
        "terminal": n,
        "avg_days": (sum(days) / len(days)) if days else 0.0,
        "notified": notified,
        "notify_rate": (notified / n * 100) if n else 0.0,
        "acked": acked,
        "ack_rate": (acked / n * 100) if n else 0.0,
    }


def _imp_source_label(source_type: str) -> str:
    """改进项来源的显示名。"""
    return {"survey": "问卷建议", "rating_comment": "低分/评价", "manual": "管理层自录"}.get(source_type, source_type)


def _imp_notify_external(imp: dict) -> None:
    """外发回告提出人（管理层动作，03 蓝图 §5.5 + 队长 t11 硬要求）。

    - _notify 传 req=None 绕过 P0-P2 优先级门控（门控仅在 req 非空时生效）；
    - 送达回执决定是否写 notified_at：外发失败（提出人两条通道都不可达）绝不标记「已回告」，
      只提示补联系方式后重试；
    - 站内提醒条与外发完全解耦：只要进入需回告终态且提出人未确认，主区提醒条必然可见
      （见 db.pending_improvement_notice 的口径），外发只是尽力而为的增强通道。
    """
    me = get_current_user() or {}
    text = (f"您在需求看板提出的改进建议「{imp['summary']}」已有进展：已{imp['status']}。"
            + (f"说明：{imp['resolution_note']}。" if imp.get("resolution_note") else "")
            + " 感谢您的反馈，让系统更好用。")
    res = _notify([imp["proposer_id"]], text, req=None)
    if imp["proposer_id"] in res.get("delivered", []):
        db.mark_improvement_notified(imp["id"], me.get("role", ""))
        st.session_state["_flash"] = {"type": "success", "text": f"📢 已回告提出人：{imp['summary']}"}
    else:
        st.session_state["_flash"] = {
            "type": "warning",
            "text": (f"⚠ 外发未送达（{USER_ID_TO_NAME.get(imp['proposer_id'], imp['proposer_id'])} "
                     "不在提醒群且无可用邮箱，或未配置钉钉/SMTP）。站内提醒仍然生效；"
                     "可补齐联系方式后重试，重试成功前不计入回告率。"),
        }
    st.rerun()


def _imp_transition(imp: dict, new_status: str, note_key: str) -> None:
    """操作台的状态推进入口：UI 收集结论文案，DAO 校验角色+流转白名单，失败给提示。"""
    me = get_current_user() or {}
    note = (st.session_state.get(note_key) or "").strip()
    if new_status in ("不采纳", "已上线", "已搁置") and not note:
        st.error(f"请先在「处理结论」里填写{'不采纳原因' if new_status == '不采纳' else '上线说明/搁置原因'}。")
        return
    ok, msg = db.update_improvement_status(imp["id"], new_status, me.get("role", ""), resolution_note=note)
    st.session_state["_flash"] = {"type": "success" if ok else "error", "text": ("✅ " if ok else "❌ ") + msg}
    st.rerun()


def _render_improvement_console(imps: list[dict]) -> None:
    """改进项操作台（仅管理层，03 蓝图 §3.3/§6）：新建 / 推进 / 指派 / 归类 / 回告 / 导出。"""
    st.markdown("**改进项操作台**（仅管理层；主管视角为下方匿名化列表）")

    # ＋ 新建改进项（manual，合法可多条，不参与唯一索引防重）
    with st.expander("➕ 新建改进项"):
        users = get_users()
        uid_opts = [u["id"] for u in users]
        c1, c2 = st.columns(2)
        with c1:
            new_summary = st.text_input("一句话标题（回告文案用）", key="imp_new_summary", max_chars=100)
            new_proposer = st.selectbox(
                "提出人", options=uid_opts, key="imp_new_proposer",
                format_func=lambda uid: f"{USER_ID_TO_NAME.get(uid, uid)}（{uid}）")
            new_category = st.selectbox("归类", options=["（未归类）"] + IMPROVEMENT_CATEGORIES,
                                        key="imp_new_category")
        with c2:
            new_detail = st.text_area("详细说明（≤500 字）", key="imp_new_detail", max_chars=500)
        if st.button("创建", type="primary", key="imp_new_submit"):
            if not (new_summary.strip() or new_detail.strip()):
                st.error("请至少填写标题或详细说明。")
            else:
                try:
                    imp_id = db.create_improvement(
                        source_type="manual", source_id=None,
                        proposer_id=new_proposer,
                        proposer_name=USER_ID_TO_NAME.get(new_proposer, ""),
                        summary=new_summary.strip(), detail=new_detail.strip(),
                        category="" if new_category == "（未归类）" else new_category)
                    st.session_state["_flash"] = {"type": "success", "text": f"✅ 已创建改进项 {imp_id}"}
                    st.rerun()
                except Exception as e:
                    st.error(f"创建失败：{e}")

    # 批量补告（03 蓝图 §5.3）：已搁置项默认不回告，这里供管理层批量补告存量
    shelved_unnotified = [i for i in imps if i["status"] == "已搁置" and not i["notified_at"]]
    if shelved_unnotified and st.button(f"📢 批量补告已搁置项（{len(shelved_unnotified)} 条）",
                                        key="imp_notify_shelved_batch"):
        me = get_current_user() or {}
        delivered = 0
        for it in shelved_unnotified:
            text = (f"您在需求看板提出的改进建议「{it['summary']}」已搁置。"
                    + (f"说明：{it['resolution_note']}。" if it.get("resolution_note") else ""))
            res = _notify([it["proposer_id"]], text, req=None)
            if it["proposer_id"] in res.get("delivered", []):
                db.mark_improvement_notified(it["id"], me.get("role", ""))
                delivered += 1
        st.session_state["_flash"] = {
            "type": "success" if delivered else "warning",
            "text": f"批量补告完成：外发送达 {delivered}/{len(shelved_unnotified)} 条"
                    "（未送达者的站内提醒已生效）。",
        }
        st.rerun()

    for it in imps:
        src = _imp_source_label(it["source_type"])
        owner = USER_ID_TO_NAME.get(it["owner_id"], "未指派") if it["owner_id"] else "未指派"
        title = (f"{it['status']} ｜ {it['summary']}"
                 f"（{it['proposer_name'] or it['proposer_id']} · {src} · 负责人 {owner}）")
        with st.expander(title):
            st.markdown(
                f'<div style="font-size:13px;color:#555;">'
                f'提出人：{it["proposer_name"] or it["proposer_id"]}'
                + (f'（{it["proposer_role"]}·{it["proposer_dept"]}）' if it.get("proposer_role") else '')
                + f' ｜ 来源：{src}（{it["source_id"] or "—"}）｜ 归类：{it["category"] or "未归类"}'
                f'<br>创建：{it["created_at"]} ｜ 更新：{it["updated_at"] or "—"}'
                + (f' ｜ 关闭：{it["closed_at"]}' if it["closed_at"] else '')
                + (f' ｜ 已回告：{it["notified_at"]}' if it["notified_at"] else ' ｜ 未回告')
                + (f' ｜ 提出人已知悉：{it["proposer_ack_at"]}' if it["proposer_ack_at"] else '')
                + '</div>',
                unsafe_allow_html=True,
            )
            st.markdown(f"**原文**：{it['detail']}")
            if it["resolution_note"]:
                st.markdown(f"**处理结论**：{it['resolution_note']}")

            note_key = f"imp_note_{it['id']}"
            # 只渲染合法目标状态（流转白名单由 db.IMPROVEMENT_TRANSITIONS 定义，DAO 再校验一层）
            targets = db.IMPROVEMENT_TRANSITIONS.get(it["status"], ())
            btn = {"处理中": ("▶ 开始处理", "primary"), "已上线": ("✅ 标记已上线", "primary"),
                   "不采纳": ("🚫 不采纳", "secondary"), "已搁置": ("⏸ 已搁置", "secondary"),
                   "待处理": ("♻ 重新激活", "secondary")}
            if targets:
                st.text_input("处理结论（推进到终态时必填：上线说明 / 不采纳原因 / 搁置原因）",
                              key=note_key, max_chars=500)
                cols = st.columns(min(len(targets), 3))
                for idx, ts in enumerate(targets):
                    label, kind = btn[ts]
                    if cols[idx % len(cols)].button(label, key=f"imp_go_{it['id']}_{ts}",
                                                    type=kind, use_container_width=True):
                        _imp_transition(it, ts, note_key)
            # 终态（已上线/不采纳）且未回告：精准回告入口
            if it["status"] in ("已上线", "不采纳") and not it["notified_at"]:
                if st.button("📢 回告提出人", key=f"imp_notify_{it['id']}", use_container_width=True):
                    _imp_notify_external(it)

            # 指派负责人 / 修改归类与摘要（非终态可指派；任意状态可改摘要归类）
            with st.form(f"imp_meta_form_{it['id']}"):
                st.markdown("**指派负责人 / 修改归类与摘要**")
                owner_opts = ["（不指派）"] + [u["id"] for u in get_users()]
                pick_owner = st.selectbox(
                    "负责人", options=owner_opts, key=f"imp_owner_{it['id']}",
                    index=0 if not it["owner_id"] else owner_opts.index(it["owner_id"]),
                    format_func=lambda uid: ("（不指派）" if uid == "（不指派）"
                                             else f"{USER_ID_TO_NAME.get(uid, uid)}（{uid}）"),
                    disabled=it["status"] in db.IMPROVEMENT_TERMINAL)
                pick_summary = st.text_input("摘要", value=it["summary"], key=f"imp_sum_{it['id']}",
                                             max_chars=100)
                pick_cat = st.selectbox("归类", options=["（未归类）"] + IMPROVEMENT_CATEGORIES,
                                        key=f"imp_cat_{it['id']}",
                                        index=0 if not it["category"] else (["（未归类）"] + IMPROVEMENT_CATEGORIES).index(it["category"]))
                if st.form_submit_button("保存", type="primary"):
                    me = get_current_user() or {}
                    if not (it["status"] in db.IMPROVEMENT_TERMINAL):
                        ok, msg = db.assign_improvement_owner(
                            it["id"], "" if pick_owner == "（不指派）" else pick_owner, me.get("role", ""))
                        if not ok:
                            st.error(msg)
                    ok, msg = db.update_improvement_meta(
                        it["id"], me.get("role", ""), summary=pick_summary,
                        category="" if pick_cat == "（未归类）" else pick_cat)
                    if not ok:
                        st.error(msg)
                    else:
                        st.session_state["_flash"] = {"type": "success", "text": f"✅ {it['id']} 已保存"}
                        st.rerun()

    with st.expander("📥 导出改进项记录"):
        import pandas as pd
        st.download_button(
            "下载 CSV",
            data=pd.DataFrame([{
                "ID": i["id"], "状态": i["status"], "来源": _imp_source_label(i["source_type"]),
                "来源ID": i["source_id"] or "", "提出人": i["proposer_name"] or i["proposer_id"],
                "归类": i["category"], "摘要": i["summary"], "原文": i["detail"],
                "负责人": USER_ID_TO_NAME.get(i["owner_id"], "") if i["owner_id"] else "",
                "处理结论": i["resolution_note"], "创建": i["created_at"], "更新": i["updated_at"],
                "关闭": i["closed_at"], "已回告": i["notified_at"], "提出人已知悉": i["proposer_ack_at"],
            } for i in imps]).to_csv(index=False).encode("utf-8-sig"),
            file_name=f"改进项记录_{TODAY_STR}.csv",
            mime="text/csv",
        )


def _render_eval_improvement_section():
    """D3 改进闭环健康度维度（03 蓝图 §3.3，一期核心）。"""
    st.caption("问卷与评价中提出的建议在此跟踪：待处理 → 处理中 → 已上线 / 不采纳 / 已搁置；"
               "终态后回告提出人（站内提醒 + 钉钉/邮件尽力而为）。")
    imps = db.load_improvements(with_proposer=_can_view_rating_detail())
    h = improvement_health(imps)
    if h["total"] == 0:
        st.info("暂无改进项。用户在问卷中提交建议会自动进入闭环；管理层也可从低分评价一键转入，或在此新建。")
        return

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("全部改进项", h["total"])
    m2.metric("待处理积压", h["backlog"])
    m3.metric("处理中", h["by_status"].get("处理中", 0))
    m4.metric("平均处理时长", f"{h['avg_days']:.1f} 天" if h["terminal"] else "—",
              help="mean(closed_at − created_at)，仅统计已上线/不采纳（有明确处置结论的终态）")
    m5.metric("回告率", f"{h['notify_rate']:.0f}%" if h["terminal"] else "—",
              help="已外发回告（notified_at）的终项 / 终项总数；站内已确认率另见下方说明")
    if h["backlog_warn"]:
        st.warning(f"⚠ 待处理改进项已积压 {h['backlog']} 条（>3），建议尽快认领或关闭。")
    if h["terminal"]:
        st.caption(f"终项（已上线+不采纳）{h['terminal']} 条：已外发回告 {h['notified']} 条，"
                   f"提出人已确认（站内「知道了」）{h['acked']} 条（{h['ack_rate']:.0f}%）。"
                   "站内提醒不依赖外发成功——外发失败项仍会在提出人主区显示。")

    st.markdown("**状态分布**")
    _render_bar_chart(
        [(s, h["by_status"].get(s, 0), color) for s, color in
         zip(db.IMPROVEMENT_STATUSES, ("#FB8C00", "#1E88E5", "#43A047", "#9E9E9E", "#B0BEC5"))],
        total=max(h["total"], 1),
    )

    if _can_view_rating_detail():
        _render_improvement_console(imps)
    else:
        # 主管：匿名化只读列表（取数层已不含 proposer_name，03 蓝图 §5.4/§5.6-7）
        st.markdown("**改进项列表（匿名，仅汇总与推进状态）**")
        st.table([{
            "状态": i["status"], "归类": i["category"] or "未归类",
            "摘要": i["summary"], "详情（截断）": i["detail"][:60] + ("…" if len(i["detail"]) > 60 else ""),
            "创建时间": i["created_at"],
        } for i in imps])
        st.caption("🔒 按统一口径，改进项的提出人姓名仅管理层可见；如需推动组员提建议，可使用匿名视图。")


def _render_eval_activity_section():
    """D4 用户活跃（t20 新增，03 蓝图的有意范围扩展）：DAU/WAU/MAU + 页面热度 + 逐人明细。

    如实口径：埋点只能向前生效——上线前的历史访问无法回溯，指标自上线起从 0 累积，
    不得用其他表的时间戳（如 requirements.created_at）伪造活跃度数字。
    权限：DAU/WAU/MAU 与页面热度对管理层+主管可见；逐人明细属「点名式同事数据」，
    仅管理层可见（复用 _can_view_rating_detail，与逐人评价明细同口径）。
    """
    a = db.activity_stats()
    if not a.get("has_data"):
        st.info("暂无活跃记录：埋点自本次上线后开始累积（今天之前的历史访问无法回溯），"
                "近 24 小时/近 7 天/近 30 天活跃与页面热度会从 0 起步、随使用逐步变得有意义。")
        return
    m1, m2, m3 = st.columns(3)
    # 标签用「近 24 小时」而非「今日」：dau 是滚动 24 小时窗口（created_at >= now-1天），
    # 不是日历日——上午打开页面时「今日」会把昨日午后的活跃误读为今天（t24 R-1 修复）
    m1.metric("近 24 小时活跃（DAU）", a["dau"])
    m2.metric("近 7 天活跃（WAU）", a["wau"])
    m3.metric("近 30 天活跃（MAU）", a["mau"])
    # 累计使用量（向上汇报口径）：滚动窗口只回答「最近热不热」，累计数回答「一共用了多少」
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("注册用户数", a["total_users"])
    c2.metric("累计登录次数", a["total_logins"])
    c3.metric("累计页面访问次数", a["total_views"])
    c4.metric("MAU 渗透率", f"{a['mau'] / a['total_users'] * 100:.0f}%" if a["total_users"] else "—",
              help="近 30 天活跃用户 ÷ 注册用户数，反映系统在全员中的覆盖面")
    st.caption("口径：DAU/WAU/MAU 为近 1/7/30 天内有埋点记录（登录或页面访问）的去重用户数，"
               "DAU 是滚动 24 小时窗口；累计数自埋点上线起算，历史不回溯。")

    # 每日活跃趋势（近 30 个日历日，含今天；无记录的日期补 0，曲线连续不断轴）
    import pandas as pd  # 与其他导出同款局部导入
    by_day = {d["date"]: d["users"] for d in a["daily"]}
    days = [(TODAY - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(29, -1, -1)]
    st.markdown("**每日活跃人数（近 30 天，按日历日去重）**")
    st.line_chart(
        pd.DataFrame({"活跃人数": [by_day.get(d, 0) for d in days]}, index=days),
        color="#00897B", height=220,
    )

    # CSV 导出（向领导交数的出口）：汇总 + 每日趋势 + 页面热度 + 逐人明细；
    # 逐人明细沿用页面同一权限（仅管理层），主管导出的文件不含该节
    def _activity_csv() -> str:
        sections = [
            ("汇总", pd.DataFrame([
                {"指标": "近24小时活跃（DAU）", "数值": a["dau"]},
                {"指标": "近7天活跃（WAU）", "数值": a["wau"]},
                {"指标": "近30天活跃（MAU）", "数值": a["mau"]},
                {"指标": "注册用户数", "数值": a["total_users"]},
                {"指标": "累计登录次数", "数值": a["total_logins"]},
                {"指标": "累计页面访问次数", "数值": a["total_views"]},
                {"指标": "统计截止", "数值": TODAY_STR},
            ])),
            ("每日活跃·近30天", pd.DataFrame([
                {"日期": d["date"], "活跃人数": d["users"], "埋点次数": d["events"]}
                for d in a["daily"]
            ])),
            ("页面热度·近30天", pd.DataFrame([
                {"页面": p, "访问次数": c} for p, c in a["page_heat"]
            ])),
        ]
        if _can_view_rating_detail():
            sections.append(("逐人明细·近30天·仅管理层", pd.DataFrame([{
                "姓名": u["user_name"] or u["user_id"], "角色": u["role"],
                "访问次数": u["visits"], "活跃天数": u["active_days"],
                "最近活跃": u["last_active"],
            } for u in a["user_activity"]])))
        return "\n".join(f"【{name}】\n{df.to_csv(index=False).rstrip('\n')}"
                         for name, df in sections)

    st.download_button(
        "⬇ 下载用户活跃 CSV",
        data=_activity_csv().encode("utf-8-sig"),
        file_name=f"用户活跃_{TODAY_STR}.csv",
        mime="text/csv",
        use_container_width=False,
        help="含汇总、每日活跃趋势、页面热度"
             + ("、逐人明细（仅管理层）" if _can_view_rating_detail() else ""),
    )

    if a["page_heat"]:
        st.markdown("**页面热度（近 30 天访问次数）**")
        _render_bar_chart(
            [(p, c, "#00897B") for p, c in a["page_heat"]],
            total=max(a["page_heat"][0][1], 1),
        )
    else:
        st.caption("近 30 天暂无页面访问记录（只有登录事件）。")
    # 逐人明细：仅管理层（点名式同事数据）；主管可见上面的汇总与页面热度
    if _can_view_rating_detail():
        if a["user_activity"]:
            st.markdown("**逐人活跃明细（近 30 天，仅管理层可见）**")
            st.table([{
                "姓名": u["user_name"] or u["user_id"], "角色": u["role"],
                "访问次数": u["visits"], "活跃天数": u["active_days"],
                "最近活跃": u["last_active"],
            } for u in a["user_activity"]])
    else:
        st.caption("🔒 逐人活跃明细仅管理层可见。")


def _dt_any(s) -> datetime | None:
    """解析三种精度的时间串（秒级/分钟级/日级），失败返回 None。

    阶段效率统计要同时读新埋点（%H:%M 的流转记录、%H:%M:%S 的 created_dt）
    与存量字段（日级的 created_at / actual_completion_date），
    统一走这一个入口，避免每个调用点各写一串 try。
    """
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(s or "").strip(), fmt)
        except ValueError:
            continue
    return None


def _fmt_fill_secs(secs: float) -> str:
    """填写耗时的展示格式：秒级埋点按人类习惯归到分钟。"""
    return f"{secs / 60:.1f} 分钟" if secs >= 60 else f"{secs:.0f} 秒"


def _render_eval_stage_efficiency_section():
    """⛽ 阶段效率：表单填写耗时 + 各阶段停留 + 交付周期/按期率 + 卡单预警。

    数据口径（与「用户活跃」区块同一原则，如实声明）：
    - 填写耗时（form_fill_seconds）与阶段停留（status_history）为本次新增埋点，
      存量需求没有这些字段——不计入、也不得用 created_at 等日级时间戳伪造，
      指标自上线起从 0 累积；
    - 交付周期与按期完成率用存量字段（created_at / actual_completion_date，日级精度），
      全量历史可算，作为埋点积累期的对照基线。
    权限：汇总指标对管理层+主管可见；「逐人填写耗时明细」属点名式数据，
    仅管理层可见（复用 _can_view_rating_detail，与用户活跃明细同口径）。
    """
    reqs = get_requirements()
    now = datetime.now()

    def _avg(xs):
        return (sum(xs) / len(xs)) if xs else None

    # ---- 1) 表单填写耗时（新埋点）----
    fills = [r for r in reqs
             if isinstance(r.get("form_fill_seconds"), (int, float))
             and r["form_fill_seconds"] > 0]
    all_secs = sorted(r["form_fill_seconds"] for r in fills)
    ai_secs = sorted(r["form_fill_seconds"] for r in fills if r.get("ai_assisted"))
    manual_secs = sorted(r["form_fill_seconds"] for r in fills if not r.get("ai_assisted"))

    # ---- 2) 阶段停留（新埋点）：相邻两条流转记录的间隔 = 前一阶段的停留时长 ----
    seg_days: dict[str, list[float]] = {}
    for r in reqs:
        pts = [(h.get("to"), _dt_any(h.get("time"))) for h in (r.get("status_history") or [])]
        for (stage, t0), (_nxt, t1) in zip(pts, pts[1:]):
            if stage and t0 and t1 and t1 >= t0:
                seg_days.setdefault(stage, []).append((t1 - t0).total_seconds() / 86400.0)
    confirm_days = seg_days.get("待确认", [])

    # ---- 3) 交付周期与按期率（存量字段，全量历史可算）----
    delivery, on_time, with_due = [], 0, 0
    for r in reqs:
        if r.get("status") != "已完成":
            continue
        fin = _dt_any(r.get("actual_completion_date"))
        created = _dt_any(r.get("created_at"))
        if fin and created and fin >= created:
            delivery.append((fin - created).total_seconds() / 86400.0)
        due = _dt_any(r.get("requested_due_date"))
        if due and fin:
            with_due += 1
            if fin <= due:
                on_time += 1

    # ---- 4) 卡单预警：开放状态停留超过阶段阈值 ----
    # 待确认是共识门禁要求快；需求池/待启动是排队分配；进行中/暂停周期天然更长。
    # 存量需求没有留痕：仅「待确认/需求池」可用创建日期近似进入时间（误差≤1 天），
    # 后续阶段无法知道何时进入——宁缺毋假，不计入预警。
    STUCK_RULES = {"待确认": 3, "需求池": 7, "待启动": 7, "进行中": 14, "暂停": 14}
    stuck = []
    for r in reqs:
        cur = r.get("status")
        if cur not in STUCK_RULES:
            continue
        hist = r.get("status_history") or []
        if hist:
            anchor = _dt_any(hist[-1].get("time"))
        elif cur in ("待确认", "需求池"):
            anchor = _dt_any(r.get("created_dt")) or _dt_any(r.get("created_at"))
        else:
            anchor = None
        if anchor and anchor <= now:
            days = (now - anchor).total_seconds() / 86400.0
            if days > STUCK_RULES[cur]:
                stuck.append((days, r))
    stuck.sort(key=lambda x: -x[0])

    # ---- 指标行（埋点类指标与存量指标并排，help 注明各自口径）----
    m1, m2, m3, m4 = st.columns(4)
    a_fill, a_confirm, a_deliv = _avg(all_secs), _avg(confirm_days), _avg(delivery)
    m1.metric("平均填写表单", _fmt_fill_secs(a_fill) if a_fill else "—",
              help="从打开「新建需求」页到提交成功的耗时（超 4 小时的挂起会话不计入）；埋点自本功能上线起累积")
    m2.metric("待确认平均耗时", f"{a_confirm:.1f} 天" if a_confirm is not None else "—",
              help="需求从提交到主管确认入池的平均停留，即共识速度；埋点自本功能上线起累积")
    m3.metric("平均交付周期", f"{a_deliv:.1f} 天" if a_deliv is not None else "—",
              help="已完成需求：实际完成日期 − 创建日期（日级精度，全量历史可算）")
    m4.metric("按期完成率", f"{on_time / with_due * 100:.0f}%" if with_due else "—",
              help="已完成且约定了期望完成日期的需求中，实际完成 ≤ 期望日期的比例")

    # ---- 表单填写效率 ----
    st.markdown("**表单填写效率**")
    if not fills:
        st.info("暂无填写耗时记录：该埋点自本功能上线起累积（此前创建的需求没有计时，不伪造数字）。")
    else:
        c_ai, c_man = st.columns(2)
        c_ai.metric(f"AI 预填平均 · {len(ai_secs)} 条", _fmt_fill_secs(_avg(ai_secs)) if ai_secs else "—")
        c_man.metric(f"纯手动平均 · {len(manual_secs)} 条", _fmt_fill_secs(_avg(manual_secs)) if manual_secs else "—")
        med = all_secs[len(all_secs) // 2]
        st.caption(f"口径：{len(fills)} 条有效记录 · 中位数 {_fmt_fill_secs(med)} · "
                   f"最长 {_fmt_fill_secs(all_secs[-1])}；超 4 小时的挂起会话未计入。")
        if _can_view_rating_detail():
            st.markdown("**逐人填写明细（近 30 条，仅管理层可见）**")
            st.table([{
                "需求": r["id"],
                "标题": (r.get("title") or "")[:18],
                "提出人": USER_ID_TO_NAME.get(r.get("requester_id", ""), r.get("requester_id") or "—"),
                "耗时": _fmt_fill_secs(r["form_fill_seconds"]),
                "AI 预填": "是" if r.get("ai_assisted") else "否",
                "提交时间": r.get("created_dt") or r.get("created_at") or "",
            } for r in sorted(fills, key=lambda r: r.get("created_dt") or "", reverse=True)[:30]])
        else:
            st.caption("🔒 逐人填写明细仅管理层可见。")

    # ---- 各阶段平均停留 ----
    st.markdown("**各阶段平均停留**")
    order = [s for s in ["待确认", "需求池", "待启动", "进行中", "暂停"] if s in seg_days]
    if order:
        rows = [(s, _avg(seg_days[s]), STATUS_COLORS.get(s, "#999")) for s in order]
        texts = [f"{_avg(seg_days[s]):.1f} 天 · {len(seg_days[s])} 段" for s in order]
        _render_bar_chart(rows, total=max(r[1] for r in rows), show_value=lambda i: texts[i])
        st.caption("口径：相邻两条流转记录的间隔 = 前一阶段的停留时长；仅统计已走完的历史停留段，"
                   "当前正在停留的时段见下方「卡单预警」；自埋点上线起累积。")
    else:
        st.info("暂无阶段停留记录：流转留痕（status_history）自本功能上线起累积，"
                "此前创建的需求没有分钟级时间链，不伪造数字。")

    # ---- 卡单预警 ----
    st.markdown("**卡单预警**")
    if stuck:
        st.warning(f"{len(stuck)} 条需求在当前阶段停留过久"
                   "（阈值：待确认 3 天 · 需求池/待启动 7 天 · 进行中/暂停 14 天）")
        st.table([{
            "需求": r["id"],
            "标题": (r.get("title") or "")[:18],
            "当前状态": r.get("status"),
            "已停留": f"{days:.0f} 天",
            "负责人": USER_ID_TO_NAME.get(r.get("requested_owner_id", ""), r.get("requested_owner_id") or "—"),
        } for days, r in stuck[:15]])
        if len(stuck) > 15:
            st.caption(f"仅列出停留最久的前 15 条，共 {len(stuck)} 条。")
    else:
        st.success("无卡单：所有开放状态的需求都在阶段阈值内正常流转"
                   "（存量需求仅对待确认/需求池按创建日期近似判断，后续阶段无留痕不计入）。")


def _render_eval_improvement_feed():
    """🌟 改进动态（03 蓝图 §5.5/§6）：已上线改进项公示，强化「提了有用」的正反馈。"""
    live = db.load_improvements(status="已上线", with_proposer=True)
    if not live:
        st.caption("暂无已上线的改进项。建议被采纳上线后会在这里公示（保留提出人姓名——已上线内容无点名敏感性）。")
        return
    for it in live[:20]:
        st.markdown(
            f'<div style="border-left:3px solid #43A047;padding:4px 10px;margin-bottom:6px;">'
            f'<div>🎉 「{it["summary"]}」已上线</div>'
            f'<div style="font-size:12px;color:#666;">'
            f'提出人：{it["proposer_name"] or it["proposer_id"]} · {it["closed_at"] or it["updated_at"] or ""}'
            + (f' · {it["resolution_note"]}' if it["resolution_note"] else '')
            + '</div></div>',
            unsafe_allow_html=True,
        )


def page_system_eval():
    """📋 系统评估（管理层+主管可见）：需求评价 + KUI 问卷 + 改进闭环 + 用户活跃 + 阶段效率 + 改进动态。

    t31 卡片化：仅容器层调整（统一页头 + 卡片包裹五个区块函数的调用点）；
    区块函数本体零改动——业务逻辑、统计口径与权限门控不动。
    t38：五个区块由「竖着堆」改为横向标签互斥显示（版式参考客诉系统系统评估页的
    Tab 切换条，与「项目视图」用同一套 _nav_tabs）。
    附带收益：原来五个区块每次 rerun 都要全部执行一遍（各自含库查询/统计聚合），
    改成标签后**只执行当前这一块**，页面更快；区块函数本体仍零改动。
    """
    _page_header("📋 系统评估",
                 "系统自身的运营评估——数据来自问卷与需求评价，用于判断系统好不好用、哪里要改、改进是否落地")
    _tab = _nav_tabs(
        [
            ("rating", "⭐ 需求评价"),
            ("survey", "📝 KUI 问卷"),
            ("improvement", "🔄 改进闭环"),
            ("activity", "👥 用户活跃"),
            ("stage", "⛽ 阶段效率"),
            ("feed", "🌟 改进动态"),
        ],
        state_key="eval_tab",
    )
    # 每个标签内容仍包在卡片里（保持与其他页一致的卡片视觉）；
    # 区块函数自身已带「## 标题」级小标题，与标签文案重复，故本层不加卡片标题条。
    with _card():
        if _tab == "rating":
            _render_eval_rating_section()
        elif _tab == "survey":
            _render_eval_survey_section()
        elif _tab == "improvement":
            _render_eval_improvement_section()
        elif _tab == "activity":
            _render_eval_activity_section()
        elif _tab == "stage":
            _render_eval_stage_efficiency_section()
        else:
            _render_eval_improvement_feed()


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


def get_requirement(req_id: str) -> dict | None:
    for r in get_requirements():
        if r["id"] == req_id:
            return r
    return None


def save_requirement(req: dict):
    """同步写入数据库与 session_state 缓存"""
    db.save_requirement(req)
    _invalidate_data_caches()
    for i, r in enumerate(st.session_state.requirements):
        if r["id"] == req["id"]:
            st.session_state.requirements[i] = req
            _mark_own_write()
            return
    st.session_state.requirements.append(req)
    _mark_own_write()


def delete_requirement(req_id: str):
    """删除需求（含其附件、佐证材料与评论区附件的上传文件）"""
    req = get_requirement(req_id)
    if req:
        for att in req.get("attachments", []):
            db.remove_attachment(req_id, att.get("filename", ""))
        for ev in req.get("evidence", []):
            if ev.get("filename"):
                db.remove_attachment(req_id, ev.get("filename"))
        for c in req.get("comments", []):
            for f_meta in c.get("files") or []:
                db.remove_attachment(req_id, f_meta.get("filename", ""))
    db.delete_requirement(req_id)
    _invalidate_attachment_cache()
    st.session_state.requirements = [r for r in st.session_state.requirements if r["id"] != req_id]
    _mark_own_write()


def user_load(user_id: str) -> float:
    """计算用户当前在跑需求的总估算工作量"""
    return _user_load_map().get(user_id, 0.0)


def user_task_count(user_id: str) -> int:
    """计算用户当前在跑需求的任务数量"""
    return _user_task_count_map().get(user_id, 0)


def _active_reqs() -> list[dict]:
    """在跑（待启动/进行中/暂停）需求，作为负载聚合的公共过滤"""
    return [
        r for r in get_requirements()
        if r.get("status") in ["待启动", "进行中", "暂停"]
    ]


@st.cache_data(ttl=15, show_spinner=False)
def _user_task_count_map() -> dict:
    """一次遍历聚合所有用户的在跑任务数，避免每个工程师都全量扫一遍。"""
    counts = Counter()
    for r in _active_reqs():
        for uid in r.get("owner_ids", []):
            counts[uid] += 1
    return dict(counts)


@st.cache_data(ttl=15, show_spinner=False)
def _user_load_map() -> dict:
    """一次遍历聚合所有用户的在跑总工作量。"""
    totals: dict = {}
    for r in _active_reqs():
        effort = float(r.get("effort_estimate_days") or 0)
        for uid in r.get("owner_ids", []):
            totals[uid] = totals.get(uid, 0.0) + effort
    return totals


# ============================================================
# 页面：中心总览看板
# ============================================================

def page_overview():
    # t31 卡片化：统一页头 + KPI（st.metric 由全局 CSS 卡片化）+ 各区块白卡片；业务逻辑零改动
    reqs = get_requirements()
    _page_header("📊 中心总览", f"数据截至 {TODAY_STR} · 共 {len(reqs)} 条需求",
                 "➕ 新建需求", "➕ 新建需求")

    col1, col2, col3, col4, col5 = st.columns(5)
    status_count = Counter(r["status"] for r in reqs)
    with col1:
        st.metric("在跑需求", sum(status_count.get(s, 0) for s in ["待启动", "进行中", "暂停"]))
    with col2:
        st.metric("本月新增", sum(1 for r in reqs if r.get("created_at", "") >= MONTH_START_STR))
    with col3:
        st.metric("本月完成/终止", sum(1 for r in reqs if r.get("status") in ["已完成", "已终止"] and r.get("actual_completion_date", "") >= MONTH_START_STR))
    with col4:
        st.metric("暂停中", status_count.get("暂停", 0))
    with col5:
        st.metric("P0/P1", sum(1 for r in reqs if r.get("priority") in ["P0", "P1"] and r.get("status") not in ["已完成", "已终止"]))

    with _card("📋 需求状态分布", "按状态分组 · 悬停查看需求 · 点击「查看」跳转需求管理"):
        # 注入 hover CSS（各状态卡片启用悬停浮层）
        st.markdown("""
        <style>
        .hover-status-card { position: relative; cursor: pointer; }
        .hover-status-card .req-panel {
            display: none;
            position: absolute;
            top: 100%;
            left: 50%;
            transform: translateX(-50%);
            z-index: 9999;
            width: 300px;
            max-height: 360px;
            overflow-y: auto;
            background: #ffffff;
            border: 1px solid #e0e0e0;
            border-radius: 8px;
            box-shadow: 0 6px 20px rgba(0,0,0,.18);
            padding: 6px;
            text-align: left;
            font-weight: normal;
        }
        .hover-status-card:hover .req-panel { display: block; }
        .hover-status-card .req-item {
            border-bottom: 1px solid #f0f0f0;
            padding: 6px 4px;
        }
        .hover-status-card .req-item:last-child { border-bottom: none; }
        </style>
        """, unsafe_allow_html=True)

        # 构建状态卡片（有需求的状态带 hover 面板），每卡片下方带跳转按钮
        status_items = {status: [] for status in STATUS_LABELS}
        for r in reqs:
            status_items.setdefault(r.get("status"), []).append(r)
        status_cols = st.columns(len(STATUS_LABELS))
        for i, status in enumerate(STATUS_LABELS):
            with status_cols[i]:
                items = status_items.get(status, [])
                color = STATUS_COLORS.get(status, "#9E9E9E")
                panel_html = ""
                if items:
                    rows = []
                    for r in items:
                        owner = _owner_names(r)
                        due = r.get("requested_due_date") or "无截止日"
                        rows.append(
                            f'<div class="req-item">'
                            f'<b>{r["id"]}</b> {priority_badge(r.get("priority", "未知"))}'
                            f'<div style="font-size:12px;margin-top:2px;">{r["title"]}</div>'
                            f'<div style="font-size:11px;color:#666;margin-top:2px;">👤 {owner} · 📅 {due}</div>'
                            f'</div>'
                        )
                    panel_html = f'<div class="req-panel">{"".join(rows)}</div>'
                st.markdown(
                    f'<div class="hover-status-card" style="text-align:center;padding:8px;border-radius:8px;background:{color}22;">'
                    f'<div style="font-size:24px;font-weight:700;color:{color};">{status_count.get(status, 0)}</div>'
                    f'<div style="font-size:12px;color:#555;">{status}</div>'
                    f'{panel_html}'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                if st.button("🔍 查看", key=f"status_jump_{status}", use_container_width=True,
                             help=f"跳转到需求管理并筛选「{status}」"):
                    st.session_state["_pending_status_filter"] = status
                    st.session_state["_pending_nav"] = True
                    st.session_state["_pending_nav_target"] = "📦 需求管理"
                    st.rerun()

    left, right = st.columns(2)

    with left:
        with _card("🏢 按部门分布"):
            source_count = Counter(r["source"] for r in reqs)
            for src, cnt in source_count.most_common():
                pct = cnt / max(len(reqs), 1) * 100
                st.markdown(
                    f'<div style="margin-bottom:6px;"><span style="display:inline-block;width:120px;">{src}</span>'
                    f'<span style="display:inline-block;background:#1E88E5;height:14px;width:{pct*3:.0f}px;border-radius:3px;vertical-align:middle;margin:0 8px;"></span>'
                    f'<span style="font-weight:600;">{cnt}</span></div>',
                    unsafe_allow_html=True,
                )

    with right:
        with _card("👷 工程师负载", "在跑任务数量"):
            engineers = [u for u in USERS if u["role"] == "工程师"]
            task_count_map = _user_task_count_map()
            eng_loads = [(u["name"], task_count_map.get(u["id"], 0)) for u in engineers]
            eng_loads.sort(key=lambda x: -x[1])
            max_load = max((l for _, l in eng_loads), default=1)
            # 全部行拼进一个滚动容器（超出高度出滚动条，不足则无滚动条），
            # 同时把原本「每人一个 st.markdown」合并为单个元素，减少组件数
            load_rows = []
            for name, load in eng_loads:
                pct = load / max(max_load, 0.1) * 100
                color = "#E53935" if load > 8 else "#FB8C00" if load > 4 else "#43A047"
                load_rows.append(
                    f'<div style="margin-bottom:6px;"><span style="display:inline-block;width:80px;">{name}</span>'
                    f'<span style="display:inline-block;background:{color};height:14px;width:{max(pct*2,2):.0f}px;border-radius:3px;vertical-align:middle;margin:0 8px;"></span>'
                    f'<span style="font-weight:600;">{load}</span></div>'
                )
            st.markdown(
                f'<div style="max-height:240px;overflow-y:auto;padding-right:6px;">{"".join(load_rows)}</div>',
                unsafe_allow_html=True,
            )

    # ===== 需求评价（一期评价体系）=====
    with _card("⭐ 需求评价", "仅已完成需求 · 提出人/建议责任人/执行人各评一次"):
        sat = satisfaction_stats(reqs)
        st.caption("口径：只统计「已完成」需求上的评价；提出人 / 建议责任人 / 执行人各评一次，按评价条目计。")
        if sat["done"] == 0:
            st.info("暂无已完成需求，评价数据将在需求交付后产生。")
        else:
            s1, s2, s3, s4 = st.columns(4)
            s1.metric("平均分", f"{sat['avg']:.2f} / 5" if sat["rated"] else "—",
                      f"满意率 {sat['positive_rate']:.0f}%" if sat["rated"] else None)
            s2.metric("已评价 / 应评价", f"{sat['rated']} / {sat['expected']}")
            s3.metric("待评价", sat["missing"])
            s4.metric("已完成需求", sat["done"],
                      f"其中 {sat['unrated_reqs']} 条无人评价" if sat["unrated_reqs"] else None)
            if sat["rated"]:
                _render_bar_chart(
                    [(f"{k} 星 {SATISFACTION_LABELS.get(k, '')}", sat["dist"].get(k, 0),
                      SATISFACTION_COLORS.get(k, "#9E9E9E")) for k in [5, 4, 3, 2, 1]],
                    total=max(sat["rated"], 1),
                )
                # 按角色分组：提出人视角 vs 执行人视角的差异本身有洞察价值
                role_rows = []
                for role in ("提出人", "建议责任人", "执行人"):
                    vals = sat["by_role"].get(role)
                    if vals:
                        role_rows.append((f"{role}（{len(vals)} 条）", sum(vals) / len(vals)))
                if role_rows:
                    st.markdown("**按角色平均分**")
                    _render_bar_chart(
                        [(label, round(avg * 10), "#7E57C2") for label, avg in role_rows],
                        total=50,
                        show_value=lambda i: f"{role_rows[i][1]:.2f}",
                    )
            else:
                st.caption("已完成需求尚无评价记录。")

    with _card("⚠️ 风险提醒", "基于规则自动扫描"):
        risks = []
        for r in reqs:
            if r.get("status") == "暂停":
                risks.append(f'{r["id"]} 「{r["title"]}」 处于暂停状态')
            if r.get("priority") in ["P0", "P1"] and r.get("status") in ["待启动", "需求池"]:
                risks.append(f'{r["id"]} 「{r["title"]}」 高优先级但未启动')
            if r.get("requested_due_date") and r.get("status") not in ["已完成", "已终止"]:
                try:
                    due = datetime.strptime(r["requested_due_date"], "%Y-%m-%d").date()
                    if due < TODAY:
                        overdue_days = (TODAY - due).days
                        risks.append(f'{r["id"]} 「{r["title"]}」 已超期 {overdue_days} 天')
                except Exception:
                    pass
        if risks:
            for risk in risks[:10]:
                st.markdown(f"- {risk}")
        else:
            st.info("暂无风险")

    # ===== 紧急度 × 重要度 四象限 =====
    with _card():
        render_priority_quadrant()


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


# ============================================================
# 页面：我的看板
# ============================================================

def page_my_kanban():
    # t31 卡片化：统一页头 + 看板列/四象限白卡片；业务逻辑零改动
    user_id = st.session_state.current_user_id
    user_name = USER_ID_TO_NAME.get(user_id, "未知")

    my_reqs = [
        r for r in get_requirements()
        if user_id in r.get("owner_ids", [])
        or r.get("requester_id") == user_id
        or r.get("requested_owner_id") == user_id
    ]

    _page_header(f"📋 我的看板 · {user_name}", f"与您相关的需求共 {len(my_reqs)} 条",
                 "➕ 新建需求", "➕ 新建需求")

    if not my_reqs:
        st.info("您当前没有关联的需求")
        return

    status_groups = {
        "待确认": [],
        "需求池": [],
        "待启动": [],
        "进行中": [],
        "暂停": [],
        "已完成": [],
        "已终止": [],
    }
    for r in my_reqs:
        s = r.get("status")
        if s in status_groups:
            status_groups[s].append(r)

    with _card("📋 我的需求", "按状态分列 · 点击卡片下方「查看」进入详情"):
        cols = st.columns(len(status_groups))
        for col, (status, items) in zip(cols, status_groups.items()):
            with col:
                st.markdown(
                    f'<div style="background:{STATUS_COLORS[status]}11;padding:8px;border-radius:8px 8px 0 0;border-left:4px solid {STATUS_COLORS[status]};">'
                    f'<span style="font-weight:600;color:{STATUS_COLORS[status]};">{status}</span>'
                    f'<span style="float:right;color:#777;">{len(items)}</span></div>',
                    unsafe_allow_html=True,
                )
                with st.container(height=520):
                    for r in items:
                        card_html = (
                            f'<div style="background:white;border:1px solid #ECEFF1;border-radius:6px;padding:10px;margin:6px 0;">'
                            f'<div style="font-size:11px;color:#888;margin-bottom:4px;">{r["id"]}</div>'
                            f'<div style="font-weight:600;font-size:13px;margin-bottom:6px;">{r["title"]}</div>'
                            f'<div>{priority_badge(r.get("priority","未知"))}</div>'
                            f'<div style="margin-top:6px;font-size:11px;color:#666;">'
                        )
                        if r.get("requested_due_date"):
                            try:
                                due = datetime.strptime(r["requested_due_date"], "%Y-%m-%d").date()
                                days_left = (due - TODAY).days
                                status = r.get("status", "")
                                if status in ("已完成", "已终止"):
                                    card_html += f'⏰ {r["requested_due_date"]}'
                                elif days_left < 0:
                                    card_html += f'⏰ {r["requested_due_date"]}（<span style="color:#E53935;font-weight:600;">逾期 {-days_left} 天</span>）'
                                else:
                                    card_html += f'⏰ {r["requested_due_date"]}（剩 {days_left} 天）'
                            except Exception:
                                card_html += f'⏰ {r["requested_due_date"]}'
                        card_html += '</div>'
                        # 子需求进度摘要（多执行人需求自动拆分后展示）
                        my_subs = r.get("sub_requirements") or []
                        if my_subs:
                            my_done = sum(1 for s in my_subs if s.get("status") in ("已完成", "已终止"))
                            card_html += (
                                f'<div style="margin-top:4px;font-size:11px;color:#1565C0;">'
                                f'🧩 子需求完成 {my_done}/{len(my_subs)}</div>'
                            )
                        card_html += '</div>'
                        st.markdown(card_html, unsafe_allow_html=True)
                        if st.button("查看", key=f"card_{r['id']}", use_container_width=True):
                            st.session_state["selected_req_id"] = r["id"]
                            st.session_state["_pending_nav"] = True
                            st.session_state["_pending_nav_target"] = "📄 需求详情"
                            st.rerun()

    # 我的看板四象限（仅当前用户相关需求）
    with _card():
        render_priority_quadrant(my_reqs, render_matrix=False)


# ============================================================
# 页面：需求管理（v0.3 新增）- 全需求列表 + Excel 导入导出
# ============================================================

# Excel 列名 <-> Requirement 字段映射
EXCEL_COLUMN_MAP = [
    ("需求ID", "id"),
    ("标题", "title"),
    ("描述", "description"),
    ("部门", "source"),
    ("状态", "status"),
    ("优先级", "priority"),
    ("客户名", "customer"),
    ("提出人", "_requester_name"),
    ("建议责任人", "_requested_owner_name"),
    ("责任人", "_owner_name"),
    ("关联项目", "_project_name"),
    ("期望完成日期", "requested_due_date"),
    ("价值评分", "value_rating"),
    ("复杂度评分", "complexity_rating"),
    ("评分来源", "rating_source"),
    ("标签", "_tags_str"),
    ("佐证材料链接", "_evidence_link"),
    ("估算工作量(人天)", "effort_estimate_days"),
    ("创建日期", "created_at"),
    ("实际完成日期", "actual_completion_date"),
    ("终止原因", "termination_reason"),
    ("佐证材料数", "_evidence_count"),
    ("评论数", "_comment_count"),
]

VALID_SOURCES = ["总经办", "售前", "销售", "客服", "研发", "工程", "生产", "研究院", "其他"]
VALID_STATUSES = ["待确认", "需求池", "待启动", "进行中", "暂停", "已完成", "已终止"]
VALID_PRIORITIES = ["P0", "P1", "P2", "P3"]
VALID_RATINGS = ["高", "中", "低", "未评", ""]


def _req_to_excel_row(r: dict) -> dict:
    """Requirement 字典 -> Excel 行（按 EXCEL_COLUMN_MAP 的中文列名）"""
    requester_name = USER_ID_TO_NAME.get(r.get("requester_id", ""), "")
    requested_owner_name = USER_ID_TO_NAME.get(r.get("requested_owner_id", ""), "")
    owner_ids = [i for i in r.get("owner_ids", []) if i]
    owner_name = "、".join(USER_ID_TO_NAME.get(i, "") for i in owner_ids)
    project = next((p["name"] for p in get_projects() if p["id"] == r.get("project_id", "")), "")
    tags_str = ", ".join(r.get("tags", []))
    evidence_link = r.get("evidence", [{}])[0].get("link", "") if r.get("evidence") else ""
    row = {
        "需求ID": r.get("id", ""),
        "标题": r.get("title", ""),
        "描述": r.get("description", ""),
        "部门": r.get("source", ""),
        "状态": r.get("status", ""),
        "优先级": r.get("priority", ""),
        "客户名": r.get("customer", ""),
        "提出人": requester_name,
        "建议责任人": requested_owner_name,
        "责任人": owner_name,
        "关联项目": project,
        "期望完成日期": r.get("requested_due_date", ""),
        "价值评分": r.get("value_rating", "未评"),
        "复杂度评分": r.get("complexity_rating", "未评"),
        "评分来源": r.get("rating_source", ""),
        "标签": tags_str,
        "佐证材料链接": evidence_link,
        "估算工作量(人天)": r.get("effort_estimate_days", 0),
        "创建日期": r.get("created_at", ""),
        "实际完成日期": r.get("actual_completion_date", ""),
        "终止原因": r.get("termination_reason", ""),
        "佐证材料数": len(r.get("evidence", [])),
        "评论数": len(r.get("comments", [])),
    }
    return row


def _excel_row_to_req(row, default_requester_id="U001") -> tuple[dict, list[str]]:
    """Excel 行 -> Requirement 字典，返回 (req, errors)"""
    errors = []

    title = str(row.get("标题", "")).strip()
    description = str(row.get("描述", "")).strip()
    source = str(row.get("部门", row.get("来源", "")) or "").strip()
    evidence_link = str(row.get("佐证材料链接", "")).strip()

    if not title:
        errors.append("标题为空")
    if not description:
        errors.append("描述为空")
    if not source:
        errors.append("部门为空")
    elif source not in VALID_SOURCES:
        errors.append(f"部门「{source}」不在可选部门中")
    if not evidence_link:
        errors.append("佐证材料链接为空（必填）")

    priority = str(row.get("优先级", "P2")).strip() or "P2"
    if priority not in VALID_PRIORITIES:
        errors.append(f"优先级「{priority}」不合法")

    status = str(row.get("状态", "需求池")).strip() or "需求池"
    if status not in VALID_STATUSES:
        errors.append(f"状态「{status}」不合法")

    value_rating = str(row.get("价值评分", "未评")).strip() or "未评"
    complexity_rating = str(row.get("复杂度评分", "未评")).strip() or "未评"
    if value_rating not in VALID_RATINGS:
        errors.append(f"价值评分「{value_rating}」不合法")
    if complexity_rating not in VALID_RATINGS:
        errors.append(f"复杂度评分「{complexity_rating}」不合法")

    # 日期校验
    due_date = str(row.get("期望完成日期", "")).strip()
    if due_date:
        try:
            datetime.strptime(due_date, "%Y-%m-%d")
        except Exception:
            errors.append(f"日期格式「{due_date}」非 YYYY-MM-DD")
            due_date = ""

    # 姓名匹配用户
    requester_name = str(row.get("提出人", "")).strip()
    requested_owner_name = str(row.get("建议责任人", "")).strip()
    owner_names = [n.strip() for n in str(row.get("责任人", "")).split("、") if n.strip()]
    project_name = str(row.get("关联项目", "")).strip()

    requester_id = USER_NAME_TO_ID.get(requester_name, default_requester_id) if requester_name else default_requester_id
    requested_owner_id = USER_NAME_TO_ID.get(requested_owner_name, "") if requested_owner_name else ""
    owner_ids = []
    for n in owner_names:
        uid = USER_NAME_TO_ID.get(n, "")
        if not uid:
            errors.append(f"责任人「{n}」未匹配到用户")
        else:
            owner_ids.append(uid)

    project_id = next((p["id"] for p in get_projects() if p["name"] == project_name), "") if project_name else ""

    tags_str = str(row.get("标签", "")).strip()
    tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []
    tags = tags[:5]

    rating_source = str(row.get("评分来源", "")).strip()
    if (value_rating != "未评" or complexity_rating != "未评") and not rating_source:
        rating_source = "人工"

    # ID 处理
    req_id = str(row.get("需求ID", "")).strip()

    req = {
        "id": req_id,
        "title": title,
        "description": description,
        "source": source,
        "source_ref": "",
        "customer": str(row.get("客户名", "")).strip(),
        "requester_id": requester_id,
        "requested_owner_id": requested_owner_id,
        "owner_ids": owner_ids,
        "project_id": project_id,
        "status": status,
        "priority": priority,
        "value_rating": value_rating,
        "complexity_rating": complexity_rating,
        "rating_source": rating_source,
        "tags": tags,
        "requested_due_date": due_date,
        "created_at": str(row.get("创建日期", "")).strip() or TODAY_STR,
        "actual_completion_date": str(row.get("实际完成日期", "")).strip(),
        "termination_reason": str(row.get("终止原因", "")).strip(),
        "effort_estimate_days": float(row.get("估算工作量(人天)", 0) or 0),
        "evidence": [{"type": "other", "label": evidence_link[:80], "link": evidence_link,
                      "uploaded_by": default_requester_id, "uploaded_at": TODAY_STR}] if evidence_link else [],
        "comments": [],
    }
    # 多执行人：导入后同样自动拆分子需求（n 执行人 ↔ n 子需求）
    _sync_sub_requirements(req)
    return req, errors


def page_management():
    st.markdown("## 📦 需求管理")
    st.caption("全需求列表 + Excel 导入/导出")

    tab_list, tab_import, tab_export = st.tabs(["📋 需求列表", "📥 Excel 导入", "📤 Excel 导出"])

    # ===== 列表 Tab =====
    with tab_list:
        reqs = get_requirements()
        st.markdown(f"**共 {len(reqs)} 条需求**")

        # 筛选
        with st.expander("🔍 筛选", expanded=False):
            f_col1, f_col2, f_col3, f_col4 = st.columns(4)
            with f_col1:
                # 从中心总览状态分布跳转过来时，预设状态筛选
                if "_pending_status_filter" in st.session_state:
                    st.session_state["mgmt_f_status"] = [st.session_state.pop("_pending_status_filter")]
                f_status = st.multiselect("状态", options=STATUS_LABELS, default=[], key="mgmt_f_status")
            with f_col2:
                f_source = st.multiselect("部门", options=SOURCE_LABELS, default=[])
            with f_col3:
                f_priority = st.multiselect("优先级", options=["P0", "P1", "P2", "P3"], default=[])
            with f_col4:
                f_owner = st.multiselect("执行人", options=[u["name"] for u in USERS], default=[])

            f_col5, f_col6 = st.columns([1, 2])
            with f_col5:
                f_project = st.multiselect("关联项目", options=[_project_display_name(p) for p in get_projects()], default=[], key="mgmt_f_project")
            with f_col6:
                f_keyword = st.text_input("关键词（标题/描述）", "")

        # 应用筛选
        filtered = []
        for r in reqs:
            if f_status and r.get("status") not in f_status: continue
            if f_source and r.get("source") not in f_source: continue
            if f_priority and r.get("priority") not in f_priority: continue
            if f_owner:
                owner_names = {USER_ID_TO_NAME.get(i, "") for i in r.get("owner_ids", []) if i}
                if not (owner_names & set(f_owner)): continue
            if f_project and _req_project_name(r) not in f_project: continue
            if f_keyword.strip():
                kw = f_keyword.strip().lower()
                if kw not in r.get("title", "").lower() and kw not in r.get("description", "").lower():
                    continue
            filtered.append(r)

        # 分页参数
        PAGE_SIZE = 20
        total_pages = max(1, (len(filtered) + PAGE_SIZE - 1) // PAGE_SIZE)
        page_key = "mgmt_page"
        if page_key not in st.session_state:
            st.session_state[page_key] = 0
        # 筛选条件变化时回到第一页
        filter_fp = (tuple(f_status or []), tuple(f_source or []), tuple(f_priority or []), tuple(f_owner or []), tuple(f_project or []), f_keyword.strip())
        if st.session_state.get("_last_filter_fp") != filter_fp:
            st.session_state[page_key] = 0
            st.session_state["_last_filter_fp"] = filter_fp
        page = st.session_state[page_key]
        page = max(0, min(page, total_pages - 1))
        start = page * PAGE_SIZE
        end = start + PAGE_SIZE
        page_items = filtered[start:end]

        st.caption(f"筛选后：{len(filtered)} 条 · 第 {page+1}/{total_pages} 页")

        # 用 HTML 表格展示（不用 dataframe，因为字段太多，HTML 更可控）
        if not filtered:
            st.info("没有匹配的需求")
        else:
            header_html = (
                '<div style="display:flex;flex-wrap:wrap;background:#1F4E79;color:white;padding:8px;border-radius:6px 6px 0 0;font-size:14px;font-weight:600;">'
                '<div style="width:90px;">ID</div>'
                '<div style="width:280px;">标题</div>'
                '<div style="width:70px;">状态</div>'
                '<div style="width:70px;">部门</div>'
                '<div style="width:110px;">关联项目</div>'
                '<div style="width:40px;">优先级</div>'
                '<div style="width:50px;">价值</div>'
                '<div style="width:50px;">复杂度</div>'
                '<div style="width:70px;">执行人</div>'
                '<div style="width:80px;">期望完成</div>'
                '<div style="width:60px;">操作</div>'
                '</div>'
            )
            st.markdown(header_html, unsafe_allow_html=True)
            for r in page_items:
                v = r.get("value_rating", "未评")
                c = r.get("complexity_rating", "未评")
                v_color = RATING_COLORS.get(v, "#BDBDBD")
                c_color = RATING_COLORS.get(c, "#BDBDBD")
                owner_name = _owner_names(r, "—")
                due = r.get("requested_due_date") or "—"
                # 过期标红
                due_display = due
                if due != "—":
                    try:
                        d = datetime.strptime(due, "%Y-%m-%d").date()
                        if d < TODAY and r.get("status") not in ["已完成", "已终止"]:
                            due_display = f'<span style="color:#E53935;font-weight:600;">{due}⚠</span>'
                    except Exception: pass
                row_html = (
                    f'<div style="display:flex;flex-wrap:wrap;padding:6px 8px;border-bottom:1px solid #EEE;font-size:13px;align-items:center;">'
                    f'<div style="width:90px;color:#666;">{r["id"]}</div>'
                    f'<div style="width:280px;">{r["title"][:35]}</div>'
                    f'<div style="width:70px;">{status_badge(r["status"])}</div>'
                    f'<div style="width:70px;color:#555;">{r.get("source","")}</div>'
                    f'<div style="width:110px;color:#555;">{_req_project_name(r) or "—"}</div>'
                    f'<div style="width:40px;">{priority_badge(r.get("priority","未知"))}</div>'
                    f'<div style="width:50px;color:{v_color};font-weight:600;">{v}</div>'
                    f'<div style="width:50px;color:{c_color};font-weight:600;">{c}</div>'
                    f'<div style="width:70px;">{owner_name}</div>'
                    f'<div style="width:80px;">{due_display}</div>'
                    f'</div>'
                )
                info_col, act_col = st.columns([1, 0.08], gap="small")
                with info_col:
                    st.markdown(row_html, unsafe_allow_html=True)
                with act_col:
                    if st.button("查看", key=f"mgmt_view_{r['id']}", use_container_width=True):
                        st.session_state["selected_req_id"] = r["id"]
                        st.session_state["_pending_nav"] = True
                        st.session_state["_pending_nav_target"] = "📄 需求详情"
                        st.rerun()
            # 分页导航（始终显示）
            nav_c1, nav_c2, nav_c3, nav_c4, nav_c5, nav_c6 = st.columns([1, 1, 1, 1, 1, 1])
            with nav_c2:
                if st.button("◀ 上一页", disabled=(page == 0), use_container_width=True, key="mgmt_prev"):
                    st.session_state[page_key] = max(0, page - 1)
                    st.rerun()
            with nav_c3:
                jump_to = st.number_input(
                    "跳转", min_value=1, max_value=total_pages, value=page + 1,
                    step=1, key="mgmt_jump", label_visibility="collapsed",
                )
                if jump_to != page + 1:
                    st.session_state[page_key] = jump_to - 1
                    st.rerun()
            with nav_c4:
                st.markdown(
                    f'<div style="text-align:center;padding-top:8px;color:#666;font-size:13px;">'
                    f'第 {page+1} / {total_pages} 页</div>',
                    unsafe_allow_html=True,
                )
            with nav_c5:
                if st.button("下一页 ▶", disabled=(page >= total_pages - 1), use_container_width=True, key="mgmt_next"):
                    st.session_state[page_key] = min(total_pages - 1, page + 1)
                    st.rerun()

    # ===== 导出 Tab =====
    with tab_export:
        st.markdown("### 📤 导出为 Excel")
        st.caption("导出当前所有需求（含全部字段）。evidence 和 comments 数组序列化为 JSON 字符串。")

        exp_col1, exp_col2 = st.columns([1, 2])
        with exp_col1:
            if st.button("📊 生成 Excel 文件", type="primary", use_container_width=True):
                try:
                    import pandas as pd
                    import io
                    rows = [_req_to_excel_row(r) for r in get_requirements()]
                    df = pd.DataFrame(rows, columns=[c[0] for c in EXCEL_COLUMN_MAP])
                    buf = io.BytesIO()
                    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                        df.to_excel(writer, index=False, sheet_name="需求列表")
                    buf.seek(0)
                    st.session_state["_export_bytes"] = buf.getvalue()
                    st.success(f"✅ 生成成功，{len(rows)} 条需求")
                except Exception as e:
                    st.error(f"导出失败：{e}")

        with exp_col2:
            xlsx_bytes = st.session_state.get("_export_bytes")
            if xlsx_bytes:
                fname = f"requirements_export_{TODAY_STR.replace('-','')}.xlsx"
                st.download_button(
                    label=f"⬇ 下载 {fname}",
                    file_name=fname,
                    data=xlsx_bytes,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

        st.markdown("---")
        st.markdown("##### 模板下载（用于导入）")
        st.caption("空白模板，含字段说明与示例行。填好后到「📥 导入」Tab 上传。")
        if st.button("📋 下载导入模板"):
            try:
                import pandas as pd
                import io
                template_data = [
                    {"标题": "示例：SMT线增加扫码", "描述": "客户要求入料口加扫码，用于追溯", "部门": "销售",
                     "状态": "需求池", "优先级": "P2", "客户名": "示例客户",
                     "提出人": "赵新宇", "建议责任人": "", "责任人": "",
                     "关联项目": "SMT视觉检测线", "期望完成日期": "2026-09-30",
                     "价值评分": "中", "复杂度评分": "低", "评分来源": "人工",
                     "标签": "视觉检测设备, 软件, 新功能",
                     "佐证材料链接": "https://example.com/email-forwarded",
                     "估算工作量(人天)": 8, "创建日期": TODAY_STR,
                     "实际完成日期": "", "终止原因": "",
                     "佐证材料数": 0, "评论数": 0},
                    {"标题": "", "描述": "", "部门": "", "状态": "", "优先级": "",
                     "客户名": "", "提出人": "", "建议责任人": "", "责任人": "",
                     "关联项目": "", "期望完成日期": "", "价值评分": "", "复杂度评分": "",
                     "评分来源": "", "标签": "", "佐证材料链接": "",
                     "估算工作量(人天)": "", "创建日期": "", "实际完成日期": "",
                     "终止原因": "", "佐证材料数": "", "评论数": ""},
                ]
                df = pd.DataFrame(template_data, columns=[c[0] for c in EXCEL_COLUMN_MAP])
                buf = io.BytesIO()
                with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                    df.to_excel(writer, index=False, sheet_name="导入模板")
                buf.seek(0)
                st.download_button(
                    label="⬇ 下载 模板.xlsx",
                    file_name="requirements_template.xlsx",
                    data=buf.getvalue(),
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            except Exception as e:
                st.error(f"模板生成失败：{e}")

    # ===== 导入 Tab =====
    with tab_import:
        st.markdown("### 📥 从 Excel 导入")
        st.caption("上传 .xlsx 文件，自动校验并批量导入。建议先下载模板按格式填写。")

        allow_overwrite = st.checkbox("允许覆盖已存在的需求ID", value=False)
        uploaded = st.file_uploader("选择 Excel 文件", type=["xlsx", "xls"], key="mgmt_upload")

        if uploaded is not None:
            try:
                # 解析结果按文件内容缓存：上传后每次 rerun 不再重复 pd.read_excel / 逐行校验
                preview_rows, parsed_reqs = _cached_excel_parse(
                    uploaded.getvalue(), st.session_state.current_user_id
                )
                st.success(f"✅ 文件解析成功，共 {len(parsed_reqs)} 行")

                # 显示原始数据预览（前 5 行）
                with st.expander("原始数据预览（前 5 行）", expanded=False):
                    st.dataframe(preview_rows, use_container_width=True)

                # ID 冲突检查（依赖当前库与「允许覆盖」勾选，必须实时计算，不能进缓存）
                existing_ids = {r["id"] for r in get_requirements()}
                parsed_out = []
                for req, errs in parsed_reqs:
                    conflict = False
                    if req["id"] and req["id"] in existing_ids and not allow_overwrite:
                        errs.append(f"ID「{req['id']}」已存在（未勾选允许覆盖）")
                        conflict = True
                    parsed_out.append((req, errs, conflict))

                ok_reqs = [(req, conflict) for req, errs, conflict in parsed_out if not errs]
                err_reqs = [(idx + 2, req, errs) for idx, (req, errs, conflict) in enumerate(parsed_out) if errs]

                # 汇总
                m_col1, m_col2, m_col3 = st.columns(3)
                with m_col1:
                    st.metric("可导入", len(ok_reqs))
                with m_col2:
                    st.metric("有问题", len(err_reqs))
                with m_col3:
                    existing_count = sum(1 for req, c in ok_reqs if c)
                    st.metric("其中覆盖", existing_count)

                # 错误报告
                if err_reqs:
                    st.warning(f"⚠ {len(err_reqs)} 行有问题，将跳过：")
                    err_rows = []
                    for row_num, req, errs in err_reqs:
                        err_rows.append({"Excel 行号": row_num, "标题": req["title"][:30], "问题": "；".join(errs)})
                    st.dataframe(err_rows, use_container_width=True)

                    # 错误报告下载（保持原交互：直接一个下载按钮）
                    import pandas as pd_err
                    import io as io_err
                    err_buf = io_err.BytesIO()
                    pd_err.DataFrame(err_rows).to_excel(err_buf, index=False, sheet_name="错误报告")
                    err_buf.seek(0)
                    st.download_button(
                        label="⬇ 下载错误报告",
                        file_name=f"import_errors_{TODAY_STR.replace('-','')}.xlsx",
                        data=err_buf.getvalue(),
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )

                # 导入确认
                if ok_reqs:
                    st.markdown("---")
                    st.markdown(f"##### 待导入 {len(ok_reqs)} 条（预览前 5 条）")
                    for req, _ in ok_reqs[:5]:
                        st.markdown(
                            f'- {req["id"] or "（新ID）"} 「{req["title"]}」 · 部门:{req["source"]} · 状态:{req["status"]}',
                        )
                    if len(ok_reqs) > 5:
                        st.caption(f"...还有 {len(ok_reqs)-5} 条")

                    if st.button(f"✅ 确认导入 {len(ok_reqs)} 条", type="primary"):
                        inserted = 0
                        overwritten = 0
                        for req, conflict in ok_reqs:
                            if not req["id"]:
                                req["id"] = gen_req_id()
                            if conflict:
                                overwritten += 1
                            else:
                                inserted += 1
                            save_requirement(req)
                        st.session_state["_flash"] = {"type": "success", "text": f"✅ 导入完成：新增 {inserted} 条，覆盖 {overwritten} 条"}
                        st.rerun()
                else:
                    st.error("没有可导入的数据，请先修正错误")

            except Exception as e:
                st.error(f"文件解析失败：{e}")
                st.caption("请确认：①文件是 .xlsx 格式；②第一行是中文列名（建议先下载模板参考）；③「标题」「描述」「部门」「佐证材料链接」是必填列。")


# ============================================================
# 项目管理面板（复用组件）
# ============================================================

def _render_project_management(selected_pid: str = "", p_info: dict | None = None, req_count: int = 0):
    """统一的项目管理面板：新建 / 修改名称 / 删除"""
    # 提交成功后清空表单：必须在控件实例化前清空，否则报「cannot be modified after widget is instantiated」
    if st.session_state.pop("_clear_new_proj_form", False):
        st.session_state["mgmt_new_name"] = ""
        st.session_state["mgmt_new_group"] = "（请选择）"  # 下拉框不能置空串，回到占位项
        st.session_state["mgmt_new_highlight"] = ""
    if st.session_state.pop("_clear_rename_proj_form", False) and selected_pid:
        st.session_state[f"mgmt_rename_name_{selected_pid}"] = ""
        # 所属大项目下拉框回到当前已保存值（不产生待保存变更）
        cur = ((p_info or {}).get("code") or "").strip()
        st.session_state[f"mgmt_rename_group_{selected_pid}"] = cur or "（未分组）"
        st.session_state[f"mgmt_rename_highlight_{selected_pid}"] = ""

    with st.expander("🛠 项目管理", expanded=False):

        # ---- 新建项目 ----
        st.markdown("##### ➕ 新建项目")
        with st.form("mgmt_new_project"):
            new_name = st.text_input("项目名称 *", placeholder="例如：XX型号全自动测试设备", key="mgmt_new_name")
            new_group = st.selectbox("所属大项目 *", options=["（请选择）"] + PROJECT_GROUPS, key="mgmt_new_group")
            new_highlight = st.text_input("核心亮点/定制需求（选填）", placeholder="例如：支持在线视觉检测", key="mgmt_new_highlight")
            if st.form_submit_button("✅ 创建项目", type="primary", use_container_width=True):
                if not new_name.strip():
                    st.error("项目名称不能为空")
                elif new_group == "（请选择）":
                    st.error("请选择所属大项目")
                else:
                    existing = get_projects()
                    if any(p["name"] == new_name.strip() for p in existing):
                        st.error(f"项目名称「{new_name.strip()}」已存在")
                    else:
                        seq = db.next_project_seq()
                        new_proj = {"id": f"P{seq:03d}", "name": new_name.strip(), "code": new_group, "highlight": new_highlight.strip()}
                        db.save_project(new_proj)
                        st.session_state.projects = db.load_all_projects()
                        _invalidate_data_caches()
                        _mark_own_write()
                        # 提交成功后清空表单（下次渲染前统一清）
                        st.session_state["_clear_new_proj_form"] = True
                        st.session_state["_flash"] = {"type": "success", "text": f"✅ 已创建项目：{new_proj['id']} 「{_project_display_name(new_proj)}」"}
                        st.rerun()

        # ---- 修改与删除（仅选中项目时显示） ----
        if selected_pid and p_info:
            st.markdown("---")
            st.markdown(f"##### ✏️ 修改「{_project_display_name(p_info)}」")

            # 修改名称 / 所属大项目 / 核心亮点（名称留空=不修改）
            with st.form("mgmt_rename_project"):
                rename_name = st.text_input("新项目名称", placeholder=f"当前：{p_info['name']}", key=f"mgmt_rename_name_{selected_pid}")
                cur_code = (p_info.get("code") or "").strip()
                group_options = ["（未分组）"] + list(PROJECT_GROUPS)
                if cur_code and cur_code not in group_options:
                    group_options.append(cur_code)  # 历史遗留大项目名，保留为选项避免保存时被悄悄改写
                rename_group = st.selectbox(
                    "所属大项目",
                    options=group_options,
                    index=group_options.index(cur_code) if cur_code else 0,
                    key=f"mgmt_rename_group_{selected_pid}",
                )
                rename_highlight = st.text_input("核心亮点/定制需求（选填）", value=p_info.get("highlight", ""), placeholder="例如：支持在线视觉检测", key=f"mgmt_rename_highlight_{selected_pid}")
                if st.form_submit_button("💾 保存修改", use_container_width=True):
                    new_name_val = rename_name.strip() if rename_name.strip() else p_info["name"]
                    # 检查重名（排除自身）
                    existing = get_projects()
                    name_conflict = new_name_val != p_info["name"] and any(p["name"] == new_name_val and p["id"] != selected_pid for p in existing)
                    if name_conflict:
                        st.error(f"项目名称「{new_name_val}」已被其他项目使用")
                    else:
                        p_info["name"] = new_name_val
                        p_info["code"] = "" if rename_group == "（未分组）" else rename_group
                        p_info["highlight"] = rename_highlight.strip()
                        db.save_project(p_info)
                        st.session_state.projects = db.load_all_projects()
                        _invalidate_data_caches()
                        _mark_own_write()
                        # 提交成功后清空表单（下次渲染前统一清）
                        st.session_state["_clear_rename_proj_form"] = True
                        st.session_state["_flash"] = {"type": "success", "text": f"✅ 已更新项目「{_project_display_name(p_info)}」"}
                        st.rerun()

            # 删除项目
            st.markdown("---")
            st.markdown("##### 🗑 删除项目")
            st.warning(f"⚠ 确定要删除项目「{_project_display_name(p_info)}」吗？")
            st.caption(f"该项目关联 {req_count} 条需求，删除后这些需求的「关联项目」将变为空。此操作不可恢复。")
            confirm_del = st.checkbox("我确认删除此项目", key=f"mgmt_confirm_del_{selected_pid}")
            if st.button("🗑 确认删除", type="secondary", disabled=not confirm_del, key=f"mgmt_del_{selected_pid}"):
                db.delete_project(selected_pid)
                st.session_state.projects = db.load_all_projects()
                _invalidate_data_caches()
                _mark_own_write()
                st.session_state["_pending_proj_reset"] = True
                st.session_state["_flash"] = {"type": "success", "text": f"✅ 已删除项目「{_project_display_name(p_info)}」"}
                st.rerun()


# ============================================================
# 页面：项目视图（v0.2.4 新增）- 对应 PRD v1.x §F2.2 项目负责人视图
# ============================================================

def _render_project_dashboard(reqs, heading: str):
    """渲染一个项目（或一个大项目聚合）的看板：指标 + 风险 + 状态分组 + 执行人统计。"""
    st.markdown("---")
    st.markdown(f"### {heading}")
    st.caption(f"共 {len(reqs)} 条需求")

    # 关键指标
    active = [r for r in reqs if r.get("status") in ("需求池", "待启动", "进行中", "暂停")]
    completed = [r for r in reqs if r.get("status") == "已完成"]
    terminated = [r for r in reqs if r.get("status") == "已终止"]
    unassigned = [r for r in reqs if r.get("status") == "需求池" and not r.get("owner_ids")]
    overdue = []
    for r in reqs:
        if r.get("requested_due_date") and r.get("status") not in ("已完成", "已终止"):
            try:
                d = datetime.strptime(r["requested_due_date"], "%Y-%m-%d").date()
                if d < TODAY:
                    overdue.append(r)
            except Exception:
                pass

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("需求总数", len(reqs))
    col2.metric("在跑", len(active))
    col3.metric("已完成", len(completed))
    col4.metric("已终止", len(terminated))
    col5.metric("未指派", len(unassigned))

    # 风险提醒
    if overdue:
        st.error(f"⚠ {len(overdue)} 条已超期：{', '.join(r['id'] for r in overdue[:5])}")
    if unassigned:
        st.warning(f"⚠ {len(unassigned)} 条未指派执行人：{', '.join(r['id'] for r in unassigned[:5])}")

    # 按状态分组展示
    st.markdown("---")
    st.markdown("### 按状态分组")
    status_groups = {s: [] for s in STATUS_LABELS}
    for r in reqs:
        s = r.get("status", "需求池")
        if s in status_groups:
            status_groups[s].append(r)

    cols = st.columns(len(STATUS_LABELS))
    for col, (status, items) in zip(cols, status_groups.items()):
        with col:
            st.markdown(
                f'<div style="background:{STATUS_COLORS[status]}11;padding:6px;border-radius:6px 6px 0 0;border-left:3px solid {STATUS_COLORS[status]};">'
                f'<span style="font-weight:600;color:{STATUS_COLORS[status]};font-size:12px;">{status}</span>'
                f'<span style="float:right;color:#777;font-size:11px;">{len(items)}</span></div>',
                unsafe_allow_html=True,
            )
            for r in items[:8]:
                owner = _owner_names(r)
                due = r.get("requested_due_date", "—")
                overdue_mark = ""
                if due != "—":
                    try:
                        dd = datetime.strptime(due, "%Y-%m-%d").date()
                        if dd < TODAY and r.get("status") not in ("已完成", "已终止"):
                            overdue_mark = ' <span style="color:#E53935;">⚠</span>'
                    except Exception:
                        pass
                st.markdown(
                    f'<div style="background:white;border:1px solid #EEE;padding:6px;margin:4px 0;font-size:11px;">'
                    f'{priority_badge(r.get("priority", "未知"))}'
                    f' <b>{r["id"]}</b>{overdue_mark}'
                    f'<div style="margin-top:3px;">{r["title"][:25]}</div>'
                    f'<div style="color:#666;font-size:10px;margin-top:2px;">👤 {owner} · 📅 {due}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            if len(items) > 8:
                st.caption(f"...还有 {len(items)-8} 条")

    # 按执行人统计
    st.markdown("---")
    st.markdown("### 按执行人统计")
    owner_counts = Counter()
    for r in reqs:
        ids = [i for i in r.get("owner_ids", []) if i]
        if ids:
            for i in ids:
                owner_counts[USER_ID_TO_NAME.get(i, "未知")] += 1
        else:
            owner_counts["未指派"] += 1
    for name, cnt in owner_counts.most_common():
        st.markdown(
            f'<div><span style="display:inline-block;width:80px;">{name}</span>'
            f'<span style="display:inline-block;background:#1E88E5;height:12px;width:{cnt*15}px;border-radius:2px;vertical-align:middle;margin:0 6px;"></span>'
            f'<b>{cnt}</b></div>',
            unsafe_allow_html=True,
        )


# 标题 → 大项目 自动归类标签（仅作用于未关联小项目的需求；业务标签优先于 AI，全都没有归「其他」）
_TITLE_TAG_TO_GROUP = [("A", "EQ-200"), ("B", "EQ-400"), ("C", "EQ-700"), ("AI", "研发AI赋能")]


def _req_effective_group(r: dict, pid_to_code: dict[str, str]) -> str:
    """需求生效的大项目归属（仅「大项目视图」聚合用，不写回数据）：

    - 关联了小项目 → 沿用该项目的「所属大项目」（code；无 code / 项目已删 → 未分组）；
    - 未关联小项目 → 按标题自动归类：含 A/B/C（多标签时取标题中最早出现的）→ 对应大项目；
      业务标签都没有而含 AI → 研发AI赋能（业务标签与 AI 同时出现时业务标签优先）；
      全都没有 → 其他。
    """
    pid = r.get("project_id", "")
    if pid:
        return pid_to_code.get(pid, "") or "未分组"
    title = (r.get("title") or "").upper()
    hits = sorted((title.find(tag), grp) for tag, grp in _TITLE_TAG_TO_GROUP[:3] if tag in title)
    if hits:
        return hits[0][1]
    if "AI" in title:
        return "研发AI赋能"
    return "其他"


def _group_projects(all_projects):
    """按「所属大项目」（code 字段）分组：五个固定大项目恒在（即使暂无小项目），
    历史遗留的自定义大项目名排在后面（可通过「修改项目」归入固定项），未分组的放最后。"""
    groups: dict[str, list[dict]] = {g: [] for g in PROJECT_GROUPS}
    for p in all_projects:
        code = (p.get("code") or "").strip()
        if code in groups:
            groups[code].append(p)
        elif code:
            groups.setdefault(code, []).append(p)  # 历史遗留大项目，保留展示避免数据被悄悄改写
        else:
            groups.setdefault("未分组", []).append(p)
    # 「未分组」固定排在最后
    ungrouped = groups.pop("未分组", None)
    if ungrouped:
        groups["未分组"] = ungrouped
    return groups


def _render_group_view(all_projects):
    """大项目视图：A/B/C/D 概览卡片 + 选择一个大项目看聚合看板（点击卡片下方按钮即自动选中）。"""
    groups = _group_projects(all_projects)
    # 需求 → 大项目 归类：关联小项目沿用其归属；未关联的按标题自动归类（A/B/C/AI→其他），
    # 保证每条需求在大项目视图都有落点（此前未关联的需求会从视图里凭空消失）
    pid_to_code = {p["id"]: (p.get("code") or "").strip() for p in all_projects}
    reqs_by_group: dict[str, list[dict]] = {}
    for r in get_requirements():
        reqs_by_group.setdefault(_req_effective_group(r, pid_to_code), []).append(r)
    for extra in reqs_by_group:  # 孤儿需求（关联的小项目已被删）兜底出「未分组」卡片
        groups.setdefault(extra, [])
    group_labels = list(groups.keys())

    # 卡片点击写入的选择值可能因分组变化而失效（如项目被删），兜底回第一个
    if st.session_state.get("proj_group_sel") not in group_labels:
        st.session_state["proj_group_sel"] = group_labels[0]

    # 大项目卡片网格：每行最多 4 张，超出自动换行（大项目多时每张仍保有可读宽度）
    CARDS_PER_ROW = 4
    st.markdown("### 大项目概览")
    for row_start in range(0, len(group_labels), CARDS_PER_ROW):
        row_groups = group_labels[row_start:row_start + CARDS_PER_ROW]
        cols = st.columns(len(row_groups))
        for col, g in zip(cols, row_groups):
            gp_reqs = reqs_by_group.get(g, [])
            active = sum(1 for r in gp_reqs if r.get("status") in ("需求池", "待启动", "进行中", "暂停"))
            done = sum(1 for r in gp_reqs if r.get("status") == "已完成")
            # 当前选中的大项目卡片高亮显示
            selected = st.session_state.get("proj_group_sel") == g
            border_color = "#1E88E5" if selected else "#E0E0E0"
            bg_color = "#E3F2FD" if selected else "#FFFFFF"
            with col:
                st.markdown(
                    f'<div style="border:2px solid {border_color};background:{bg_color};'
                    f'border-radius:8px;padding:10px;text-align:center;">'
                    f'<div style="font-size:18px;font-weight:700;color:#1F4E79;">{g}</div>'
                    f'<div style="color:#666;font-size:12px;margin-top:4px;">{len(groups[g])} 个小项目</div>'
                    f'<div style="color:#555;font-size:12px;margin-top:2px;">需求 {len(gp_reqs)} · 在跑 {active} · 完成 {done}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                if st.button(
                    "🔍 查看详情",
                    key=f"group_card_{g}",
                    use_container_width=True,
                    type="primary" if selected else "secondary",
                    help=f"查看大项目「{g}」的聚合看板",
                ):
                    st.session_state["proj_group_sel"] = g
                    st.rerun()

    st.markdown("---")
    st.caption("💡 点击上方卡片中的「🔍 查看详情」切换下方看板")
    # 选中项由卡片点击控制（原下拉框已按需求隐藏）
    sel_group = st.session_state["proj_group_sel"]
    gp_reqs = reqs_by_group.get(sel_group, [])
    _render_project_dashboard(gp_reqs, f"{sel_group}（{len(groups[sel_group])} 个小项目）")
    # 项目管理已提为同级标签（「⚙️ 管理项目」），此处不再重复渲染


def _render_single_project_view(all_projects):
    """单项目视图：「选择项目」仅列出当前大项目下的小项目（大项目在「大项目视图」中选定）。"""
    groups = _group_projects(all_projects)
    group_labels = list(groups.keys())
    # 兜底：大项目被删/未选过时回第一个
    if st.session_state.get("proj_group_sel") not in group_labels:
        st.session_state["proj_group_sel"] = group_labels[0]
    sel_group = st.session_state["proj_group_sel"]
    group_projects = groups[sel_group]

    st.caption(f"当前大项目：**{sel_group}**（在「大项目视图」中点击卡片切换）")

    # 固定大项目恒在卡片列表中，可能暂无小项目
    if not group_projects:
        st.info(f"大项目「{sel_group}」下暂无小项目，请到「⚙️ 管理项目」新建。")
        return

    project_options = {p["id"]: _project_display_name(p) for p in group_projects}
    # 删除项目后重置 selectbox
    if st.session_state.pop("_pending_proj_reset", False):
        st.session_state.proj_view_pid = list(project_options.keys())[0]
    # 切换大项目后，原选中的小项目可能不在当前大项目下，兜底回第一个
    if st.session_state.get("proj_view_pid") not in project_options:
        st.session_state.proj_view_pid = list(project_options.keys())[0]
    selected_pid = st.selectbox(
        "选择项目（仅当前大项目）",
        options=list(project_options.keys()),
        format_func=lambda x: project_options.get(x, x),
        key="proj_view_pid",
    )
    if not selected_pid:
        return

    project_reqs = [r for r in get_requirements() if r.get("project_id") == selected_pid]
    p_info = next((p for p in all_projects if p["id"] == selected_pid), None)
    if not p_info:
        st.warning("项目不存在")
        return

    heading = _project_display_name(p_info)
    if (p_info.get("code") or "").strip():
        heading += f" · 大项目 {(p_info.get('code') or '').strip()}"
    _render_project_dashboard(project_reqs, heading)
    # 项目管理已提为同级标签（「⚙️ 管理项目」），此处不再重复渲染


def page_project_view():
    st.markdown("## 📂 项目视图")
    st.caption(
        "项目负责人专用，按「关联项目」标签筛选 · 未关联小项目的需求按标题自动归入大项目"
        "（A/B/C → 对应大项目，否则含 AI → 研发AI赋能，都不是 → 其他）"
    )

    all_projects = get_projects()

    # 横向标签菜单（用户要求：参考客诉系统系统评估页的 Tab 样式）。
    # 原来的 st.radio（大项目视图 / 单项目视图）改为标签条，并新增「管理项目」独立入口——
    # 此前项目管理是拼在两个视图底部的一段（两个视图各自调用 _render_project_management），
    # 内容重复且不好找；现在提为同级标签，三个视图互斥、各管一摊。
    _mode = _nav_tabs(
        [("group", "🏢 大项目视图"), ("single", "📄 单项目视图"), ("manage", "⚙️ 管理项目")],
        state_key="proj_view_mode_tab",
    )

    if not all_projects and _mode != "manage":
        st.info("暂无项目，请切换到「⚙️ 管理项目」新建")
        return

    if _mode == "group":
        _render_group_view(all_projects)
    elif _mode == "single":
        _render_single_project_view(all_projects)
    else:
        _render_project_management()


# ============================================================
# 页面：新建需求（含 A1-A4）
# ============================================================

def _reset_new_requirement_form():
    """清空表单：将各控件 session_state 重置为默认值。必须在表单渲染前调用（否则会报 widget 已实例化）。"""
    # 文本输入 / 文本域 / 链接
    for k in ["req_title", "req_description", "req_customer",
              "evidence_link_1", "evidence_link_2"]:
        st.session_state[k] = ""
    # 下拉框回到“未选择”，不要把“其他”或第一位主管伪装成 AI 结果。
    st.session_state.req_source = "（请选择）"
    st.session_state.req_priority = PRIORITY_LABELS[-1]  # 未知
    st.session_state.req_owner = "（请选择）"
    st.session_state.req_project = "（无）"
    st.session_state.req_stage = "（请选择）"
    st.session_state.req_due_year = ""
    st.session_state.req_due_month = "（请选择）"
    st.session_state.req_due_day = ""
    st.session_state.form_value_rating = "未评"
    st.session_state.form_complexity_rating = "未评"
    # 文件上传：不能直接赋值，通过更换 key（递增版本号）来重置
    st.session_state.req_uploader_version = st.session_state.get("req_uploader_version", 0) + 1
    # 标签
    for tag in ALL_TAGS:
        st.session_state[f"tag_{tag}"] = False
    # 临时状态
    for k in ["form_values", "last_extract_result", "last_tag_result", "last_clarity_result", "last_sim_result", "a1_owner_hint"]:
        st.session_state.pop(k, None)
    # 填写计时基准也一并清掉：下一次填写从重新打开表单起算（阶段效率埋点）
    st.session_state.pop("_req_form_open_dt", None)


def page_new_requirement():
    # 填写计时（阶段效率埋点）：首次进入本页起算，setdefault 保证后续 rerun
    # （AI 抽取、换控件、传附件）不重置基准；提交后由 _reset_new_requirement_form 清零。
    # 用户离开再回来会沿用旧基准导致虚高——统计端以 4 小时为界丢弃（见提交处）。
    st.session_state.setdefault("_req_form_open_dt", datetime.now())
    st.markdown("## ➕ 新建需求")

    st.markdown("#### 步骤 1：粘贴原始信息（聊天 / 邮件 / 描述）")
    raw_input = st.text_area(
        "原始信息",
        height=150,
        placeholder="例如：客户示例客户C集成的客户工程师昨天在群里反馈，说我们的测试设备在高速贴装时视觉定位精度不稳，部分 IC 偏移到 0.05mm，希望我们能优化到 0.02mm，下周三之前给方案。",
        label_visibility="collapsed",
    )
    st.caption("💡 习惯语音输入的话：可先在微信/QQ 等社交软件里发语音，长按转文字后复制，粘贴到此处即可。")

    col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 4])
    with col_btn1:
        run_extract = st.button("🤖 AI 智能抽取", type="primary", disabled=not raw_input.strip())
    with col_btn2:
        use_template = st.button("📋 手动表单")

    if run_extract:
        with st.spinner("AI 正在抽取字段…"):
            result = ai_helper.extract_requirement_fields(
                user_input=raw_input,
                provider=st.session_state.provider,
                api_key=st.session_state.api_key,
                model=st.session_state.model or None,
            )
        st.session_state["last_extract_result"] = {
            "success": result.success,
            "data": result.data,
            "raw": result.raw,
            "error": result.error,
            "latency_ms": result.latency_ms,
            "model": result.model,
        }
        # 标签推荐已合并进 A1 抽取（recommended_tags），不再单独调用 A4
        if result.success and result.data:
            _rec_tags = result.data.get("recommended_tags", [])
            st.session_state["last_tag_result"] = {
                "success": True,
                "data": [{"tag": t, "confidence": 1.0} for t in _rec_tags],
            }
            # 初始化表单值
            st.session_state["form_values"] = result.data
            # 同步到表单 widget 的 session_state key
            # （表单字段已带 key，key 存在时 value/index 参数会被 Streamlit 忽略，必须直接写 session_state）
            _d = result.data
            st.session_state["req_title"] = _d.get("title", "")
            st.session_state["req_description"] = _d.get("description", "")
            st.session_state["req_customer"] = _d.get("customer", "")
            _due = _d.get("requested_due_date", "")
            st.session_state["req_due_year"] = ""
            st.session_state["req_due_month"] = "（请选择）"
            st.session_state["req_due_day"] = ""
            if _due:
                try:
                    _y, _m, _dd = (int(x) for x in _due.split("-"))
                    st.session_state["req_due_year"] = str(_y)
                    st.session_state["req_due_month"] = f"{_m}月"
                    st.session_state["req_due_day"] = str(_dd)
                except Exception:
                    pass
            st.session_state["req_source"] = _d.get("source") if _d.get("source") in SOURCE_LABELS else "（请选择）"
            _pri = _d.get("priority_hint", "未知")
            st.session_state["req_priority"] = _pri if _pri in PRIORITY_LABELS else "未知"
            # 建议责任人：只有原文包含唯一的完整主管姓名时才自动选中，避免“客户工程师”等称呼误匹配。
            st.session_state["req_owner"] = "（请选择）"
            st.session_state.pop("a1_owner_hint", None)
            _requested_owner = _d.get("requested_owner", "")
            if _requested_owner:
                _heads = [u for u in USERS if u["role"] == "主管"]
                _matched = [u["name"] for u in _heads if u["name"] in _requested_owner]
                if len(_matched) == 1:
                    st.session_state["req_owner"] = _matched[0]
                else:
                    st.session_state["a1_owner_hint"] = _requested_owner
            # AI 建议的价值/复杂度评分（「未知」已在 ai_helper 归一化为「未评」）
            _vr = _d.get("value_rating", "未评")
            st.session_state["form_value_rating"] = _vr if _vr in ("高", "中", "低", "未评") else "未评"
            _cr = _d.get("complexity_rating", "未评")
            st.session_state["form_complexity_rating"] = _cr if _cr in ("高", "中", "低", "未评") else "未评"

    last = st.session_state.get("last_extract_result")
    if last:
        if last["success"]:
            st.success(f"✅ AI 抽取完成 · 耗时 {last['latency_ms']}ms · 模型 {last['model']}")
            _uncertain = (last.get("data") or {}).get("uncertain_fields") or []
            _field_labels = {
                "title": "标题", "description": "描述", "source": "部门", "customer": "客户名",
                "requested_due_date": "期望完成日期", "priority_hint": "优先级", "requested_owner": "责任人", "tags": "标签",
            }
            if _uncertain:
                st.warning("请重点确认：" + "、".join(_field_labels.get(item, item) for item in _uncertain))
            else:
                st.caption("AI 已完成首轮预填；提交前请以原始材料核对字段，不确定项会保持为空。")
        else:
            st.error(f"❌ AI 抽取失败：{last['error']}")
            st.info("👉 您仍可使用下方手动表单填写")

    if st.session_state.get("a1_owner_hint"):
        st.info(f"AI 在原文中识别到责任人提及“{st.session_state['a1_owner_hint']}”，但未与系统主管名单精确匹配，请在下方手动确认。")

    # 表单（无论 AI 是否成功都显示，便于用户修改）
    # 处理「提交后清空表单」标记（必须在表单渲染前执行，避免 widget 已实例化报错）
    if st.session_state.pop("_clear_form", False):
        _reset_new_requirement_form()

    form_values = st.session_state.get("form_values") or {}

    st.markdown("---")
    st.markdown("#### 步骤 2：确认 / 修改字段")

    uploader_version = st.session_state.get("req_uploader_version", 0)

    # 关联项目 + 项目阶段（放在表单外，便于「阶段」随「项目」实时禁用/启用）
    proj_col1, proj_col2 = st.columns(2)
    with proj_col1:
        project_name = st.selectbox(
            "关联项目",
            options=["（无）"] + [_project_display_name(p) for p in get_projects()],
            key="req_project",
        )
    with proj_col2:
        project_stage = st.selectbox(
            "项目阶段",
            options=["（请选择）"] + PROJECT_STAGES,
            key="req_stage",
            disabled=(project_name == "（无）"),
        )
    st.caption(
        "💡 关联项目选「（无）」时，大项目视图按标题自动归类：含 A/B/C → 对应大项目"
        "（多标签取最先出现的）；否则含 AI → 研发AI赋能；都不是 → 其他"
    )

    with st.form("req_form"):
        col_l, col_r = st.columns(2)
        with col_l:
            title = st.text_input("标题 *", value=form_values.get("title", ""), key="req_title")
            source_options = ["（请选择）"] + SOURCE_LABELS
            source_default = 0
            if form_values.get("source") in SOURCE_LABELS:
                source_default = source_options.index(form_values.get("source"))
            source = st.selectbox(
                "部门 *",
                options=source_options,
                index=source_default,
                key="req_source",
            )
            customer = st.text_input("客户名", value=form_values.get("customer", ""), key="req_customer")
            priority = st.selectbox(
                "优先级",
                options=PRIORITY_LABELS,
                index=PRIORITY_LABELS.index(form_values.get("priority_hint", "未知")) if form_values.get("priority_hint") in PRIORITY_LABELS else len(PRIORITY_LABELS) - 1,
                key="req_priority",
            )
        with col_r:
            description = st.text_area("描述 *", value=form_values.get("description", ""), height=150, key="req_description")
            requested_due_date = _date_picker("期望完成日期", form_values.get("requested_due_date", ""), prefix="req_due")
            heads = [u for u in USERS if u["role"] == "主管"]
            selected_head_name = st.selectbox(
                "建议责任人 *",
                options=["（请选择）"] + [u["name"] for u in heads],
                index=0,
                key="req_owner",
            )
            st.caption("仅在原文明确出现系统主管的完整姓名时自动预填；“客户工程师”等称呼需人工确认。")
            head = next((u for u in heads if u["name"] == selected_head_name), None)

        # 价值-复杂度评分（v0.2.2 新增，可选）
        st.markdown("##### 价值-复杂度评分（可选，留空=未评）")
        st.caption("仅项目负责人/管理层在评审时建议填写；工程师无需填。AI 也可推断（会标 AI 标记）。")
        vc_col1, vc_col2 = st.columns(2)
        with vc_col1:
            value_rating = st.selectbox(
                "价值（对客户/公司业务）",
                options=["未评"] + ["高", "中", "低"],
                index=0,
                key="form_value_rating",
            )
        with vc_col2:
            complexity_rating = st.selectbox(
                "复杂度（技术实现）",
                options=["未评"] + ["高", "中", "低"],
                index=0,
                key="form_complexity_rating",
            )

        # 佐证材料（必填）— v0.2.0 新增
        st.markdown("##### 佐证材料 *（必填，符合「书面留痕」原则）")
        st.caption("至少填写一项链接或上传一份文件。例如：邮件原文链接、聊天截图、技术协议、会议纪要。")
        evidence_link = st.text_input(
            "佐证材料链接 1",
            placeholder="例如：邮件原文链接 / 共享盘路径 / 钉钉消息链接",
            key="evidence_link_1",
        )
        evidence_link2 = st.text_input(
            "佐证材料链接 2（可选）",
            placeholder="可选的第二条链接",
            key="evidence_link_2",
        )
        uploaded_evidence = st.file_uploader(
            "或上传文件（可多选，支持图片/文档/音频）",
            type=["png", "jpg", "jpeg", "pdf", "docx", "xlsx", "txt", "eml",
                  "mp3", "wav", "m4a", "aac", "ogg", "amr", "wma", "flac"],
            accept_multiple_files=True,
            key=f"evidence_upload_{uploader_version}",
        )

        # 标签推荐
        st.markdown("##### AI 推荐标签（A4）")
        tag_result_data = st.session_state.get("last_tag_result", {})
        recommended_tags = []
        if tag_result_data.get("success") and tag_result_data.get("data"):
            recommended_tags = [t.get("tag") for t in tag_result_data["data"] if t.get("tag")]

        selected_tags = []
        if recommended_tags:
            tag_cols = st.columns(min(len(recommended_tags) + len(ALL_TAGS), 6))
            # 推荐标签高亮
            for tag in ALL_TAGS:
                checked = tag in recommended_tags
                if st.checkbox(tag, value=checked, key=f"tag_{tag}"):
                    selected_tags.append(tag)
        else:
            st.caption("（暂无 AI 推荐，可手动勾选）")
            for tag in ALL_TAGS:
                if st.checkbox(tag, value=False, key=f"tag_{tag}"):
                    selected_tags.append(tag)

        submit_clicked = st.form_submit_button("提交需求", type="primary")

    # 提交反馈（紧邻提交按钮显示）
    submit_result = st.session_state.pop("_req_submit_result", None)
    if submit_result:
        if submit_result["type"] == "success":
            st.success(submit_result["text"])
        else:
            st.error(submit_result["text"])

    # A3 清晰度检查（在表单外，提交前触发）
    st.markdown("---")
    st.markdown("#### 步骤 3：AI 清晰度检查（A3）")
    if st.button("🔍 检查清晰度", disabled=not (title and description)):
        with st.spinner("AI 正在检查…"):
            result = ai_helper.check_clarity(
                title=title,
                description=description,
                source=source,
                customer=customer,
                provider=st.session_state.provider,
                api_key=st.session_state.api_key,
                model=st.session_state.model or None,
            )
        if result.success:
            st.session_state["last_clarity_result"] = result.data
        else:
            st.session_state["last_clarity_result"] = None
            st.error(f"❌ 清晰度检查失败：{result.error}")

    last_clarity = st.session_state.get("last_clarity_result")
    if last_clarity is not None:
        if last_clarity:
            st.warning(f"AI 提出 {len(last_clarity)} 条建议（可选）：")
            for i, s in enumerate(last_clarity, 1):
                st.markdown(f"**{i}. {s.get('dimension', '')}** — {s.get('suggestion', '')}")
        else:
            st.success("✅ 描述清晰，AI 无补充建议")

    # A2 相似检测
    st.markdown("---")
    st.markdown("#### 步骤 4：AI 相似需求检测（A2）")
    if st.button("🔎 检测相似需求", disabled=not (title and description)):
        # 简单基于关键词在现有需求中找 Top 3 候选（POC 用，避免 Embedding 部署）
        candidates = simple_keyword_match(title + " " + description, get_requirements(), top_n=3)
        if not candidates:
            st.info("未找到候选")
        else:
            with st.spinner(f"AI 正在复核 {len(candidates)} 条候选…"):
                result = ai_helper.check_similarity(
                    new_title=title,
                    new_description=description,
                    candidates=[{"id": c["id"], "title": c["title"], "description": c.get("description", "")} for c in candidates],
                    provider=st.session_state.provider,
                    api_key=st.session_state.api_key,
                    model=st.session_state.model or None,
                )
            if result.success:
                st.session_state["last_sim_result"] = {"candidates": candidates, "judgement": result.data}
            else:
                st.error(f"❌ 相似检测失败：{result.error}")

    last_sim = st.session_state.get("last_sim_result")
    if last_sim:
        st.markdown("**Top 3 相似候选**：")
        judgement = {item["id"]: item for item in last_sim["judgement"]} if last_sim.get("judgement") else {}
        for c in last_sim["candidates"]:
            j = judgement.get(c["id"], {})
            dup = j.get("duplicate", False)
            conf = j.get("confidence", 0)
            icon = "🔁" if dup else "✅"
            color = "#E53935" if dup else "#43A047"
            st.markdown(
                f'<div style="padding:8px;border-left:3px solid {color};margin:6px 0;">'
                f'{icon} <b>{c["id"]}</b> 「{c["title"]}」'
                f' &nbsp;<span style="color:{color};font-weight:600;">{"重复" if dup else "不重复"}（{conf:.2f}）</span>'
                f'<div style="color:#666;font-size:12px;">{j.get("reason","")}</div></div>',
                unsafe_allow_html=True,
            )

    # 提交逻辑（在表单外，因为 form_submit 已经在 form 内）
    if submit_clicked:
        if not title or not description:
            st.session_state["_req_submit_result"] = {"type": "error", "text": "标题与描述为必填项"}
            st.rerun()
        elif source not in SOURCE_LABELS:
            st.session_state["_req_submit_result"] = {"type": "error", "text": "部门为必填项，请选择"}
            st.rerun()
        elif not head:
            st.session_state["_req_submit_result"] = {"type": "error", "text": "建议责任人为必填项，请选择"}
            st.rerun()
        elif not (evidence_link.strip() or evidence_link2.strip() or uploaded_evidence):
            st.session_state["_req_submit_result"] = {"type": "error", "text": "佐证材料为必填项（至少一条链接或一个文件），符合「书面留痕」原则"}
            st.rerun()
        elif project_name != "（无）" and project_stage == "（请选择）":
            st.session_state["_req_submit_result"] = {"type": "error", "text": "已关联项目，请选择项目阶段"}
            st.rerun()
        else:
            # 填写耗时（阶段效率埋点）：从首次进入本页到点击提交的秒数；
            # 超过 4 小时视为「挂起跨会话」（离开页面很久再回来提交），不纳入统计。
            fill_secs = None
            _opened = st.session_state.get("_req_form_open_dt")
            if isinstance(_opened, datetime):
                _d = (datetime.now() - _opened).total_seconds()
                if 0 < _d <= 4 * 3600:
                    fill_secs = round(_d)
            # 是否 AI 预填：本会话做过一次成功的智能抽取即算（form_values/表单值已被预填）
            ai_assisted = bool((st.session_state.get("last_extract_result") or {}).get("success"))

            # 生成需求 ID（先于 evidence 构造，上传文件按此 ID 落盘归档）
            new_req_id = gen_req_id()

            # 构造 evidence 列表
            ev_list = []
            if evidence_link.strip():
                ev_list.append({
                    "type": "other", "label": evidence_link.strip()[:80],
                    "link": evidence_link.strip(),
                    "uploaded_by": st.session_state.current_user_id,
                    "uploaded_at": TODAY_STR,
                })
            if evidence_link2.strip():
                ev_list.append({
                    "type": "other", "label": evidence_link2.strip()[:80],
                    "link": evidence_link2.strip(),
                    "uploaded_by": st.session_state.current_user_id,
                    "uploaded_at": TODAY_STR,
                })
            for f in (uploaded_evidence or []):
                # 上传文件：真正保存内容到 data/attachments/<req_id>/，供详情页下载
                content = f.read()
                meta = db.save_attachment(
                    req_id=new_req_id,
                    filename=f.name,
                    content=content,
                    uploaded_by=st.session_state.current_user_id,
                )
                ev_list.append({
                    "type": "file",
                    "label": f.name,
                    "link": "",
                    "filename": meta["filename"],
                    "size": meta["size"],
                    "uploaded_by": st.session_state.current_user_id,
                    "uploaded_at": TODAY_STR,
                })

            # 建议责任人 = 所选主管
            requested_owner_id = head["id"] if head else ""
            department = head.get("department", "") if head else ""
            rating_source_label = ""
            if value_rating != "未评" or complexity_rating != "未评":
                rating_source_label = "人工"

            new_req = {
                "id": new_req_id,
                "title": title,
                "description": description,
                "evidence": ev_list,
                "source": source,
                "source_ref": "",
                "customer": customer,
                "requester_id": st.session_state.current_user_id,
                "requested_owner_id": requested_owner_id,
                "owner_ids": [],
                "department": department,
                "project_id": next((p["id"] for p in get_projects() if _project_display_name(p) == project_name), "") if project_name != "（无）" else "",
                "project_stage": project_stage if (project_name != "（无）" and project_stage != "（请选择）") else "",
                "status": "待确认",
                "priority": priority,
                "value_rating": value_rating,
                "complexity_rating": complexity_rating,
                "rating_source": rating_source_label,
                "tags": selected_tags,
                "requested_due_date": requested_due_date,
                "created_at": TODAY_STR,
                # ---- 阶段效率埋点（系统评估 → 阶段效率）----
                # created_at 只有日期精度，做阶段耗时需要秒级锚点；status_history 首条
                # 记录「（新建）→ 待确认」，与后续流转记录构成完整时间链；
                # form_fill_seconds / ai_assisted 供「填写效率」与 AI 增益对比。
                "created_dt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "form_fill_seconds": fill_secs,
                "ai_assisted": ai_assisted,
                "status_history": [{
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "user": USER_ID_TO_NAME.get(st.session_state.current_user_id, ""),
                    "from": "（新建）",
                    "to": "待确认",
                }],
                "review_state": "待责任人确认",
                "proposed_changes": None,
                "effort_estimate_days": 0,
                "comments": [],
                "liked_by": [],
            }
            save_requirement(new_req)
            # 【暂停提醒·试用】进入需求池前的共识流程提醒，试用效果好后取消注释恢复
            # _notify(
            #     [requested_owner_id],
            #     f"{USER_ID_TO_NAME.get(st.session_state.current_user_id, '同事')} 提交了新需求「{title}」，等待您确认。",
            #     req=new_req,
            # )
            st.session_state["_req_submit_result"] = {
                "type": "success",
                "text": f"✅ 已创建：{new_req['id']} 「{new_req['title']}」\n\n📎 已附 {len(ev_list)} 份佐证材料 · 状态：待确认（等待责任人确认后进入需求池）",
            }
            # 标记清空表单（在下次渲染前执行，避免 widget 已实例化报错）
            st.session_state["_clear_form"] = True
            st.rerun()


def simple_keyword_match(query: str, reqs: list[dict], top_n: int = 3) -> list[dict]:
    """POC 简化版相似检索：基于 Jaccard 关键词重合度。生产环境应换成 Embedding。"""
    # 简单分词：按字符级 n-gram (3-4 字) 模拟中文分词
    def tokenize(text: str) -> set:
        text = text.lower()
        tokens = set()
        # 提取 2-4 字长度的连续中文/字母数字片段
        cur = ""
        for ch in text:
            if '一' <= ch <= '鿿' or ch.isalnum():
                cur += ch
            else:
                if len(cur) >= 2:
                    for n in (2, 3, 4):
                        for i in range(len(cur) - n + 1):
                            tokens.add(cur[i:i + n])
                cur = ""
        if len(cur) >= 2:
            for n in (2, 3, 4):
                for i in range(len(cur) - n + 1):
                    tokens.add(cur[i:i + n])
        return tokens

    q_tokens = tokenize(query)
    if not q_tokens:
        return []

    scored = []
    for r in reqs:
        if r.get("status") in ["已终止", "已完成"]:
            continue
        text = (r.get("title", "") + " " + r.get("description", ""))
        r_tokens = tokenize(text)
        if not r_tokens:
            continue
        # Jaccard 相似度
        sim = len(q_tokens & r_tokens) / len(q_tokens | r_tokens)
        scored.append((sim, r))
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[:top_n] if _ > 0.05]


# ============================================================
# 页面：需求详情
# ============================================================

def _render_consensus(req):
    """待确认状态下的需求共识：主管接受/驳回/提调整，提出人确认/拒绝调整。"""
    requester_name = USER_ID_TO_NAME.get(req.get("requester_id", ""), "—")
    owner_name = USER_ID_TO_NAME.get(req.get("requested_owner_id", ""), "未指定")
    review_state = req.get("review_state", "待责任人确认")
    me = st.session_state.current_user_id
    proposal = req.get("proposed_changes")

    st.markdown("---")
    st.markdown("### 🤝 需求确认（共识）")
    st.caption(f"提出人：{requester_name} · 主管：{owner_name} · 双方达成共识后方可进入需求池")

    if not req.get("requested_owner_id"):
        st.warning("该需求尚未指定主管，无法进行确认。")
        return

    # 提出人可撤回（待确认阶段，进入需求池之前）：撤回 = 直接删除该需求
    if me == req.get("requester_id"):
        with st.expander("↩ 撤回需求（提出人）"):
            st.caption("撤回后该需求将被删除，不再显示、不参与任何统计。此操作不可恢复。")
            confirm = st.checkbox("我确认撤回并删除此需求", key=f"cons_withdraw_confirm_{req['id']}")
            if st.button("↩ 确认撤回", key=f"cons_withdraw_{req['id']}", disabled=not confirm):
                req_id = req["id"]
                delete_requirement(req_id)
                st.session_state["_flash"] = {"type": "info", "text": f"已撤回并删除需求 {req_id}"}
                st.rerun()

    if review_state == "待责任人确认":
        if req.get("requester_reply"):
            st.info(f"💬 提出人补充说明：{req['requester_reply']}")
        if me == req.get("requested_owner_id"):
            st.info("您是主管，请确认是否接受该需求，或反馈修改意见。")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("✅ 接受，进入需求池", key="cons_accept", type="primary"):
                    if proposal:
                        if proposal.get("requested_due_date"):
                            req["requested_due_date"] = proposal["requested_due_date"]
                        if proposal.get("priority") not in (None, ""):
                            req["priority"] = proposal["priority"]
                        if proposal.get("effort_estimate_days") is not None:
                            req["effort_estimate_days"] = proposal["effort_estimate_days"]
                    req["proposed_changes"] = None
                    req["requester_reply"] = None
                    _record_status_change(req, req.get("status", ""), "需求池", owner_name)
                    req["status"] = "需求池"
                    req["review_state"] = "已达成共识"
                    save_requirement(req)
                    _notify(
                        [req.get("requester_id", "")],
                        f"您提出的需求「{req['title']}」已被主管采纳，进入需求池。",
                        req=req,
                    )
                    st.session_state["_flash"] = {"type": "success", "text": f"✅ 已接受，{req['id']} 进入需求池"}
                    st.rerun()
            with c2:
                # 驳回后清空驳回原因（须在控件实例化前清）
                if st.session_state.pop("_clear_cons_reject", False):
                    st.session_state["cons_reject_reason"] = ""
                reject_reason = st.text_input("驳回原因", key="cons_reject_reason", placeholder="说明驳回理由")
                if st.button("❌ 驳回（终止）", key="cons_reject", disabled=not reject_reason.strip()):
                    _record_status_change(req, req.get("status", ""), "已终止", owner_name)
                    req["status"] = "已终止"
                    req["termination_reason"] = reject_reason.strip()
                    req["actual_completion_date"] = TODAY_STR
                    req["review_state"] = "已驳回"
                    save_requirement(req)
                    # 【暂停提醒·试用】共识流程驳回提醒，试用效果好后取消注释恢复
                    # _notify(
                    #     [req.get("requester_id", "")],
                    #     f"您提出的需求「{req['title']}」已被驳回终止"
                    #     f"（原因：{reject_reason.strip()}），请到看板查看。",
                    #     req=req,
                    # )
                    st.session_state["_clear_cons_reject"] = True
                    st.session_state["_flash"] = {"type": "success", "text": f"已驳回，{req['id']} 已终止"}
                    st.rerun()

            with st.expander("✏️ 反馈修改意见（改范围 / 截止日期 / 优先级）"):
                with st.form("cons_propose"):
                    new_due = _date_picker("期望完成日期", req.get("requested_due_date", ""), prefix=f"cons_due_{req['id']}")
                    pri_options = PRIORITY_LABELS
                    new_priority = st.selectbox(
                        "优先级",
                        options=pri_options,
                        index=pri_options.index(req.get("priority", "未知")) if req.get("priority", "未知") in pri_options else len(pri_options) - 1,
                    )
                    new_effort = st.number_input("估算工作量（人天）", min_value=0.0, value=float(req.get("effort_estimate_days") or 0), step=0.5)
                    note = st.text_area("调整说明", placeholder="说明需要调整的内容与理由")
                    if st.form_submit_button("提交调整"):
                        req["proposed_changes"] = {
                            "requested_due_date": new_due.strip(),
                            "priority": new_priority,
                            "effort_estimate_days": new_effort,
                            "note": note.strip(),
                        }
                        req["review_state"] = "待提出人确认"
                        save_requirement(req)
                        # 【暂停提醒·试用】共识流程反馈调整提醒，试用效果好后取消注释恢复
                        # _notify(
                        #     [req.get("requester_id", "")],
                        #     f"主管「{owner_name}」对需求「{req['title']}」反馈了修改意见，等待您确认。",
                        #     req=req,
                        # )
                        st.session_state["_flash"] = {"type": "success", "text": "已反馈修改意见，等待提出人补充说明"}
                        st.rerun()
        else:
            st.info(f"等待主管「{owner_name}」处理…")

    elif review_state == "待提出人确认":
        if proposal:
            st.warning(f"主管「{owner_name}」反馈以下修改意见：")
            if proposal.get("requested_due_date"):
                st.markdown(f"- **期望完成日期**：{proposal.get('requested_due_date')}")
            if proposal.get("priority") not in (None, ""):
                st.markdown(f"- **优先级**：{proposal.get('priority')}")
            if proposal.get("effort_estimate_days") is not None:
                st.markdown(f"- **估算工作量（人天）**：{proposal.get('effort_estimate_days')}")
            if proposal.get("note"):
                st.markdown(f"- **修改意见**：{proposal.get('note')}")
        if me == req.get("requester_id"):
            st.info("请根据主管的修改意见填写补充说明，提交后由主管最终确认。")
            reply_ver = st.session_state.get("cons_reply_uploader_ver", 0)
            with st.form("cons_reply"):
                reply = st.text_area("补充说明", placeholder="说明您的补充 / 澄清内容")
                reply_files = st.file_uploader(
                    "补充佐证文件（可选，可多选，支持图片/文档/音频）",
                    type=["png", "jpg", "jpeg", "pdf", "docx", "xlsx", "txt", "eml",
                          "mp3", "wav", "m4a", "aac", "ogg", "amr", "wma", "flac"],
                    accept_multiple_files=True,
                    key=f"cons_reply_files_{reply_ver}",
                )
                if st.form_submit_button("📤 提交补充说明"):
                    if not reply.strip():
                        st.error("请填写补充说明")
                    else:
                        req["requester_reply"] = reply.strip()
                        # 上传的文件补充到佐证材料
                        for f in (reply_files or []):
                            content = f.read()
                            meta = db.save_attachment(req_id=req["id"], filename=f.name, content=content, uploaded_by=me)
                            req.setdefault("evidence", []).append({
                                "type": "file",
                                "label": f.name,
                                "link": "",
                                "filename": meta["filename"],
                                "size": meta["size"],
                                "uploaded_by": me,
                                "uploaded_at": TODAY_STR,
                            })
                        req["review_state"] = "待责任人确认"
                        req.setdefault("comments", []).append({
                            "user": USER_ID_TO_NAME.get(me, "我"),
                            "time": TODAY_STR,
                            "text": f"提出人提交补充说明：{reply.strip()}",
                        })
                        save_requirement(req)
                        # 【暂停提醒·试用】共识流程补充说明提醒，试用效果好后取消注释恢复
                        # _notify(
                        #     [req.get("requested_owner_id", "")],
                        #     f"{requester_name} 对需求「{req['title']}」提交了补充说明，等待您再次确认。",
                        #     req=req,
                        # )
                        if reply_files:
                            _invalidate_attachment_cache()
                        st.session_state["cons_reply_uploader_ver"] = reply_ver + 1
                        st.session_state["_flash"] = {"type": "success", "text": "已提交补充说明，等待主管确认"}
                        st.rerun()
        else:
            st.info(f"等待提出人「{requester_name}」填写补充说明…")
    else:
        if review_state == "已驳回":
            st.warning("该需求此前已被主管驳回终止，但状态被改回了「待确认」，处于异常状态。")
            if st.button("🔧 恢复为「已终止」", key="cons_restore_terminated"):
                req["status"] = "已终止"
                save_requirement(req)
                st.rerun()
        else:
            st.caption(f"共识状态：{review_state}")


def _render_status_change(req):
    """非「待确认」状态下的状态流转：切换状态、指派执行人、改日期/评分。"""
    me = st.session_state.current_user_id
    st.markdown("---")
    st.markdown("**状态变更**")
    # 内联成功提示（在当前区块显示，无需回到页面顶部）
    sc = st.session_state.pop("_status_change_flash", None)
    if sc and sc.get("req_id") == req["id"]:
        st.success(sc["text"])
    # 「待确认」是共识门禁状态，只能由新建/共识流程进入，不允许手动改入
    status_options = [s for s in STATUS_LABELS if s != "待确认"]
    new_status = st.selectbox(
        "切换状态",
        options=status_options,
        index=status_options.index(req["status"]) if req["status"] in status_options else 0,
        key=f"status_select_{req['id']}",
    )

    # 终止原因输入（如果新状态是「已终止」）
    term_reason_input = ""
    if new_status == "已终止":
        term_reason_input = st.text_input(
            "请填写终止原因（必填）",
            value=req.get("termination_reason", ""),
            key=f"term_reason_{req['id']}",
        )

    # 执行人指派（仅建议责任人，可多选）+ 工作量评估（建议责任人或执行人）
    assign_owners = None
    new_effort = None
    if new_status in ("待启动", "进行中"):
        owner_ids = [i for i in req.get("owner_ids", []) if i]
        current_owner = "、".join(USER_ID_TO_NAME.get(i, "未知") for i in owner_ids) if owner_ids else "未指派"
        is_requested_owner = (me == req.get("requested_owner_id"))
        is_executor = (me in owner_ids)
        # 指派 / 调整执行人：仅建议责任人，可指定任何人（不限部门/角色），可多选
        if is_requested_owner:
            name_to_dept = {u["name"]: (u.get("department") or "") for u in USERS}
            assign_owners = st.multiselect(
                "指派 / 调整执行人（可多选）",
                options=[u["name"] for u in USERS],
                default=[USER_ID_TO_NAME.get(i, i) for i in owner_ids],
                format_func=lambda x: f"{x}（{name_to_dept.get(x, '')}）" if name_to_dept.get(x) else x,
                key=f"assign_owner_{req['id']}",
            )
        else:
            st.caption(f"当前执行人：{current_owner}（仅建议责任人可指派/调整）")
        # 工作量评估：建议责任人 或 执行人 均可改
        if is_requested_owner or is_executor:
            new_effort = st.number_input(
                "工作量评估（人天）",
                min_value=0.0,
                value=float(req.get("effort_estimate_days") or 0),
                step=0.5,
                key=f"effort_{req['id']}",
            )

    # 期望完成日期（终态「已完成 / 已终止」无需填写）
    new_due_date = ""
    if new_status not in ("已完成", "已终止"):
        new_due_date = _date_picker("期望完成日期", req.get("requested_due_date", ""), prefix=f"due_date_{req['id']}")

    # 价值-复杂度评分修改（任意状态都可改）
    edit_rating = st.checkbox("编辑价值/复杂度评分", value=False, key=f"edit_rating_{req['id']}")
    new_v, new_c = None, None
    if edit_rating:
        rc1, rc2 = st.columns(2)
        with rc1:
            v_options = ["未评", "高", "中", "低"]
            new_v = st.selectbox(
                "价值",
                options=v_options,
                index=v_options.index(req.get("value_rating", "未评")) if req.get("value_rating", "未评") in v_options else 0,
                key=f"edit_v_{req['id']}",
            )
        with rc2:
            new_c = st.selectbox(
                "复杂度",
                options=v_options,
                index=v_options.index(req.get("complexity_rating", "未评")) if req.get("complexity_rating", "未评") in v_options else 0,
                key=f"edit_c_{req['id']}",
            )

    status_changed = new_status != req["status"]
    if status_changed or new_effort is not None:
        if st.button("✅ 确认变更", key=f"confirm_{req['id']}"):
            # 校验
            if new_status == "已终止" and not term_reason_input.strip():
                st.error("「已终止」状态必须填写终止原因")
            else:
                newly_assigned: list[str] = []  # 本次变更新指派的执行人（用于钉钉提醒）
                if status_changed:
                    _record_status_change(req, req["status"], new_status, USER_ID_TO_NAME.get(me, "我"))
                req["status"] = new_status
                if new_status in ("已完成", "已终止"):
                    req["actual_completion_date"] = TODAY_STR
                if new_status == "已终止":
                    req["termination_reason"] = term_reason_input.strip()
                if new_status in ("待启动", "进行中") and assign_owners is not None:
                    old_ids = [i for i in req.get("owner_ids", []) if i]
                    new_ids = [USER_NAME_TO_ID.get(n, "") for n in assign_owners if USER_NAME_TO_ID.get(n)]
                    newly_assigned = [i for i in new_ids if i not in old_ids]
                    # 指派提醒：只保留「沿用且已消读」的执行人，新增/换人的执行人会收到提醒
                    req["assign_seen_by"] = [
                        u for u in req.get("assign_seen_by", []) if u in old_ids and u in new_ids
                    ]
                    req["owner_ids"] = new_ids
                    # 多执行人：自动保持「子需求 ↔ 执行人」一一对应（新增补建、移除删除）
                    _sync_sub_requirements(req)
                if new_status in ("待启动", "进行中") and new_effort is not None:
                    req["effort_estimate_days"] = new_effort
                # 保存期望完成日期
                if new_due_date.strip():
                    req["requested_due_date"] = new_due_date.strip()
                # 保存评分（如果用户开了评分编辑）
                if edit_rating:
                    req["value_rating"] = new_v
                    req["complexity_rating"] = new_c
                    if new_v != "未评" or new_c != "未评":
                        req["rating_source"] = "人工"
                save_requirement(req)
                # 钉钉提醒：完成 → 同步提出人与建议负责人；指派 → 提醒新增的执行人
                if new_status == "已完成":
                    _notify(
                        [req.get("requester_id", ""), req.get("requested_owner_id", "")],
                        f"需求「{req['title']}」（{req['id']}）已完成，特此同步。",
                        req=req,
                    )
                if newly_assigned:
                    sub_cnt = len(req.get("sub_requirements") or [])
                    tail = (
                        f"已自动拆分为 {sub_cnt} 条子需求，请到「需求详情」查看并流转您负责的子需求。"
                        if sub_cnt else "请到「我的看板」查看。"
                    )
                    _notify(
                        newly_assigned,
                        f"需求「{req['title']}」（{req['id']}）已指派给您执行，"
                        f"当前状态「{new_status}」，{tail}",
                        req=req,
                    )
                flash_text = f"✅ 已成功更新状态为「{new_status}」" if status_changed else "✅ 已成功更新"
                st.session_state["_status_change_flash"] = {"req_id": req["id"], "text": flash_text}
                st.rerun()


def _render_sub_requirements(req):
    """子需求区：每条子需求与一位执行人一一对应，状态由对应执行人独立流转（负责人可代改），
    父需求状态按「全量联动」自动推导。"""
    subs = req.get("sub_requirements") or []
    if not subs:
        return
    me = st.session_state.current_user_id
    is_head = (me == req.get("requested_owner_id"))
    done_cnt = sum(1 for s in subs if s.get("status") in ("已完成", "已终止"))

    # 内联成功提示（区别于父需求「状态变更」区块的提示）
    sflash = st.session_state.pop("_sub_change_flash", None)
    if sflash and sflash.get("req_id") == req["id"]:
        st.success(sflash["text"])

    st.markdown("---")
    st.markdown(f"**🧩 子需求**（{len(subs)} 条 · 与执行人一一对应 · 进度 {done_cnt}/{len(subs)}）")
    st.caption("每条子需求由对应执行人独立流转状态，建议责任人可代改并编辑子需求内容；父需求状态随子需求自动联动。")

    for sub in subs:
        cur = sub.get("status", "待启动")
        owner_name = USER_ID_TO_NAME.get(sub.get("owner_id", ""), "—")
        can_operate = (me == sub.get("owner_id")) or is_head
        note_html = (
            f'<div style="font-size:11px;color:#999;margin-top:2px;">{sub.get("note", "")}</div>'
            if sub.get("note") else ""
        )
        row = st.columns([4, 1, 3])
        with row[0]:
            st.markdown(
                f'<div style="padding:6px 8px;background:#F5F5F5;border-radius:4px;">'
                f'<b>{sub["title"]}</b>&nbsp;<span style="color:#888;font-size:11px;">{sub["id"]}</span>'
                f'<div style="font-size:12px;color:#555;margin-top:2px;">👤 {owner_name}</div>'
                f'{note_html}</div>',
                unsafe_allow_html=True,
            )
        with row[1]:
            st.markdown(status_badge(cur), unsafe_allow_html=True)
        with row[2]:
            if can_operate:
                status_opts = [s for s in STATUS_LABELS if s != "待确认"]
                new_st = st.selectbox(
                    "切换状态",
                    options=status_opts,
                    index=status_opts.index(cur) if cur in status_opts else 0,
                    key=f"sub_st_{sub['id']}",
                    label_visibility="collapsed",
                )
                term_reason = ""
                if new_st == "已终止":
                    term_reason = st.text_input(
                        "终止原因（必填）",
                        value=sub.get("termination_reason", ""),
                        key=f"sub_term_{sub['id']}",
                    )
                if st.button("✅ 确认变更", key=f"sub_ok_{sub['id']}", use_container_width=True):
                    if new_st == "已终止" and not term_reason.strip():
                        st.error("「已终止」必须填写终止原因")
                    elif new_st == cur:
                        st.info("状态未变化")
                    else:
                        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                        my_name = USER_ID_TO_NAME.get(me, "我")
                        sub["history"].append({"time": now_str, "user": my_name, "from": cur, "to": new_st})
                        sub["status"] = new_st
                        if new_st == "已终止":
                            sub["termination_reason"] = term_reason.strip()
                        # 父需求状态全量联动
                        agg = _aggregate_parent_status(req)
                        parent_changed = False
                        if agg and agg != req["status"]:
                            _record_status_change(req, req["status"], agg, "（子需求联动）")
                            req["status"] = agg
                            if agg in ("已完成", "已终止"):
                                req["actual_completion_date"] = TODAY_STR
                            parent_changed = True
                        save_requirement(req)
                        # 提醒：子需求完成 → 通知负责人；父需求自动完成 → 同步提出人与负责人
                        all_subs = req.get("sub_requirements") or []
                        d_cnt = sum(1 for s in all_subs if s.get("status") in ("已完成", "已终止"))
                        if new_st == "已完成" and me != req.get("requested_owner_id"):
                            _notify(
                                [req.get("requested_owner_id", "")],
                                f"子需求「{sub['title']}」（{sub['id']}）已由 {my_name} 标记完成，"
                                f"父需求「{req['title']}」进度 {d_cnt}/{len(all_subs)}。",
                                req=req,
                            )
                        if parent_changed and agg == "已完成":
                            _notify(
                                [req.get("requester_id", ""), req.get("requested_owner_id", "")],
                                f"需求「{req['title']}」（{req['id']}）全部子需求已完成，需求已完成。",
                                req=req,
                            )
                        flash_text = f"✅ 子需求 {sub['id']} 已更新为「{new_st}」"
                        if parent_changed:
                            flash_text += f"，父需求联动为「{agg}」"
                        st.session_state["_sub_change_flash"] = {"req_id": req["id"], "text": flash_text}
                        st.rerun()
            else:
                st.caption(f"由执行人 {owner_name} 操作")

        with st.expander(f"🕘 变更记录（{sub['id']} · {len(sub.get('history') or [])} 条）"):
            hist = sub.get("history") or []
            if hist:
                for h in reversed(hist):
                    st.markdown(f"- {h['time']} · {h['user']}：{h.get('from', '—')} → {h.get('to', '—')}")
            else:
                st.caption("暂无流转记录")
        if is_head:
            with st.expander(f"✏️ 编辑子需求内容（{sub['id']}）"):
                with st.form(f"sub_edit_{sub['id']}"):
                    edit_title = st.text_input("子需求标题", value=sub.get("title", ""), key=f"sub_edit_t_{sub['id']}")
                    edit_note = st.text_area("说明（选填）", value=sub.get("note", ""), height=68, key=f"sub_edit_n_{sub['id']}")
                    if st.form_submit_button("💾 保存", use_container_width=True):
                        sub["title"] = edit_title.strip() or sub.get("title", "")
                        sub["note"] = edit_note.strip()
                        save_requirement(req)
                        st.session_state["_sub_change_flash"] = {"req_id": req["id"], "text": f"✅ 已更新子需求 {sub['id']}"}
                        st.rerun()


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


def page_requirement_detail():
    st.markdown("## 📄 需求详情")

    req_ids = [r["id"] for r in get_requirements()]
    # 如果从看板点击「查看」跳转过来，覆盖当前选中的需求
    preselected = st.session_state.pop("selected_req_id", None)
    if preselected and preselected in req_ids:
        st.session_state["detail_select_req"] = preselected
    # 确保 session_state 中的值在 req_ids 中有效（防止数据变更后失效）
    if st.session_state.get("detail_select_req") not in req_ids:
        st.session_state["detail_select_req"] = req_ids[0] if req_ids else None
    title_by_id = {r["id"]: r["title"] for r in get_requirements()}
    req_id = st.selectbox(
        "选择需求",
        options=req_ids,
        key="detail_select_req",
        format_func=lambda x: f"{x} · {title_by_id.get(x, '')}"[:60],
    )
    if not req_id:
        return

    req = get_requirement(req_id)
    if not req:
        st.warning("需求不存在")
        return

    st.markdown(f"### {req['title']}")
    st.markdown(
        f'{status_badge(req["status"])} &nbsp; {priority_badge(req.get("priority","未知"))} &nbsp; '
        f'<span style="color:#666;">{req["id"]}</span>',
        unsafe_allow_html=True,
    )

    st.markdown("---")
    col_a, col_b, col_c, col_d = st.columns(4)
    with col_a:
        st.markdown(f"**部门**<br>{req.get('source','')}", unsafe_allow_html=True)
    with col_b:
        requester = USER_ID_TO_NAME.get(req.get("requester_id", ""), "—")
        st.markdown(f"**提出人**<br>{requester}", unsafe_allow_html=True)
    with col_c:
        requested_owner = USER_ID_TO_NAME.get(req.get("requested_owner_id", ""), "—")
        st.markdown(f"**建议负责人**<br>{requested_owner}", unsafe_allow_html=True)
    with col_d:
        owner = _owner_names(req)
        st.markdown(f"**执行人**<br>{owner}", unsafe_allow_html=True)

    # 点赞（可点赞/取消，提出人不能给自己点赞）
    liked_by = req.get("liked_by", [])
    me = st.session_state.current_user_id
    like_names = "、".join(USER_ID_TO_NAME.get(u, "未知") for u in liked_by)
    if like_names:
        st.caption(f"👍 {len(liked_by)} 人觉得有用：{like_names}")
    if me == req.get("requester_id"):
        st.caption("（您是本需求的提出人，不能给自己点赞）" + (f" 当前获赞 {len(liked_by)}" if liked_by else ""))
    elif me in liked_by:
        if st.button(f"👍 已点赞 {len(liked_by)} · 点击取消", key=f"unlike_{req['id']}"):
            req["liked_by"] = [u for u in liked_by if u != me]
            save_requirement(req)
            st.rerun()
    else:
        if st.button(f"👍 觉得有用，点赞（当前 {len(liked_by)}）", key=f"like_{req['id']}"):
            req.setdefault("liked_by", []).append(me)
            save_requirement(req)
            st.rerun()

    # 需求评价（一期）：提出人 / 建议责任人 / 执行人各评一次。
    # 可见性口径：普通人只看得到自己那条；管理层可展开看全部明细（与用户确认的口径一致）。
    if req.get("status") == "已完成":
        _raters = _req_raters(req)
        _ratings_map = _ratings(req)
        _is_rater = me in _raters
        _is_mgr = _can_view_rating_detail()
        if _raters and (_is_rater or _is_mgr):
            st.markdown("---")
            st.markdown("**需求评价**")
            st.caption(f"本需求需 {len(_raters)} 人评价（提出人 / 建议责任人 / 执行人），"
                       f"目前已完成 {len(_ratings_map)} 人。")
            _mine = _ratings_map.get(me)
            if _mine:
                st.markdown(
                    f'我的评价：{_star_html(_mine["score"], 17)} &nbsp;'
                    f'<b>{_mine["score"]}/5</b> · {SATISFACTION_LABELS.get(_mine["score"], "")} '
                    f'<span style="color:#666;font-size:12px;">{_mine.get("at", "")}</span>',
                    unsafe_allow_html=True,
                )
                if _mine.get("comment"):
                    st.caption(f"我的补充说明：{_mine['comment']}")
                if _is_rater and st.button("✏️ 修改我的评价", key=f"rerate_{req['id']}"):
                    st.session_state["_rating_req"] = req["id"]
                    st.rerun()
            elif _is_rater:
                st.caption(f"您在本需求中的身份是「{_rater_role(req, me)}」，尚未评价。")
                if st.button("⭐ 去评价", type="primary", key=f"rate_here_{req['id']}"):
                    st.session_state["_rating_req"] = req["id"]
                    st.rerun()

            if _is_mgr and _ratings_map:
                with st.expander(f"👁 全部评价明细（{len(_ratings_map)}/{len(_raters)} 人，仅管理层可见）"):
                    for _uid in _raters:
                        _rec = _ratings_map.get(_uid)
                        _name = USER_ID_TO_NAME.get(_uid, _uid)
                        _role = _rater_role(req, _uid)
                        if _rec:
                            st.markdown(
                                f'{_star_html(_rec["score"], 15)} <b>{_rec["score"]}/5</b> '
                                f'{_name}（{_role}）'
                                f'<span style="color:#666;font-size:12px;"> · {_rec.get("at", "")}</span>',
                                unsafe_allow_html=True,
                            )
                            if _rec.get("comment"):
                                st.caption(f"　└ {_rec['comment']}")
                        else:
                            st.caption(f"⏳ {_name}（{_role}）尚未评价")
                    _scored = [int(v["score"]) for v in _ratings_map.values() if v.get("score")]
                    if _scored:
                        st.markdown(f"**本需求均分：{sum(_scored) / len(_scored):.2f} / 5**")

    st.markdown("---")
    st.markdown("**描述**")
    st.markdown(req.get("description", "—"))

    # 关联项目（可补充 / 修改）
    st.markdown("---")
    st.markdown("**关联项目**")
    st.caption(
        "💡 选「（无）」时，大项目视图按标题自动归类：含 A/B/C → 对应大项目；"
        "否则含 AI → 研发AI赋能；都不是 → 其他"
    )
    all_projects = get_projects()
    if not all_projects:
        st.caption("暂无项目，请先到「项目视图」新建项目后再关联。")
    else:
        proj_options = ["（无）"] + [_project_display_name(p) for p in all_projects]
        current_proj = next((p for p in all_projects if p["id"] == req.get("project_id", "")), None)
        current_proj_name = _project_display_name(current_proj) if current_proj else ""
        proj_index = proj_options.index(current_proj_name) if current_proj_name in proj_options else 0
        stage_options = ["（请选择）"] + PROJECT_STAGES
        cur_stage = req.get("project_stage", "")
        stage_index = stage_options.index(cur_stage) if cur_stage in stage_options else 0
        pcol, scol = st.columns(2)
        with pcol:
            new_proj_name = st.selectbox("关联项目", options=proj_options, index=proj_index, key=f"detail_proj_{req['id']}")
        with scol:
            new_stage = st.selectbox("项目阶段", options=stage_options, index=stage_index, key=f"detail_stage_{req['id']}", disabled=(new_proj_name == "（无）"))
        if st.button("💾 保存关联项目", key=f"save_proj_{req['id']}", use_container_width=True):
            if new_proj_name != "（无）" and new_stage == "（请选择）":
                st.error("已关联项目，请选择项目阶段")
            else:
                req["project_id"] = next((p["id"] for p in all_projects if _project_display_name(p) == new_proj_name), "") if new_proj_name != "（无）" else ""
                req["project_stage"] = new_stage if (new_proj_name != "（无）" and new_stage != "（请选择）") else ""
                save_requirement(req)
                st.session_state["_flash"] = {"type": "success", "text": "✅ 已更新关联项目"}
                st.rerun()

    # 佐证材料（v0.2.0 新增）
    st.markdown("---")
    st.markdown(f"**佐证材料**（共 {len(req.get('evidence', []))} 份）")
    evidence_list = req.get("evidence", [])
    if evidence_list:
        for i, ev_item in enumerate(evidence_list, 1):
            ev_type_label = EVIDENCE_TYPES.get(ev_item.get("type", "other"), ev_item.get("type", "其他"))
            uploader = USER_ID_TO_NAME.get(ev_item.get("uploaded_by", ""), "—")
            # 上传文件类佐证：落盘存储，提供下载按钮；链接类：直接展示链接
            if ev_item.get("filename"):
                size_kb = ev_item.get("size", 0) / 1024
                file_data = _cached_attachment(
                    req["id"], ev_item.get("filename", ""), ev_item.get("size", 0)
                )
                info_col, dl_col = st.columns([4, 1])
                with info_col:
                    st.markdown(
                        f'<div style="padding:8px;background:#FFF8E1;border-left:3px solid #FFA726;border-radius:4px;margin:6px 0;">'
                        f'<span style="color:#666;font-size:11px;">#{i} · {ev_type_label} · 上传人 {uploader}</span>'
                        f'<div style="margin-top:4px;">📎 {ev_item.get("label", "")}'
                        f' &nbsp; <span style="color:#666;font-size:11px;">{size_kb:.1f} KB</span></div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                with dl_col:
                    if file_data:
                        st.download_button(
                            "⬇下载",
                            data=file_data,
                            file_name=ev_item.get("label", ev_item.get("filename", "evidence")),
                            key=f"dl_ev_{req['id']}_{i}",
                            use_container_width=True,
                        )
                if file_data:
                    _render_file_preview(file_data, ev_item.get("label", ev_item.get("filename", "佐证文件")))
            else:
                st.markdown(
                    f'<div style="padding:8px;background:#FFF8E1;border-left:3px solid #FFA726;border-radius:4px;margin:6px 0;">'
                    f'<span style="color:#666;font-size:11px;">#{i} · {ev_type_label} · 上传人 {uploader}</span>'
                    f'<div style="margin-top:4px;">📎 {ev_item.get("label", "")}</div>'
                    f'<div style="color:#1976D2;font-size:12px;margin-top:2px;word-break:break-all;">{ev_item.get("link", "")}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
    else:
        st.warning("⚠️ 该需求未附佐证材料（不符合「书面留痕」原则）")

    if req.get("tags"):
        st.markdown("**标签**")
        st.markdown("".join(tag_chip(t) for t in req["tags"]), unsafe_allow_html=True)

    if req.get("customer") or req.get("source_ref"):
        st.markdown("---")
        st.markdown("**客户与来源**")
        if req.get("customer"):
            st.markdown(f"客户：{req['customer']}")
        if req.get("source_ref"):
            st.markdown(f"来源引用：{req['source_ref']}")

    # 终止原因（v0.2.0 新增）
    if req.get("status") == "已终止" and req.get("termination_reason"):
        st.markdown("---")
        st.markdown(f"**终止原因**：<span style='color:#5D4037;'>{req['termination_reason']}</span>", unsafe_allow_html=True)

    # 价值-复杂度评分（v0.2.2 新增）
    st.markdown("---")
    st.markdown("**价值-复杂度评分**")
    v_r = req.get("value_rating", "未评")
    c_r = req.get("complexity_rating", "未评")
    src = req.get("rating_source", "")
    if v_r == "未评" and c_r == "未评":
        st.caption("（未评）")
    else:
        st.markdown(
            rating_badge("价值", v_r, src) + " &nbsp; " + rating_badge("复杂度", c_r, src),
            unsafe_allow_html=True,
        )
        # 给出象限解读
        quadrant_hint = ""
        if v_r == "高" and c_r == "低":
            quadrant_hint = "速胜（Quick Win）— 建议优先做"
        elif v_r == "高" and c_r == "中":
            quadrant_hint = "战略项目 — 主动规划"
        elif v_r == "高" and c_r == "高":
            quadrant_hint = "重大项目 — 重点保障"
        elif v_r == "中" and c_r == "低":
            quadrant_hint = "顺手做 — 资源有空隙时做"
        elif v_r == "中" and c_r == "中":
            quadrant_hint = "正常推进"
        elif v_r == "中" and c_r == "高":
            quadrant_hint = "审慎评估"
        elif v_r == "低" and c_r == "低":
            quadrant_hint = "填空任务 — 资源紧张可弃"
        elif v_r == "低" and c_r == "中":
            quadrant_hint = "鸡肋 — 建议考虑终止"
        elif v_r == "低" and c_r == "高":
            quadrant_hint = "「钱坑」— 建议主动终止"
        if quadrant_hint:
            st.caption(f"📍 矩阵位置：{quadrant_hint}")

    # 需求确认（共识） / 状态变更
    if req["status"] == "待确认":
        _render_consensus(req)
    elif req["status"] == "已终止":
        # 终态锁定：已终止的需求不可再变更状态
        st.markdown("---")
        st.markdown("**状态变更**")
        st.info(f"🔒 该需求已终止，状态不可再变更。终止原因：{req.get('termination_reason') or '（未填写）'}")

        # 删除已终止需求
        with st.expander("🗑 删除需求"):
            st.caption("删除后该需求将从系统移除，不再显示、不参与统计。此操作不可恢复。")
            confirm = st.checkbox("我确认删除此需求", key=f"del_req_confirm_{req['id']}")
            if st.button("🗑 确认删除", key=f"del_req_{req['id']}", disabled=not confirm):
                req_id = req["id"]
                delete_requirement(req_id)
                st.session_state["_flash"] = {"type": "info", "text": f"已删除需求 {req_id}"}
                st.rerun()
    else:
        _render_status_change(req)

    # 子需求（多执行人自动拆分，一一对应独立流转）
    _render_sub_requirements(req)

    # 附件（F5 执行过程产物，区别于 evidence 佐证材料）
    st.markdown("---")
    st.markdown(f"**📎 附件**（执行过程产物，区别于「佐证材料」与「评论区附件」。共 {len(req.get('attachments', []))} 份）")
    st.caption("上传测试报告、改进图纸、调试日志等执行过程中产生的文件。文件保存在本地 data/attachments/ 目录。")

    existing_atts = req.get("attachments", [])
    if existing_atts:
        for i, att in enumerate(existing_atts):
            att_size_kb = att.get("size", 0) / 1024
            uploader_name = USER_ID_TO_NAME.get(att.get("uploaded_by", ""), "—")
            file_data = _cached_attachment(
                req["id"], att.get("filename", ""), att.get("size", 0)
            )
            cols = st.columns([4, 1, 1])
            with cols[0]:
                st.markdown(
                    f'<div style="padding:6px;background:#F5F5F5;border-radius:4px;">'
                    f'📎 <b>{att.get("filename", att.get("original_name", ""))}</b>'
                    f' &nbsp; <span style="color:#666;font-size:11px;">{att_size_kb:.1f} KB · {att.get("uploaded_at","")} · {uploader_name}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            with cols[1]:
                if file_data:
                    st.download_button(
                        "⬇下载",
                        data=file_data,
                        file_name=att.get("filename", "attachment"),
                        key=f"dl_att_{req['id']}_{i}",
                        use_container_width=True,
                    )
            with cols[2]:
                if st.button("🗑删除", key=f"del_att_{req['id']}_{i}", use_container_width=True):
                    db.remove_attachment(req["id"], att.get("filename", ""))
                    req["attachments"] = [a for a in existing_atts if a.get("filename") != att.get("filename")]
                    save_requirement(req)
                    _invalidate_attachment_cache()
                    st.session_state["_flash"] = {"type": "success", "text": "已删除附件"}
                    st.rerun()
            if file_data:
                _render_file_preview(file_data, att.get("original_name", att.get("filename", "附件")))
    else:
        st.caption("（暂无附件）")

    # 上传新附件
    new_att = st.file_uploader(
        "上传附件（支持 png/jpg/pdf/docx/xlsx/txt/eml/mp3/wav 等图片、文档与音频）",
        type=["png", "jpg", "jpeg", "gif", "pdf", "docx", "doc", "xlsx", "xls", "txt", "eml", "csv", "md",
              "mp3", "wav", "m4a", "aac", "ogg", "amr", "wma", "flac"],
        key=f"att_up_{req['id']}",
    )
    if new_att is not None:
        if st.button("💾 保存附件", key=f"save_att_{req['id']}"):
            content = new_att.read()
            att_meta = db.save_attachment(
                req_id=req["id"],
                filename=new_att.name,
                content=content,
                uploaded_by=st.session_state.current_user_id,
            )
            req.setdefault("attachments", []).append(att_meta)
            save_requirement(req)
            _invalidate_attachment_cache()
            st.session_state["_flash"] = {"type": "success", "text": f"✅ 已上传：{new_att.name}（{len(content)/1024:.1f} KB）"}
            st.rerun()

    # 评论时间线
    st.markdown("---")
    st.markdown("**评论时间线**")
    for ci, c in enumerate(req.get("comments", [])):
        st.markdown(
            f'<div style="padding:8px;background:#F5F5F5;border-radius:6px;margin:6px 0;">'
            f'<span style="font-weight:600;">{c.get("user","")}</span>'
            f'<span style="color:#888;font-size:12px;margin-left:8px;">{c.get("time","")}</span>'
            f'<div style="margin-top:4px;">{c.get("text","")}</div></div>',
            unsafe_allow_html=True,
        )
        # 评论区附件：该评论随附上传的文件，独立于「佐证材料」「附件」
        for fi, f_meta in enumerate(c.get("files") or []):
            file_data = _cached_attachment(req["id"], f_meta.get("filename", ""), f_meta.get("size", 0))
            f_size_kb = f_meta.get("size", 0) / 1024
            fcols = st.columns([4, 1])
            with fcols[0]:
                st.markdown(
                    f'<div style="margin-left:10px;padding:4px 8px;background:#FAFAFA;'
                    f'border:1px solid #EEE;border-radius:4px;font-size:12px;">'
                    f'📎 <b>{f_meta.get("original_name", f_meta.get("filename", ""))}</b>'
                    f' &nbsp;<span style="color:#999;">{f_size_kb:.1f} KB</span></div>',
                    unsafe_allow_html=True,
                )
            with fcols[1]:
                if file_data:
                    st.download_button(
                        "⬇下载",
                        data=file_data,
                        file_name=f_meta.get("filename", "file"),
                        key=f"dl_cmt_{req['id']}_{ci}_{fi}",
                        use_container_width=True,
                    )
    # 发送评论后清空输入框（须在控件实例化前清）
    if st.session_state.pop(f"_clear_comment_{req['id']}", False):
        st.session_state[f"new_comment_{req['id']}"] = ""
    comment_ver = st.session_state.get(f"comment_uploader_ver_{req['id']}", 0)
    new_comment = st.text_input("添加评论", key=f"new_comment_{req['id']}")
    comment_files = st.file_uploader(
        "评论区附件（可选，可多选，随评论保存，不进入上方「附件」列表）",
        type=["png", "jpg", "jpeg", "pdf", "docx", "xlsx", "txt", "eml",
              "mp3", "wav", "m4a", "aac", "ogg", "amr", "wma", "flac"],
        accept_multiple_files=True,
        key=f"comment_files_{req['id']}_{comment_ver}",
    )
    if st.button("发送评论", key=f"send_comment_{req['id']}") and new_comment.strip():
        me = st.session_state.current_user_id
        # 评论区附件：随评论保存（与「佐证材料」「附件」三分类独立）
        file_metas = []
        for f in (comment_files or []):
            content = f.read()
            file_metas.append(db.save_attachment(req_id=req["id"], filename=f.name, content=content, uploaded_by=me))
        req.setdefault("comments", []).append({
            "user": USER_ID_TO_NAME.get(me, "我"),
            "time": TODAY_STR,
            "text": new_comment.strip(),
            "files": file_metas,
        })
        save_requirement(req)
        if file_metas:
            _invalidate_attachment_cache()
        st.session_state[f"_clear_comment_{req['id']}"] = True
        st.session_state[f"comment_uploader_ver_{req['id']}"] = comment_ver + 1
        st.rerun()


def _render_center_briefing():
    """A6 中心简报（v0.2.4 新增，对应 PRD §F2.3 与 A6）"""
    st.markdown("### 🏢 中心简报")
    st.caption(f"数据截至 {TODAY_STR}")

    reqs = get_requirements()
    week_ago = WEEK_START_STR
    month_ago = MONTH_START_STR

    # 本周统计
    new_this_week = [r for r in reqs if r.get("created_at", "") >= week_ago]
    completed_this_week = [r for r in reqs if r.get("actual_completion_date", "") >= week_ago and r.get("status") == "已完成"]
    terminated_this_week = [r for r in reqs if r.get("actual_completion_date", "") >= week_ago and r.get("status") == "已终止"]

    col1, col2, col3 = st.columns(3)
    col1.metric("本周新增", len(new_this_week))
    col2.metric("本周完成", len(completed_this_week))
    col3.metric("本周终止", len(terminated_this_week))

    # 部门分布变化（本月 vs 上月）
    st.markdown("---")
    st.markdown("#### 按部门分布（本月新增）")
    from collections import Counter
    month_new = [r for r in reqs if r.get("created_at", "") >= month_ago]
    source_cnt = Counter(r.get("source", "其他") for r in month_new)
    total_month = max(len(month_new), 1)
    for src, cnt in source_cnt.most_common():
        pct = cnt / total_month * 100
        color = "#E53935" if src == "客服" and pct > 30 else "#1E88E5"
        st.markdown(
            f'<div style="margin-bottom:6px;"><span style="display:inline-block;width:120px;">{src}</span>'
            f'<span style="display:inline-block;background:{color};height:14px;width:{pct*3:.0f}px;border-radius:3px;vertical-align:middle;margin:0 8px;"></span>'
            f'<b>{cnt}</b> ({pct:.0f}%)</div>',
            unsafe_allow_html=True,
        )

    # 高负载工程师 Top 3
    st.markdown("---")
    st.markdown("#### 高负载工程师 Top 3（在跑需求工作量）")
    engineers = [u for u in USERS if u["role"] in ("工程师", "项目经理")]
    load_map = _user_load_map()
    eng_loads = sorted([(u["name"], load_map.get(u["id"], 0.0)) for u in engineers], key=lambda x: -x[1])[:3]
    for i, (name, load) in enumerate(eng_loads, 1):
        medal = ["🥇", "🥈", "🥉"][i-1]
        color = "#E53935" if load > 15 else "#FB8C00" if load > 8 else "#43A047"
        st.markdown(
            f'<div style="padding:8px;background:white;border-left:4px solid {color};margin:6px 0;border-radius:4px;">'
            f'{medal} <b>{name}</b> &nbsp; <span style="color:{color};font-weight:600;">{load:.1f} 人天</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # AI 一句话提示
    st.markdown("---")
    st.markdown("#### AI 一句话提示")
    if st.button("🤖 生成中心简报提示", type="primary", key="gen_center_tip"):
        active_count = sum(1 for r in reqs if r.get("status") in ("待启动", "进行中", "暂停"))
        stats = {
            "new_count": len(new_this_week),
            "completed_count": len(completed_this_week),
            "terminated_count": len(terminated_this_week),
            "active_count": active_count,
            "source_top": source_cnt.most_common(1)[0] if source_cnt else ("—", 0),
        }
        with st.spinner("AI 生成中…"):
            result = ai_helper.generate_weekly_briefing(
                user_name="研发中心",
                stats={
                    "new_count": stats["new_count"],
                    "completed_count": stats["completed_count"],
                    "active_count": stats["active_count"],
                    "due_count": 0,
                    "done_count": stats["completed_count"],
                    "pending_count": 0,
                    "blocked_count": sum(1 for r in reqs if r.get("status") == "暂停"),
                },
                high_pri_list=[r["id"] for r in reqs if r.get("priority") in ("P0", "P1") and r.get("status") not in ("已完成", "已终止")],
                provider=st.session_state.provider,
                api_key=st.session_state.api_key,
                model=st.session_state.model or None,
            )
        if result.success:
            for tip in (result.data or []):
                st.markdown(f"- {tip}")
            st.caption(f"耗时 {result.latency_ms}ms")
        else:
            st.error(f"❌ 生成失败：{result.error}")
            st.info("提示：仍可参考上方数据自行判断。")


# ============================================================
# 页面：AI 对话搜索（A5C，多轮；原 A5 单轮模板解析保留为兜底管道）
# ============================================================

def _chat_req_context_lines(matched: list[dict], limit: int = 50) -> list[str]:
    """把匹配需求压成给 LLM 的精简摘要行（第二阶段的数据回填）。
    字段清单即模型「看得见」的世界——漏一个字段，模型就无法就该字段作答
    （实测踩过：首版漏了提出人，用户按提出人查时模型只能答"没有该字段"）。"""
    lines = []
    for r in matched[:limit]:
        scores = [v.get("score") for v in _ratings(r).values() if isinstance(v, dict) and v.get("score")]
        rating = f"{sum(scores) / len(scores):.1f}" if scores else "无"
        requester = USER_ID_TO_NAME.get(r.get("requester_id", ""), "未知")
        customer = r.get("customer") or "无"
        tags = "、".join(r.get("tags") or []) or "无"
        lines.append(
            f'{r["id"]}｜{r.get("title", "")}｜状态:{r.get("status", "")}｜优先级:{r.get("priority", "")}'
            f'｜提出:{requester}｜执行:{_owner_names(r)}｜部门:{r.get("source", "")}'
            f'｜客户:{customer}｜标签:{tags}'
            f'｜创建:{r.get("created_at", "")}｜截止:{r.get("requested_due_date") or "无"}｜评分:{rating}'
        )
    return lines


def _chat_apply_plan_filter(f: dict) -> list[dict]:
    """执行 A5C 规划出的检索条件：固定字段走 apply_filter，
    自由关键词 / 排序 / 上限是 A5C 新增的，在本页后处理（apply_filter 本体不动）。"""
    matched = apply_filter(get_requirements(), f)
    kw = str(f.get("keyword") or "").strip()
    if kw:
        def _hit(r: dict) -> bool:
            hay = " ".join([
                r.get("title", ""), r.get("description", ""), r.get("customer") or "",
                " ".join(r.get("tags") or []), _owner_names(r), r.get("id", ""),
            ])
            return kw in hay
        matched = [r for r in matched if _hit(r)]
    sort = f.get("sort")
    if sort == "created_asc":
        matched.sort(key=lambda r: r.get("created_at", ""))
    elif sort == "due_asc":
        matched.sort(key=lambda r: r.get("requested_due_date") or "9999")
    else:
        matched.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    try:
        limit = min(int(f.get("limit") or 20), 50)
    except (TypeError, ValueError):
        limit = 20
    return matched[:limit]


def _chat_search_turn(text: str) -> None:
    """执行一轮对话：规划检索 → 本地过滤 → 数据回填 → 作答。
    结果（含命中的需求列表）写入 st.session_state["chat_msgs"]，供界面渲染。"""
    msgs = st.session_state.setdefault("chat_msgs", [])
    msgs.append({"role": "user", "content": text})

    provider = st.session_state.provider
    api_key = st.session_state.api_key
    model = st.session_state.model or None

    def _fallback(note: str) -> None:
        matched = simple_keyword_match(text, get_requirements(), top_n=5)
        reply = note + (f"按关键词找到 {len(matched)} 条相关需求，请看下方卡片。" if matched else "关键词匹配也没有结果，请换个说法。")
        msgs.append({"role": "assistant", "content": reply, "matched": matched, "filter": None})

    # 未配置 API Key：本地关键词兜底（与旧 A5 的降级策略一致）
    if not api_key:
        _fallback("⚠️ 未配置 API Key（侧边栏 ⚙️ LLM 配置），已用本地关键词匹配兜底，结果可能不准。\n\n")
        return

    # 给两段共用的历史：不含本轮（本轮内容单独传）
    history = [{"role": m["role"], "content": m["content"]} for m in msgs[:-1]][-12:]
    last_filter = None
    for m in reversed(msgs[:-1]):
        if m["role"] == "assistant" and m.get("filter"):
            last_filter = m["filter"]
            break

    # ── 第一段：规划检索 ──
    with st.spinner("AI 正在解析检索条件…"):
        plan = ai_helper.chat_search_plan(
            question=text, history=history, last_filter=last_filter,
            today=TODAY_STR, provider=provider, api_key=api_key, model=model,
        )
    if not plan.success:
        _fallback(f"⚠️ AI 解析失败（{plan.error}），已用本地关键词匹配兜底。\n\n")
        return

    data = plan.data if isinstance(plan.data, dict) else {}
    if data.get("action") == "answer":
        msgs.append({"role": "assistant", "content": str(data.get("direct_reply") or "…").strip(), "matched": [], "filter": None})
        return

    # ── 本地执行检索 ──
    f = data.get("filter") if isinstance(data.get("filter"), dict) else {}
    matched = _chat_apply_plan_filter(f)

    # ── 第二段：数据回填作答 ──
    lines = _chat_req_context_lines(matched)
    ctx = f"共匹配 {len(matched)} 条：\n" + "\n".join(lines) if lines else "共匹配 0 条。"
    with st.spinner("AI 正在基于检索结果作答…"):
        ans = ai_helper.chat_search_answer(
            question=text, data_context=ctx, history=history,
            provider=provider, api_key=api_key, model=model,
        )
    reply = ans.raw if ans.success else f"⚠️ 作答失败（{ans.error}）。找到 {len(matched)} 条匹配，请直接看下方卡片。"
    msgs.append({"role": "assistant", "content": reply, "matched": matched[:20], "filter": f})


def _chat_req_detail_lines(r: dict) -> str:
    """单条需求的完整信息（detail 工具的数据回填，也是模型看得见的「详情世界」）。"""
    ratings = _ratings(r)
    rating_bits = [f'{USER_ID_TO_NAME.get(uid, uid)}:{v.get("score")}星'
                   for uid, v in ratings.items() if isinstance(v, dict) and v.get("score")]
    subs = r.get("sub_requirements") or []
    sub_bits = [f'{s.get("id")}「{s.get("title")}」{s.get("status")}' for s in subs]
    atts = [a.get("filename", "?") for a in (r.get("attachments") or []) if isinstance(a, dict)]
    comments = [c for c in (r.get("comments") or []) if isinstance(c, dict)]
    cmt_bits = [f'[{c.get("time", "")}] {c.get("user", "")}: {c.get("text", "")}' for c in comments[-8:]]
    return (
        f'需求 {r["id"]}「{r.get("title", "")}」\n'
        f'状态:{r.get("status", "")}｜优先级:{r.get("priority", "")}\n'
        f'描述:{r.get("description") or "（无）"}\n'
        f'提出人:{USER_ID_TO_NAME.get(r.get("requester_id", ""), "未知")}'
        f'｜执行人:{_owner_names(r)}｜部门:{r.get("source", "")}\n'
        f'客户:{r.get("customer") or "无"}｜标签:{"、".join(r.get("tags") or []) or "无"}\n'
        f'创建:{r.get("created_at", "")}｜截止:{r.get("requested_due_date") or "无"}\n'
        f'评分:{"；".join(rating_bits) or "无"}\n'
        f'子需求:{"；".join(sub_bits) or "无"}\n'
        f'附件:{"、".join(atts) or "无"}\n'
        f'最近评论:\n' + ("\n".join(cmt_bits) if cmt_bits else "（无）")
    )


def _render_req_card(r: dict, key: str) -> None:
    """对话气泡内的单张需求卡片；key 为「查看」按钮的去重键。"""
    st.markdown(
        f'<div style="padding:10px;background:white;border:1px solid #ECEFF1;border-radius:6px;margin:6px 0;">'
        f'{status_badge(r["status"])} {priority_badge(r.get("priority", "未知"))}'
        f' &nbsp;<b>{r["id"]}</b> 「{r["title"]}」'
        f'<div style="color:#666;font-size:12px;margin-top:4px;">执行人：{_owner_names(r)} · 部门：{r.get("source", "")} · 创建：{r.get("created_at", "")}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    if st.button("查看", key=key, use_container_width=True):
        st.session_state["selected_req_id"] = r["id"]
        st.session_state["_pending_nav"] = True
        st.session_state["_pending_nav_target"] = "📄 需求详情"
        st.rerun()


def _render_matched_cards(msg_index: int, matched: list[dict]) -> None:
    for r in (matched or [])[:20]:
        _render_req_card(r, key=f"chat_card_{msg_index}_{r['id']}")


def _render_chat_turn(i: int, m: dict) -> None:
    """渲染一轮对话消息（含自校验标记与命中的需求卡片）。i 为消息在 chat_msgs 的下标（按钮 key 去重）。"""
    with st.chat_message(m["role"], avatar="user" if m["role"] == "user" else "assistant"):
        st.markdown(m["content"])
        if m.get("verify_warning"):
            st.warning(m["verify_warning"])  # 自校验未通过的标记要随历史一起留存
        _render_matched_cards(i, m.get("matched"))


_MAX_TOOL_STEPS = 4  # 智能体单轮最多工具调用次数（成本护栏，防死循环）
_MAX_FORMAT_RETRY = 2  # 工具调用 JSON 格式错误的自我修正次数（超过才降级兜底）
_TOOL_HEAD_CHARS = 400  # 判定「工具调用 vs 最终回答」时先缓冲的字符数
_MAX_TOOL_RESULT_CHARS = 8000  # 单条工具结果注入上下文的上限（成本护栏，超出截断并告知模型）

# 筛选协议里的字段名：模型偶尔会忘记 action 包装、直接输出 {"status":"进行中"}，
# 命中这些键就按 search 处理（否则会被当成最终回答，把原始 JSON 吐给用户）。
_FILTER_KEYS = {
    "keyword", "owner", "requester", "source", "status", "priority", "keywords",
    "project", "created_after", "created_before", "due_before", "sort", "limit",
}


def _chat_is_tool_call(head: str) -> bool:
    """判定模型这段输出是工具调用还是最终回答。

    ⚠️ 不能只看首字符是否为 `{`（首版就是这么写的，是个真 bug）：模型可能先写一句
    「好的，我来查询一下」再给 JSON，也可能用 ```json 围栏包裹——只看首字符会把
    原始 JSON 当成回答直接吐给用户。这里改用 parse_json_response 判别（它本身
    容忍前后多余文字与代码块围栏），并额外兼容两种常见偏差：
      ① 前后被文字包裹导致整段解析不出 JSON → 只要出现协议标记就按工具调用处理，
         收全后若仍解析失败，会走「格式自愈」让模型重发，而不是把 JSON 当回答展示；
      ② 忘记 action 包装、直接给出筛选条件 → 命中已知筛选字段即按调用处理。
    """
    data, _ = parse_json_response(head)
    if isinstance(data, dict):
        if "action" in data:
            return True
        if set(data.keys()) & _FILTER_KEYS:
            return True
    if '"action"' in head or "'action'" in head:
        return True
    # 兜底信号：开头就是 { 或 ``` 围栏 → 几乎必然是工具调用尝试。
    # 这条是为了覆盖「JSON 比缓冲长度还长、且 action 键排在截断点之后」的情形——
    # 那种情况下前面几条信号都会失效，而按回答展示会把半截 JSON 吐给用户。
    # 代价：极少数以 JSON/代码块开头的自然语言回答会被当作调用尝试，走一次格式自愈重答。
    return head.lstrip().startswith("{") or head.lstrip().startswith("```")


# ---- 只读聚合工具（P0）----
# 设计要点：全部复用页面已有的聚合函数，不新写统计口径——否则同一个数字在对话里
# 和页面上会不一致，比答不出来更糟。权限沿用系统评估页的口径（_can_view_rating_detail）：
# 逐人活跃明细等点名式数据只对管理层开放，工程师问到时只给汇总。

def _tool_engineer_load() -> str:
    """工具：工程师负载（谁手上多少条在跑）。复用总览页同一套聚合（_user_task_count_map/_user_load_map）。"""
    counts = _user_task_count_map()
    loads = _user_load_map()
    if not counts:
        return "当前没有处于「待启动/进行中/暂停」的需求被指派给任何人。"
    rows = sorted(((cnt, loads.get(uid, 0.0), USER_ID_TO_NAME.get(uid, uid))
                   for uid, cnt in counts.items()), reverse=True)
    lines = [f"{name}：{cnt} 条在跑，估算工作量 {effort:.1f} 天" for cnt, effort, name in rows]
    return f"执行人负载（在跑需求，共 {len(rows)} 人，按任务数降序）：\n" + "\n".join(lines)


def _tool_stats_overview() -> str:
    """工具：整体状况（总量/状态/优先级/部门分布、逾期、满意度）。复用 satisfaction_stats 同一口径。"""
    reqs = get_requirements()
    if not reqs:
        return "系统里暂无需求。"
    by_status = Counter(r.get("status", "未知") for r in reqs)
    active = [r for r in reqs if r.get("status") in ("需求池", "待启动", "进行中", "暂停")]
    by_pri = Counter(r.get("priority", "未知") for r in active)
    by_src = Counter(r.get("source", "未分类") for r in reqs)
    overdue = [r for r in active
               if r.get("requested_due_date") and r["requested_due_date"] < TODAY_STR]
    sat = satisfaction_stats(reqs)
    def _fmt(c: Counter) -> str:
        return "；".join(f"{k} {v}" for k, v in c.most_common() if k)
    lines = [
        f"需求总数 {len(reqs)} 条；其中在跑（需求池/待启动/进行中/暂停）{len(active)} 条",
        f"按状态：{_fmt(by_status)}",
        f"在跑的按优先级：{_fmt(by_pri)}",
        f"按部门：{_fmt(by_src)}",
        f"逾期未完成：{len(overdue)} 条"
        + ("（" + "、".join(f'{r["id"]}「{r.get("title", "")}」截止 {r.get("requested_due_date")}'
                          for r in overdue[:5]) + "）" if overdue else ""),
        f"需求方满意度：平均 {sat['avg']:.1f}/5，已评 {sat['rated']}/{sat['expected']} 人次，"
        f"满意率（4-5星）{sat['positive_rate']:.0f}%",
    ]
    return "\n".join(lines)


def _tool_improvement_status() -> str:
    """工具：改进闭环健康度。复用 improvement_health（与系统评估页同一口径）。"""
    imps = db.load_improvements(with_proposer=_can_view_rating_detail())
    h = improvement_health(imps)
    if h["total"] == 0:
        return "暂无改进项（用户在问卷中提交建议会自动进入闭环）。"
    by_status = "；".join(f"{k} {v}" for k, v in h["by_status"].most_common() if k)
    lines = [
        f"改进项合计 {h['total']} 条：{by_status}",
        f"待处理积压 {h['backlog']} 条" + ("（已超过 3 条预警线）" if h.get("backlog_warn") else ""),
    ]
    if h.get("terminal"):
        lines.append(f"已处置终项 {h['terminal']} 条，平均处理时长 {h['avg_days']:.1f} 天，"
                     f"回告率 {h['notify_rate']:.0f}%")
    return "\n".join(lines)


def _tool_activity_stats() -> str:
    """工具：使用活跃（DAU/WAU/MAU + 累计使用量 + 页面热度 + 逐人明细）。逐人明细沿用管理层门控。"""
    a = db.activity_stats()
    if not a.get("has_data"):
        return "暂无活跃记录（埋点自上线后开始累积，历史访问无法回溯）。"
    lines = [f"近 24 小时活跃 {a.get('dau', 0)} 人；近 7 天 {a.get('wau', 0)} 人；近 30 天 {a.get('mau', 0)} 人",
             f"累计：注册用户 {a.get('total_users', 0)} 人，登录 {a.get('total_logins', 0)} 次，"
             f"页面访问 {a.get('total_views', 0)} 次"]
    heat = a.get("page_heat") or []
    if heat:
        lines.append("页面热度（近 30 天访问次数）：" + "；".join(f"{p} {c}" for p, c in heat[:6]))
    if _can_view_rating_detail() and a.get("user_activity"):
        lines.append("逐人活跃（近 30 天，仅管理层可见）：" + "；".join(
            f'{u["user_name"] or u["user_id"]} 访问 {u["visits"]} 次/{u["active_days"]} 天'
            for u in a["user_activity"][:10]))
    return "\n".join(lines)


# action → (过程面板显示名, 无参工具函数)
_NO_ARG_TOOLS = {
    "engineer_load": ("👷 查看工程师负载", _tool_engineer_load),
    "stats_overview": ("📊 查看整体状况", _tool_stats_overview),
    "improvement_status": ("🔄 查看改进闭环", _tool_improvement_status),
    "activity_stats": ("👥 查看使用活跃", _tool_activity_stats),
}


def _chat_whitelist_from_history(msgs: list) -> set:
    """自校验白名单的初值：此前各轮助手消息已验证过的编号。

    为什么要带上历史：对话内已核实过的编号，在后续轮次里被再次引用（如「那条呢」）
    不算编造；只比对「本轮工具结果」会产生这种跨轮误报。
    """
    ids: set[str] = set()
    for m in msgs:
        if m.get("role") == "assistant":
            ids |= set(m.get("ids") or ())
    return ids


def _chat_verify_reply(reply: str, allowed_ids: set) -> str:
    """回答自校验（P0）：机械比对回答里引用的需求编号与工具真实返回的编号。

    为什么需要：模型可能编造编号，这是最伤信任的失败模式；而用正则比对是零成本、
    确定性的拦截手段（比换更强的模型有效）。命中未知名时返回提示文案，否则返回空串。
    """
    cited = set(ai_helper.extract_req_ids(reply or ""))
    unknown = sorted(cited - set(allowed_ids or ()))
    if not unknown:
        return ""
    known = "、".join(sorted(allowed_ids)) if allowed_ids else "无（本轮没有检索到任何需求）"
    return (f"⚠️ 自校验未通过：以下编号没有出现在本轮工具结果中，可能不准确 —— "
            f"{'、'.join(unknown)}。本轮真实命中的编号：{known}")


def _chat_search_turn(text: str) -> None:
    """智能体一轮对话（ReAct 式工具循环 + 流式回答）。

    每步模型输出二选一：JSON 工具调用（search/detail，本地执行后结果回填继续循环）
    或纯文本最终回答（st.write_stream 打字机式渲染）。
    本函数**自行渲染**本轮的用户气泡与回答——page_search 的历史循环只画进入页面时
    已存在的消息，二者配合保证同一轮内不重复渲染、也不依赖整页 rerun。
    """
    msgs = st.session_state.setdefault("chat_msgs", [])
    msgs.append({"role": "user", "content": text})
    with st.chat_message("user", avatar="user"):
        st.markdown(text)

    provider = st.session_state.provider
    api_key = st.session_state.api_key
    model = st.session_state.model or None

    def _fallback(note: str) -> None:
        m_list = simple_keyword_match(text, get_requirements(), top_n=5)
        reply = note + (f"按关键词找到 {len(m_list)} 条相关需求，请看下方卡片。" if m_list
                        else "关键词匹配也没有结果，请换个说法。")
        msgs.append({"role": "assistant", "content": reply, "matched": m_list, "filter": None,
                     "ids": [r["id"] for r in m_list]})  # 关键词命中的编号也是真实的
        with st.chat_message("assistant", avatar="assistant"):
            st.markdown(reply)
            _render_matched_cards(len(msgs) - 1, m_list)

    # 未配置 API Key：本地关键词兜底
    if not api_key:
        _fallback("⚠️ 未配置 API Key（mvp/.env 或侧边栏 ⚙️ LLM 配置），已用本地关键词匹配兜底，结果可能不准。\n\n")
        return

    history = [{"role": m["role"], "content": m["content"]} for m in msgs[:-1]][-12:]
    tool_log: list[dict] = []
    matched: list[dict] = []
    # 自校验白名单：语义 =「工具结果文本里真实出现过的需求编号」+「本对话此前已核实过的编号」。
    # 与具体是哪个工具无关（首版只从 search/detail 收集，导致聚合工具 stats_overview
    # 逾期清单里的真实编号被误判为编造——用户实测踩到，已修）。
    allowed_ids: set[str] = _chat_whitelist_from_history(msgs)
    bad_format = 0                 # 工具调用 JSON 连续格式错误计数（自愈用）

    def _note_tool_result(tag: str, ctx: str) -> None:
        """把工具结果写入对话，并把其中出现的编号纳入自校验白名单（唯一入口）。

        白名单从**完整文本**提取（截断前），上下文则按上限截断——两者顺序不能反，
        否则被截掉的编号会被误判为编造。
        """
        allowed_ids.update(ai_helper.extract_req_ids(ctx))
        shown = ctx
        if len(shown) > _MAX_TOOL_RESULT_CHARS:
            shown = shown[:_MAX_TOOL_RESULT_CHARS] + (
                f"\n…（结果过长已截断，共 {len(ctx)} 字符；如需完整数据请缩小检索范围）")
        tool_log.append({"role": "user", "content": f"【工具结果·{tag}】\n{shown}"})

    status = st.status("🤖 AI 正在工作…", expanded=True)
    try:
        for _step in range(1, _MAX_TOOL_STEPS + 1):
            gen = ai_helper.agent_stream(
                today=TODAY_STR, history=history, question=text, tool_log=tool_log,
                provider=provider, api_key=api_key, model=model,
            )
            first = next(gen, None)
            if first is None:
                raise RuntimeError("模型没有输出")
            # 先缓冲一段再判别（见 _chat_is_tool_call 的说明：只看首字符会误判）
            head = first
            while len(head) < _TOOL_HEAD_CHARS:
                nxt = next(gen, None)
                if nxt is None:
                    break
                head += nxt
            if not _chat_is_tool_call(head):
                # 最终回答：纯文本，流式渲染（官方 chat 范式：此处绝不调 st.rerun）
                status.update(label="✅ 回答完成", state="complete", expanded=False)

                def _chain():
                    yield head
                    yield from gen

                with st.chat_message("assistant", avatar="assistant"):
                    reply = st.write_stream(_chain())
                    # 回答自校验（P0）：编号对不上就明确标注，且随历史一起留存
                    warn = _chat_verify_reply(reply, allowed_ids)
                    if warn:
                        st.warning(warn)
                    msgs.append({"role": "assistant", "content": reply, "matched": matched[:20],
                                 "filter": None, "verify_warning": warn,
                                 "ids": sorted(allowed_ids)})  # 供后续轮次做跨轮白名单
                    _render_matched_cards(len(msgs) - 1, matched)
                return

            # 工具调用：收全剩余增量后解析 JSON
            full = head + "".join(gen)
            data, err = parse_json_response(full)
            if err or not isinstance(data, dict):
                # 失败自愈（P0）：把格式错误回填给模型让它自己修，连续错才降级兜底。
                # 不做自愈的话，模型偶尔少个引号就会让"智能体"退化成哑巴关键词搜索。
                bad_format += 1
                tool_log.append({"role": "assistant", "content": full})
                if bad_format >= _MAX_FORMAT_RETRY:
                    raise RuntimeError(f"连续 {bad_format} 次工具调用格式错误：{err}")
                status.write("⚠️ 工具调用格式有误，已要求模型自我修正")
                tool_log.append({"role": "user", "content":
                                 f"【系统提示】上一条输出不是合法 JSON（{err}）。请重新输出**一行合法 JSON**"
                                 f"工具调用，或直接输出纯文本最终回答。可用 action："
                                 f"search / detail / {' / '.join(_NO_ARG_TOOLS)}。"})
                continue
            action = data.get("action")
            filter_only = data.get("filter") if isinstance(data.get("filter"), dict) else None
            if action is None and set(data.keys()) & _FILTER_KEYS:
                # 模型忘记 action 包装、直接给了筛选条件：按 search 处理（不让它白跑一轮自愈）
                action, filter_only = "search", data
                status.write("⚠️ 模型未给 action，已按「检索」解释其输出")
            tool_log.append({"role": "assistant", "content": full})
            if action == "search":
                f = filter_only if filter_only is not None else (
                    data.get("filter") if isinstance(data.get("filter"), dict) else {})
                status.write(f"🔍 检索：{json.dumps(f, ensure_ascii=False) if f else '（全部）'}")
                matched = _chat_apply_plan_filter(f)
                status.write(f"→ 命中 {len(matched)} 条")
                lines = _chat_req_context_lines(matched)
                _note_tool_result("检索", f"共 {len(matched)} 条：\n"
                                          + ("\n".join(lines) if lines else "（无匹配）"))
            elif action == "detail":
                rid = str(data.get("id") or "").strip()
                status.write(f"📄 查看详情：{rid}")
                r = get_requirement(rid) if rid else None
                if r:
                    matched = [r]
                    _note_tool_result("详情", _chat_req_detail_lines(r))
                else:
                    _note_tool_result("详情", f"{rid or '(空)'} 不存在，请先用 search 确认 ID。")
            elif action in _NO_ARG_TOOLS:
                label, fn = _NO_ARG_TOOLS[action]
                status.write(f"{label}…")
                try:
                    ctx = fn()
                except Exception as tool_err:  # noqa: BLE001
                    ctx = f"（工具执行失败：{tool_err}）"
                _note_tool_result(action, ctx)
            else:
                tool_log.append({"role": "user",
                                 "content": f"未知 action {action!r}。可用：search / detail / "
                                            f"{' / '.join(_NO_ARG_TOOLS)}；信息足够时直接输出纯文本最终回答。"})
        raise RuntimeError(f"连续 {_MAX_TOOL_STEPS} 步仍在调用工具，已中止（请换个更明确的问法）")
    except Exception as e:  # noqa: BLE001 —— 循环内任何失败统一走本地兜底
        try:
            status.update(label="⚠️ 智能体出错", state="error", expanded=False)
        except Exception:
            pass
        _fallback(f"⚠️ 智能体出错（{e}），已用本地关键词匹配兜底。\n\n")


def page_search():
    st.markdown("## 🔍 AI 对话搜索")
    st.caption("像和熟悉需求的同事提问：可直接问、可连续追问；AI 会自动检索、必要时查看需求详情，"
               "回答基于真实数据生成。卡片点「查看」进入详情。对话在本页会话内保留，刷新后清空。")

    msgs = st.session_state.setdefault("chat_msgs", [])

    # 顶部工具行：清空对话（会话内历史，不涉及持久化）
    _t1, _t2 = st.columns([5, 1])
    with _t2:
        if msgs and st.button("🗑 清空对话", use_container_width=True):
            st.session_state["chat_msgs"] = []
            st.rerun()

    # 先渲染既有历史——本轮新增的消息由 _chat_search_turn 自行渲染，二者不重叠
    for i, m in enumerate(msgs):
        _render_chat_turn(i, m)

    # 顶栏全局搜索联动：查询词自动成为对话第一句（在历史之后处理，渲染顺序才正确）
    _prefill = st.session_state.pop("_search_prefill", "").strip()
    if _prefill:
        _chat_search_turn(_prefill)

    # 底部输入。⚠️ 提交后由 _chat_search_turn 本轮直接渲染（流式），**绝不调 st.rerun()**
    # （bug2 根因：chat_input 的值只在一次完整运行结束后才被前端清空，中途 rerun 会
    #  反复处理同一提问，页面陷入重跑循环、侧边栏点击被吞）。
    prompt = st.chat_input("提问或追问，例：员工01提出的进行中需求 → 那其中哪条风险最大？")
    if prompt:
        _chat_search_turn(prompt)


def apply_filter(reqs: list[dict], f: dict | None) -> list[dict]:
    if not f or not isinstance(f, dict):
        return reqs
    if f.get("error"):
        return []
    result = []
    for r in reqs:
        ok = True
        # owner 模糊匹配
        if "owner" in f and f["owner"]:
            owner_names = [USER_ID_TO_NAME.get(i, "") for i in r.get("owner_ids", []) if i]
            hit = False
            for name in owner_names:
                if f["owner"] in name or name in f["owner"] or any(tok in name for tok in [f["owner"][:2], f["owner"][-2:]]):
                    hit = True
                    break
            if not hit:
                ok = False
        if "requester" in f and f["requester"]:
            requester_name = USER_ID_TO_NAME.get(r.get("requester_id", ""), "")
            if f["requester"] not in requester_name:
                ok = False
        if "source" in f and f["source"]:
            if r.get("source") != f["source"]:
                ok = False
        if "priority" in f and f["priority"]:
            if r.get("priority") != f["priority"]:
                ok = False
        if "status" in f and f["status"]:
            if r.get("status") != f["status"]:
                ok = False
        if "project" in f and f["project"]:
            proj = next((p for p in get_projects() if p["id"] == r.get("project_id", "")), None)
            if not proj or f["project"] not in proj["name"]:
                ok = False
        # 时间范围
        created = r.get("created_at", "")
        if "created_after" in f and f["created_after"]:
            if created < f["created_after"]:
                ok = False
        if "created_before" in f and f["created_before"]:
            if created > f["created_before"]:
                ok = False
        if "due_before" in f and f["due_before"]:
            if r.get("requested_due_date", "9999") > f["due_before"]:
                ok = False
        # 关键词
        if "keywords" in f and f["keywords"]:
            text = (r.get("title", "") + r.get("description", ""))
            if not all(kw in text for kw in f["keywords"]):
                ok = False
        if ok:
            result.append(r)
    return result


# ============================================================
# 页面：周报（A6）
# ============================================================

def page_weekly():
    st.markdown("## 📅 周报自动生成（A6）")
    st.caption("个人简报与中心简报")

    tab_personal, tab_center = st.tabs(["👤 个人简报", "🏢 中心简报"])

    with tab_personal:
        _render_personal_briefing()

    with tab_center:
        _render_center_briefing()


def _render_personal_briefing():
    """A6 个人简报（原 page_weekly 内容）"""
    st.markdown("### 👤 个人简报")

    user_options = {u["id"]: f'{u["name"]}（{u["role"]}）' for u in USERS}
    selected = st.selectbox("选择用户", options=list(user_options.keys()), format_func=lambda x: user_options[x])
    user_name = USER_ID_TO_NAME.get(selected, "")

    # 计算本周统计
    week_ago = WEEK_START_STR
    user_reqs = [r for r in get_requirements() if selected in r.get("owner_ids", [])]

    new_count = sum(1 for r in user_reqs if r.get("created_at", "") >= week_ago)
    due_count = sum(1 for r in user_reqs if r.get("requested_due_date", "") >= TODAY_STR and r.get("requested_due_date", "") <= TODAY_STR + timedelta(days=7).__str__()[0:10])
    due_count = sum(1 for r in user_reqs if (TODAY.strftime("%Y-%m-%d") <= r.get("requested_due_date", "9999") <= (TODAY + timedelta(days=7)).strftime("%Y-%m-%d")))
    done_count = sum(1 for r in user_reqs if r.get("actual_completion_date", "") >= week_ago)
    pending_count = due_count - done_count if due_count > done_count else 0
    blocked_count = sum(1 for r in user_reqs if r.get("status") == "暂停")
    completed_count = done_count
    active_count = sum(1 for r in user_reqs if r.get("status") in ["待启动", "进行中", "暂停"])
    high_pri_list = [r["id"] for r in user_reqs if r.get("priority") in ["P0", "P1"] and r.get("status") not in ["已完成", "已终止"]]

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("本周新增到我名下", new_count)
    with col2:
        st.metric("本周截止", due_count)
    with col3:
        st.metric("阻塞超 3 天", blocked_count)
    with col4:
        st.metric("当前在跑", active_count)

    st.markdown("---")
    if st.button("🤖 AI 生成简报提示", type="primary"):
        stats = {
            "new_count": new_count,
            "due_count": due_count,
            "done_count": done_count,
            "pending_count": pending_count,
            "blocked_count": blocked_count,
            "completed_count": completed_count,
            "active_count": active_count,
        }
        with st.spinner("AI 正在生成简报…"):
            result = ai_helper.generate_weekly_briefing(
                user_name=user_name,
                stats=stats,
                high_pri_list=high_pri_list,
                provider=st.session_state.provider,
                api_key=st.session_state.api_key,
                model=st.session_state.model or None,
            )
        if result.success:
            st.session_state["last_briefing"] = {
                "tips": result.data,
                "latency_ms": result.latency_ms,
            }
        else:
            st.session_state["last_briefing"] = None
            st.error(f"❌ 生成失败：{result.error}")

    last = st.session_state.get("last_briefing")
    if last:
        st.markdown("#### AI 简报提示")
        for tip in last.get("tips", []):
            st.markdown(f"- {tip}")
        st.caption(f"耗时 {last['latency_ms']}ms")

    # 高优先级未完成列表
    st.markdown("---")
    st.markdown("#### 高优先级未完成需求")
    if high_pri_list:
        for r in user_reqs:
            if r["id"] in high_pri_list:
                st.markdown(
                    f'- {priority_badge(r.get("priority",""))} {r["id"]} 「{r["title"]}」',
                    unsafe_allow_html=True,
                )
    else:
        st.info("无")


# ============================================================
# 页面：AI 能力总览（专门展示6个能力）
# ============================================================

def page_ai_showcase():
    st.markdown("## 🤖 AI 助手能力总览（A1~A6）")
    st.caption("本页集中演示 6 个 AI 能力，对应 PRD 第五章")

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "A1 信息抽取", "A2 相似检测", "A3 清晰度检查",
        "A4 自动打标签", "A5 自然语言搜索", "A6 周报生成",
    ])

    with tab1:
        st.markdown("**A1 · 需求信息抽取与字段预填**")
        st.caption("将自然语言文本抽取为结构化需求字段")
        demo_input = st.text_area(
            "输入",
            value="客户示例客户C集成的客户工程师昨天在群里反馈，说我们的测试设备在高速贴装时视觉定位精度不稳，部分 IC 偏移到 0.05mm，希望我们能优化到 0.02mm，下周三之前给方案。",
            height=120,
            key="a1_input",
        )
        if st.button("运行 A1", key="a1_run"):
            with st.spinner("AI 调用中…"):
                r = ai_helper.extract_requirement_fields(
                    user_input=demo_input,
                    provider=st.session_state.provider,
                    api_key=st.session_state.api_key,
                    model=st.session_state.model or None,
                )
            if r.success:
                st.json(r.data)
                st.caption(f"耗时 {r.latency_ms}ms · 模型 {r.model}")
            else:
                st.error(r.error)

    with tab2:
        st.markdown("**A2 · 相似需求检测**")
        st.caption("判断新需求与历史候选是否实质重复")
        new_title = st.text_input("新需求标题", value="测试设备增加 BARCODE 扫码功能", key="a2_title")
        new_desc = st.text_area("新需求描述", value="客户需要在测试设备的入料口增加条码扫描，用于产品追溯。", key="a2_desc")
        if st.button("运行 A2", key="a2_run"):
            candidates = simple_keyword_match(new_title + " " + new_desc, get_requirements(), top_n=3)
            if not candidates:
                st.info("未找到候选")
            else:
                with st.spinner(f"AI 复核 {len(candidates)} 条候选…"):
                    r = ai_helper.check_similarity(
                        new_title=new_title,
                        new_description=new_desc,
                        candidates=[{"id": c["id"], "title": c["title"], "description": c.get("description", "")} for c in candidates],
                        provider=st.session_state.provider,
                        api_key=st.session_state.api_key,
                        model=st.session_state.model or None,
                    )
                if r.success:
                    for item in r.data:
                        c = next((c for c in candidates if c["id"] == item.get("id")), None)
                        if c:
                            dup = item.get("duplicate", False)
                            color = "#E53935" if dup else "#43A047"
                            st.markdown(
                                f'<div style="padding:8px;border-left:3px solid {color};margin:6px 0;">'
                                f'{"🔁 重复" if dup else "✅ 不重复"} <b>{c["id"]}</b> 「{c["title"]}」'
                                f' &nbsp;<span style="color:{color};">置信度 {item.get("confidence",0):.2f}</span>'
                                f'<div style="color:#666;font-size:12px;">{item.get("reason","")}</div></div>',
                                unsafe_allow_html=True,
                            )
                    st.caption(f"耗时 {r.latency_ms}ms")
                else:
                    st.error(r.error)

    with tab3:
        st.markdown("**A3 · 需求清晰度检查**")
        st.caption("识别信息缺失或模糊表述，给出具体补充建议")
        c_title = st.text_input("标题", value="做一下设备的优化", key="a3_title")
        c_desc = st.text_area("描述", value="客户说要尽快把设备优化一下，做得好一点，差不多就行。", key="a3_desc")
        if st.button("运行 A3", key="a3_run"):
            with st.spinner("AI 检查中…"):
                r = ai_helper.check_clarity(
                    title=c_title, description=c_desc,
                    provider=st.session_state.provider, api_key=st.session_state.api_key,
                    model=st.session_state.model or None,
                )
            if r.success:
                if r.data:
                    for i, s in enumerate(r.data, 1):
                        st.warning(f"**{i}. {s.get('dimension','')}** — {s.get('suggestion','')}")
                else:
                    st.success("描述清晰，无补充建议")
                st.caption(f"耗时 {r.latency_ms}ms")
            else:
                st.error(r.error)

    with tab4:
        st.markdown("**A4 · 自动分类与打标签**")
        st.caption("从预设标签库推荐 1-3 个标签")
        t_title = st.text_input("标题", value="HAST-III 老化舱温箱控制板国产化替代", key="a4_title")
        t_desc = st.text_area("描述", value="现用进口温箱控制板交期 16 周，需评估国产方案，温度精度 ±0.5℃，MTBF ≥ 8000h。", key="a4_desc")
        if st.button("运行 A4", key="a4_run"):
            with st.spinner("AI 推荐中…"):
                r = ai_helper.auto_tag(
                    title=t_title, description=t_desc,
                    provider=st.session_state.provider, api_key=st.session_state.api_key,
                    model=st.session_state.model or None,
                )
            if r.success:
                for t in r.data:
                    conf = t.get("confidence", 0)
                    color = "#43A047" if conf >= 0.8 else "#FB8C00" if conf >= 0.5 else "#9E9E9E"
                    st.markdown(
                        f'<span style="background:{color}22;color:{color};padding:4px 12px;border-radius:12px;margin:4px;font-weight:600;">'
                        f'#{t.get("tag","")}（{conf:.2f}）</span>',
                        unsafe_allow_html=True,
                    )
                st.caption(f"耗时 {r.latency_ms}ms")
            else:
                st.error(r.error)

    with tab5:
        st.markdown("**A5 · 自然语言搜索**")
        st.caption("将自然语言查询翻译为结构化筛选条件")
        s_query = st.text_input("查询", value="员工28负责的进行中的 P1 需求", key="a5_query")
        if st.button("运行 A5", key="a5_run"):
            with st.spinner("AI 解析中…"):
                r = ai_helper.parse_search_query(
                    query=s_query, today=TODAY_STR,
                    provider=st.session_state.provider, api_key=st.session_state.api_key,
                    model=st.session_state.model or None,
                )
            if r.success:
                st.json(r.data)
                st.caption(f"耗时 {r.latency_ms}ms")
                matched = apply_filter(get_requirements(), r.data)
                st.info(f"匹配 {len(matched)} 条")
            else:
                st.error(r.error)

    with tab6:
        st.markdown("**A6 · 周报自动生成**")
        st.caption("基于本周数据生成 1-3 条简短提示")
        if st.button("运行 A6（对当前用户）", key="a6_run"):
            selected = st.session_state.current_user_id
            user_name = USER_ID_TO_NAME.get(selected, "")
            week_ago = WEEK_START_STR
            user_reqs = [r for r in get_requirements() if selected in r.get("owner_ids", [])]
            stats = {
                "new_count": sum(1 for r in user_reqs if r.get("created_at", "") >= week_ago),
                "due_count": sum(1 for r in user_reqs if TODAY_STR <= r.get("requested_due_date", "9999") <= (TODAY + timedelta(days=7)).strftime("%Y-%m-%d")),
                "done_count": sum(1 for r in user_reqs if r.get("actual_completion_date", "") >= week_ago),
                "pending_count": 0,
                "blocked_count": sum(1 for r in user_reqs if r.get("status") == "暂停"),
                "completed_count": sum(1 for r in user_reqs if r.get("actual_completion_date", "") >= week_ago),
                "active_count": sum(1 for r in user_reqs if r.get("status") in ["待启动", "进行中", "暂停"]),
            }
            stats["pending_count"] = max(stats["due_count"] - stats["done_count"], 0)
            high_pri = [r["id"] for r in user_reqs if r.get("priority") in ["P0", "P1"] and r.get("status") not in ["已终止", "已完成"]]

            with st.spinner("AI 生成中…"):
                r = ai_helper.generate_weekly_briefing(
                    user_name=user_name, stats=stats, high_pri_list=high_pri,
                    provider=st.session_state.provider, api_key=st.session_state.api_key,
                    model=st.session_state.model or None,
                )
            if r.success:
                st.json({"stats": stats, "tips": r.data})
                st.caption(f"耗时 {r.latency_ms}ms")
            else:
                st.error(r.error)


# ============================================================
# 页面：用户管理
# ============================================================

def _render_change_password(cur):
    """非管理层：仅可修改自己的密码。"""
    st.caption("个人账户：仅可修改自己的密码")
    with st.form("change_own_password"):
        new_pwd = st.text_input("新密码", type="password")
        confirm_pwd = st.text_input("确认新密码", type="password")
        submit = st.form_submit_button("💾 保存密码", type="primary", use_container_width=True)
    if submit:
        if not new_pwd.strip():
            st.error("密码不能为空")
        elif new_pwd != confirm_pwd:
            st.error("两次输入的密码不一致")
        else:
            db.save_user(
                {"id": cur["id"], "name": cur["name"], "role": cur["role"], "department": cur.get("department", "")},
                password=new_pwd.strip(),
            )
            reload_users()
            st.session_state["_flash"] = {"type": "success", "text": "✅ 密码已更新"}
            st.rerun()


def _render_user_list_section(users):
    """👥 现有用户：花名册表 + 无提醒渠道预警。"""
    if users:
        rows = [
            {"ID": u["id"], "姓名": u["name"], "角色": u["role"], "部门": u.get("department", ""),
             "手机号": u.get("phone") or "", "邮箱": u.get("email") or "",
             "钉钉匹配": "✓" if u.get("dingtalk_userid") else "",
             "提醒渠道": _user_notify_channel(u)}
            for u in users
        ]
        st.dataframe(rows, use_container_width=True)
        no_channel = [u["name"] for u in users if _user_notify_channel(u) == "⚠ 无"]
        if no_channel:
            st.warning(f"⚠ 以下用户无任何可达提醒渠道（不在提醒群且未填邮箱）：{'、'.join(no_channel)}")
    else:
        st.info("暂无用户")


def _render_user_sync_section(users):
    """🔄 钉钉通讯录同步（按姓名自动匹配，免填手机号/邮箱）。"""
    st.caption(
        "按姓名自动匹配钉钉用户并回填「钉钉 userid / 手机号」，之后钉钉提醒直接 @ 到人。"
        "需在 .env 配置企业内部应用 DINGTALK_APP_KEY / DINGTALK_APP_SECRET（钉钉开放平台创建，"
        "开通通讯录读取权限）。姓名需与钉钉一致且企业内无重名，否则会列出提示。"
    )
    if not dingtalk.app_configured():
        st.info("未配置企业内部应用（DINGTALK_APP_KEY / DINGTALK_APP_SECRET），暂不可同步。")
    elif st.button("🔄 按姓名同步", key="dingtalk_sync"):
        result = dingtalk.list_org_users()
        if result is None:
            st.error("同步失败：请检查 AppKey/AppSecret 是否正确、通讯录读取权限是否已开通（详见启动控制台日志）。")
        else:
            name_to_userid, name_to_mobile, ambiguous = result
            matched, unmatched = [], []
            for u in users:
                hit = name_to_userid.get(u["name"])
                if hit:
                    # 手机号只在应用有权限且用户尚未填写时回填
                    mobile = name_to_mobile.get(u["name"], "")
                    db.save_user({**u, "dingtalk_userid": hit,
                                  **({"phone": mobile} if mobile and not u.get("phone") else {})})
                    matched.append(u["name"])
                else:
                    unmatched.append(u["name"])
            reload_users()
            amb = [n for n in ambiguous if n in {u["name"] for u in users}]
            parts = [f"✅ 匹配成功 {len(matched)} 人"]
            if unmatched:
                parts.append(f"⚠ 未匹配（钉钉通讯录无同名）：{'、'.join(unmatched)}，请为其手工填手机号")
            if amb:
                parts.append(f"⚠ 重名跳过（企业内同名多人）：{'、'.join(amb)}，请为其手工填手机号")
            st.session_state["_flash"] = {"type": "success", "text": "；".join(parts)}
            st.rerun()


def _render_user_add_section(users):
    """➕ 新增用户。"""
    with st.form("add_user_form"):
        c1, c2 = st.columns(2)
        with c1:
            new_name = st.text_input("姓名 *")
            new_role = st.selectbox("角色", options=USER_ROLES, index=2)  # 默认工程师
            new_phone = st.text_input("手机号（钉钉@提醒用，可空）", placeholder="用于钉钉提醒时 @ 到人")
            new_email = st.text_input(
                "邮箱（邮件提醒用，留空自动按「姓名拼音@example.com」生成）",
                placeholder="用于接收邮件提醒",
            )
        with c2:
            new_dept = st.text_input("部门")
            new_pwd = st.text_input("初始密码 *", type="password", value="123456")
            new_in_group = st.checkbox("在钉钉提醒群中（不勾选则不群@；邮件提醒与是否勾选无关）", value=True)
        add_submit = st.form_submit_button("✅ 新增用户", type="primary", use_container_width=True)
    if add_submit:
        if not new_name.strip():
            st.error("姓名不能为空")
        elif any(u["name"] == new_name.strip() for u in users):
            st.error(f"姓名「{new_name.strip()}」已存在")
        elif not new_pwd.strip():
            st.error("初始密码不能为空")
        else:
            uid = f"U{db.next_user_seq():03d}"
            db.save_user(
                {"id": uid, "name": new_name.strip(), "role": new_role, "department": new_dept.strip(),
                 "phone": new_phone.strip(),
                 "email": new_email.strip() or _default_user_email(new_name.strip()),
                 "notify_by_group": new_in_group},
                password=new_pwd.strip(),
            )
            reload_users()
            st.session_state["_flash"] = {"type": "success", "text": f"✅ 已新增用户：{new_name.strip()}（{uid}）"}
            st.rerun()


def _render_user_edit_section(users):
    """✏️ 修改 / 删除用户（删除与修改共用同一个「选择用户」，必须同标签）。"""
    if not users:
        st.info("暂无用户")
        return
    edit_id = st.selectbox(
        "选择用户",
        options=[u["id"] for u in users],
        format_func=lambda x: f"{USER_ID_TO_NAME.get(x, x)}（{x}）",
    )
    eu = next((u for u in users if u["id"] == edit_id), None)
    if not eu:
        return
    with st.form("edit_user_form"):
        e1, e2 = st.columns(2)
        with e1:
            e_name = st.text_input("姓名", value=eu["name"])
            e_role = st.selectbox(
                "角色",
                options=USER_ROLES,
                index=USER_ROLES.index(eu["role"]) if eu["role"] in USER_ROLES else 0,
            )
            e_phone = st.text_input("手机号（钉钉@提醒用，可空）", value=eu.get("phone") or "")
            e_email = st.text_input(
                "邮箱（邮件提醒用，留空按「姓名拼音@example.com」生成）",
                value=eu.get("email") or "",
            )
        with e2:
            e_dept = st.text_input("部门", value=eu.get("department", ""))
            e_pwd = st.text_input("重置密码（留空则不修改）", type="password", value="")
            e_in_group = st.checkbox("在钉钉提醒群中（不勾选则不群@；邮件提醒与是否勾选无关）",
                                     value=eu.get("notify_by_group", True))
        save_submit = st.form_submit_button("💾 保存修改", use_container_width=True)
    if save_submit:
        if not e_name.strip():
            st.error("姓名不能为空")
        elif any(u["name"] == e_name.strip() and u["id"] != edit_id for u in users):
            st.error(f"姓名「{e_name.strip()}」已被其他用户使用")
        else:
            db.save_user(
                {"id": edit_id, "name": e_name.strip(), "role": e_role, "department": e_dept.strip(),
                 "phone": e_phone.strip(),
                 "email": e_email.strip() or _default_user_email(e_name.strip()),
                 "notify_by_group": e_in_group},
                password=e_pwd.strip() or None,
            )
            reload_users()
            st.session_state["_flash"] = {"type": "success", "text": f"✅ 已更新用户：{e_name.strip()}"}
            st.rerun()

    # 删除（与修改共用上面选中的用户，故留在同一标签内）
    st.markdown("---")
    st.markdown("### 🗑 删除用户")
    is_self = edit_id == st.session_state.get("current_user_id")
    mgmt_count = sum(1 for u in users if u["role"] == "管理层")
    is_last_mgmt = eu["role"] == "管理层" and mgmt_count <= 1
    if is_self:
        st.warning("不能删除当前登录的账号")
    elif is_last_mgmt:
        st.warning("不能删除最后一位管理层账号")
    else:
        st.warning(f"确定删除用户「{eu['name']}」？此操作不可恢复。")
        confirm = st.checkbox("我确认删除", key=f"del_user_confirm_{edit_id}")
        if st.button("🗑 确认删除", disabled=not confirm, key=f"del_user_btn_{edit_id}"):
            db.delete_user(edit_id)
            reload_users()
            st.session_state["_flash"] = {"type": "success", "text": f"✅ 已删除用户：{eu['name']}"}
            st.rerun()


def page_user_management():
    st.markdown("## 👥 用户管理")
    cur = get_current_user()
    if cur and cur["role"] != "管理层":
        _render_change_password(cur)
        return

    # 横向标签菜单（与项目视图/系统评估同一套 _nav_tabs）。
    # 原来四个区块竖着堆一长页；现拆为互斥标签，每次 rerun 只执行当前区块。
    # 「修改」与「删除」共用同一个「选择用户」下拉，必须放在同一标签。
    users = get_users()
    _tab = _nav_tabs(
        [
            ("list", "👥 现有用户"),
            ("add", "➕ 新增用户"),
            ("edit", "✏️ 修改 / 删除"),
            ("sync", "🔄 钉钉同步"),
        ],
        state_key="user_mgmt_tab",
    )
    if _tab == "list":
        _render_user_list_section(users)
    elif _tab == "add":
        _render_user_add_section(users)
    elif _tab == "edit":
        _render_user_edit_section(users)
    else:
        _render_user_sync_section(users)


# ============================================================
# 主入口
# ============================================================

def _inject_login_css():
    """登录页专属样式（独立于 _inject_global_css，互不影响）。

    设计取向：**沿用系统自身的设计语言**，不照搬任何参考图——
    主色 #1E2A3A 与 APP 内深色侧边栏同源，卡片白底/圆角/浅阴影与 APP 内白卡片同源，
    强调色 #409EFF 与导航高亮/头像同源。于是「登录页 → 进系统」是同一套视觉，不是两张皮。
    配色刻意保持克制（深底 + 一张白卡 + 一个强调色），避免登录页比业务页还花哨。

    选择器说明：登录页专属规则的起点是 [data-testid="stMainBlockContainer"]，
    而业务页的卡片挂钩是 [class*="st-key-kb_card_"]，两者不重叠——
    故这里的 !important 不会影响已打磨好的业务页版式。
    """
    st.markdown("""
    <style>
    /* ===== 登录页居中（三件套，缺一不可）=====
       ① 主区铺满视口并成为 flex 容器 → 让 block 容器有确定的可用高度可撑；
       ② block 容器撑满该高度 + 纵向 flex 居中 → 卡片垂直居中；
       ③ 卡片自身 max-width + margin:0 auto → 卡片水平居中。
       为什么必须三件套：block 容器是 align-items:center 的纵向 flex，它按内容宽度
       收缩；只给它 justify-content:center 时，一旦上级没有确定高度，它会退化成
       内容高度，纵向居中就失效（卡片贴在顶部）。
       ② 的 align-items 用 stretch 而非 center：center 会让卡片按内容宽度收缩，
       而卡片内首个块（品牌区）也是收缩块，结果整块左偏——这是「没居中」的根因之一。
       视觉位置：用户要求「往下一点」，已按反馈连续下调三次（当前约低于几何正中 76px）。
       ⚠️ 方向容易搞反：justify-content:center 是在**扣掉内边距之后的内容盒**里居中，
       所以「上内边距更大 → 内容更靠下」，「下内边距更大 → 内容反而更靠上」。
       偏移量 = (上内边距 − 下内边距) / 2。当前 10.5rem vs 1rem → 下移 76px
       （1rem = 16px，故每加 2rem 上边距 = 下移 16px；需要精确微调时按此换算）。
       想再微调：只改这两个 padding 值（调大上边距更靠下）。 */
    [data-testid="stMain"] { background: #1E2A3A; }
    [data-testid="stMainBlockContainer"], [data-testid="stMain"] > div {
        min-height: 100dvh !important;
        display: flex !important; flex-direction: column !important;
        justify-content: center !important; align-items: stretch !important;
        max-width: none !important;
        padding: 10.5rem 1rem 1rem !important;
    }
    /* 登录态不显示侧边栏（用 :has 精确限定：仅在登录卡片存在时渲染才隐藏，
       避免影响登录后页面的侧边栏宽度/贴底规则）。
       两条都写：一条挡 <section> 自身，一条挡它的包裹层，DOM 结构微调也不会漏。 */
    [data-testid="stAppViewContainer"]:has([class*="st-key-kb_login_card"]) [data-testid="stSidebar"],
    [data-testid="stAppViewContainer"]:has([class*="st-key-kb_login_card"]) [data-testid="stSidebar"] > div {
        display: none !important;
    }
    /* 登录卡片：水平居中的落点就在这两行（max-width + margin auto） */
    [class*="st-key-kb_login_card"] {
        width: 100% !important; max-width: 400px !important;
        margin: 0 auto !important;
        box-sizing: border-box;
        background: #FFFFFF; border-radius: 14px;
        box-shadow: 0 12px 36px rgba(0,0,0,.30);
        padding: 30px 30px 26px !important;
    }
    /* 品牌区：站点徽标 + 主标题 + 副标题。
       必须显式给宽度：它是 flex 容器（block 容器）的子项，若不给宽度会按内容收缩，
       导致 margin:auto 失效、徽标/标题整体左偏。 */
    .kb-login-brand { width: 100%; text-align: center; margin-bottom: 22px; }
    .kb-login-logo {
        width: 46px; height: 46px; margin: 0 auto 12px;
        border-radius: 12px; background: #409EFF; color: #FFF;
        display: flex; align-items: center; justify-content: center;
        font-size: 23px; box-shadow: 0 4px 12px rgba(64,158,255,.35);
    }
    .kb-login-title {
        font-size: 20px; font-weight: 700; color: #1F2329;
        line-height: 1.3; letter-spacing: .2px;
    }
    .kb-login-sub {
        font-size: 12px; color: #8A94A6; margin-top: 6px; line-height: 1.4;
    }
    /* 表单控件：与 APP 内一致的圆角与聚焦色 */
    [class*="st-key-kb_login_card"] label p {
        font-size: 12.5px; color: #4E5969; font-weight: 500; margin: 0 0 4px !important;
    }
    [class*="st-key-kb_login_card"] input {
        font-size: 13.5px; border-radius: 8px; padding: 9px 12px;
    }
    /* 主按钮用系统主题色（不做硬编码覆盖，避免与全局主题冲突） */
    [class*="st-key-kb_login_card"] button {
        border-radius: 8px; font-size: 14px; font-weight: 600;
        padding: 9px 12px; margin-top: 4px;
    }
    [class*="st-key-kb_login_card"] button p { font-size: 14px; margin: 0 !important; }
    /* 卡片内折叠区（使用说明）：默认收起，文字调小调淡，保持卡片干净 */
    [class*="st-key-kb_login_card"] [data-testid="stExpander"] { margin-top: 14px; }
    [class*="st-key-kb_login_card"] [data-testid="stExpander"] summary {
        font-size: 11.5px !important; color: #8A94A6 !important;
        padding: 6px 0 !important; min-height: 0 !important;
    }
    [class*="st-key-kb_login_card"] [data-testid="stExpander"] summary p {
        font-size: 11.5px !important; margin: 0 !important;
    }
    [class*="st-key-kb_login_card"] [data-testid="stExpander"] [data-testid="stMarkdownContainer"] p {
        font-size: 11.5px; color: #8A94A6; line-height: 1.6; margin: 0;
    }
    /* 登录页不显示顶部工具栏残留 */
    [data-testid="stAppViewContainer"]:has([class*="st-key-kb_login_card"]) header[data-testid="stHeader"] {
        display: none !important;
    }
    </style>
    """, unsafe_allow_html=True)


def render_login_page():
    """登录页：按姓名 + 密码校验，成功后写入 current_user_id 与 logged_in。

    版式为系统自有风格（深色底 + 居中白卡），样式见 _inject_login_css。
    """
    with st.container(key="kb_login_card"):
        st.markdown(
            '<div class="kb-login-brand">'
            '<div class="kb-login-logo">🛠️</div>'
            f'<div class="kb-login-title">{APP_TITLE}</div>'
            f'<div class="kb-login-sub">{APP_SUBTITLE}</div>'
            '</div>',
            unsafe_allow_html=True,
        )

        with st.form("login_form"):
            name = st.text_input("姓名", placeholder="请输入您的姓名")
            password = st.text_input("密码", type="password", placeholder="请输入密码")
            submit = st.form_submit_button("登 录", type="primary", use_container_width=True)

        # 使用说明收在卡片内的折叠区，默认收起，保持卡片干净。
        # 这里的样式由 [data-testid="stExpander"] 命中（该挂钩已在包内自证存在），
        # 不依赖 st-key- 类——避免出现「写了规则但没给 key」的死选择器。
        with st.expander("ℹ️ 使用说明", expanded=False):
            st.markdown(
                "- 本系统为内网部署，登录后请勿在公共设备上保存密码。\n"
                "- 忘记密码请联系管理员在「用户管理」中重置。"
            )

    if submit:
        if not name.strip():
            st.error("请输入姓名")
        else:
            user = db.authenticate(name.strip(), password)
            if user:
                st.session_state.current_user_id = user["id"]
                st.session_state.logged_in = True
                # 刷新不掉线：签发令牌并安排下一轮写 Cookie——不能在本轮直接写：
                # 马上要 st.rerun()，iframe 组件会在 JS 执行前被卸载，Cookie 写不上
                st.session_state["_set_auth_cookie"] = auth.issue_token(user["id"])
                # 用户活跃埋点（D4，t20）：登录成功记一条 action='login'——mvp 是单页应用，
                # 用户停在一页不动时只看 page_view 会低估活跃度，登录事件兜底；
                # 失败只打日志，绝不阻塞登录（吸收客诉「埋点失败不抛异常」纪律）
                try:
                    db.log_user_activity(user["id"], user.get("name", ""),
                                         user.get("role", ""), "", "login")
                except Exception as e:
                    print(f"[activity] 登录埋点写入失败（不影响登录）：{e}")
                st.session_state["_flash"] = {"type": "success", "text": f"✅ 欢迎回来，{user['name']}"}
                st.rerun()
            else:
                st.error("姓名或密码错误，请重试。")


def main():
    st.set_page_config(
        page_title=APP_TITLE,
        page_icon="🛠️",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    # 隐藏 Streamlit 自带的顶部工具栏（Deploy 按钮 + ⋮ 菜单 + 运行状态指示）。
    # 该栏 position:fixed 悬浮在页顶，会遮挡业务内容；内部工具用不到 Deploy 入口。
    st.markdown(
        """
        <style>
            header[data-testid='stHeader'] { display: none; }
            div[data-testid='stToolbar'] { display: none; }
            div[data-testid='stStatusWidget'] { display: none; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    init_state()

    # 未登录：先尝试从 Cookie 还原登录态（浏览器刷新不掉线），还原不了再进登录页
    if not st.session_state.get("logged_in"):
        _try_restore_login_from_cookie()

    # 未登录：先显示登录页（登录页有专属样式，独立于三段式外壳的 _inject_global_css）
    if not st.session_state.get("logged_in"):
        _inject_login_css()
        # 退出登录的 Cookie 清除动作放在登录页这一轮完成（登出后马上 rerun，
        # 组件 iframe 会在 JS 执行前被卸载，写不进去——与登录侧同一节奏错开一轮）
        if st.session_state.pop("_clear_auth_cookie", False):
            _emit_auth_cookie_js(None)
        render_login_page()
        return

    # 登录成功后的 Cookie 写入动作：安排在 rerun 之后的这一轮执行（见 render_login_page 注释）
    _tok_to_set = st.session_state.pop("_set_auth_cookie", None)
    if _tok_to_set:
        _emit_auth_cookie_js(_tok_to_set)

    # 感知其它会话的写入（否则 A 提的需求 B 看不到）
    _sync_from_db()

    # ---- 页面字典与角色过滤（t31 起提前到侧边栏之前：导航需要它）----
    all_pages = {
        "📊 中心总览": page_overview,
        "📋 我的看板": page_my_kanban,
        "📂 项目视图": page_project_view,
        "📦 需求管理": page_management,
        "➕ 新建需求": page_new_requirement,
        "📄 需求详情": page_requirement_detail,
        "🔍 AI 搜索": page_search,
        "📅 周报": page_weekly,
        "📋 系统评估": page_system_eval,
        "👥 用户管理": page_user_management,
    }
    # 系统评估页：管理层 + 主管可见（主管用于推动组员填问卷、看趋势）。
    # 但页内的「逐人评价明细」仍只对管理层开放，见 _can_view_rating_detail。
    _cur = get_current_user()
    pages = dict(all_pages)
    if not _cur or _cur.get("role") not in ("管理层", "主管"):
        pages.pop("📋 系统评估", None)

    # ---- t31 导航迁移：解析当前页（含 _pending_nav 跳转消费）----
    selection = _resolve_nav(pages)

    # 用户活跃埋点（D4，t20；t31 随导航迁移，语义不变）：页面切换记一条 action='page_view'
    # （page_path=导航项中文名，与页面显示名一致、可读——与旧 radio 选中值同一取值空间）。
    # 用 session_state 记住「上次已记录过的页面」，同一页面值不重复写——
    # Streamlit 每次交互都会重跑脚本，不去重会把一次访问放大成几十条；
    # 失败只打日志，绝不阻塞页面渲染。
    try:
        if selection != st.session_state.get("_activity_last_page"):
            _me_act = get_current_user()
            if _me_act:
                db.log_user_activity(_me_act["id"], _me_act.get("name", ""),
                                     _me_act.get("role", ""), selection, "page_view")
            st.session_state["_activity_last_page"] = selection
    except Exception as e:
        print(f"[activity] 页面埋点写入失败（不影响渲染）：{e}")

    # 一次性汇总当前用户的全部待办提醒（避免对全量需求扫多遍）。
    # t31 起提前到侧边栏/Header 之前：待办总数供「我的看板」角标与 Header 铃铛使用；
    # 7 元组解包一行原样保留（仅调用位置上移）。
    pending, feedback, rejects, pool_owner, pool_requester, assignments, done_notices = _pending_notices_all(
        st.session_state.current_user_id
    )
    _todo_total = sum(len(x) for x in
                      (pending, feedback, rejects, pool_owner, pool_requester, assignments, done_notices))

    # ---- 三段式布局（t31）：全局样式 → 深色侧边栏（导航）→ 问卷弹窗 → 顶部 Header ----
    _inject_global_css()
    render_sidebar(pages, selection, _todo_total)

    # 系统使用体验问卷：按冷却期自动弹出（弹窗内部自行判断是否需要弹）
    _maybe_show_survey()

    # 跨页面 flash 消息（避免 st.rerun 刷掉 success/error）
    flash = st.session_state.pop("_flash", None)
    if flash:
        if flash.get("type") == "success":
            st.success(flash["text"])
        elif flash.get("type") == "error":
            st.error(flash["text"])
        elif flash.get("type") == "warning":
            st.warning(flash["text"])
        elif flash.get("type") == "info":
            st.info(flash["text"])

    # 顶部 Header（t31，取代原 st.title/st.caption 全局标题）：页标题 + 搜索 + 铃铛 + 用户胶囊
    _render_app_header(selection, _todo_total)

    # 建议责任人：待确认提醒
    if pending:
        n = len(pending)
        titles = "、".join(f"「{r['title']}」" for r in pending[:3])
        more = " 等" if n > 3 else ""
        st.warning(f"📬 您有 {n} 条需求待确认（您为指定责任人）：{titles}{more}。请及时处理。")
        if st.button("📋 前往「我的看板」确认", type="primary"):
            st.session_state["_pending_nav"] = True
            st.session_state["_pending_nav_target"] = "📋 我的看板"
            st.rerun()

    # 提出人：主管反馈/调整待确认提醒
    if feedback:
        n = len(feedback)
        titles = "、".join(f"「{r['title']}」" for r in feedback[:3])
        more = " 等" if n > 3 else ""
        st.warning(f"💬 主管对您提出的 {n} 条需求给出了反馈/调整建议：{titles}{more}。请确认是否采纳。")
        if st.button("📋 前往「我的看板」查看反馈", type="primary"):
            st.session_state["_pending_nav"] = True
            st.session_state["_pending_nav_target"] = "📋 我的看板"
            st.rerun()

    # 提出人：需求被负责人驳回
    if rejects:
        n = len(rejects)
        titles = "、".join(f"「{r['title']}」" for r in rejects[:3])
        more = " 等" if n > 3 else ""
        st.error(f"❌ 您提出的 {n} 条需求已被负责人驳回并终止：{titles}{more}。")
        if st.button("✅ 知道了", key="reject_ack_all"):
            me = st.session_state.current_user_id
            for r in rejects:
                r.setdefault("reject_seen_by", []).append(me)
                save_requirement(r)
            st.rerun()

    # 已完成提醒：同步给提出人和建议负责人
    if done_notices:
        n = len(done_notices)
        titles = "、".join(f"「{r['title']}」" for r in done_notices[:3])
        more = " 等" if n > 3 else ""
        st.success(f"🎉 您参与的需求已完成：{titles}{more}。")
        if st.button("✅ 知道了", key="done_ack_all"):
            me = st.session_state.current_user_id
            for r in done_notices:
                r.setdefault("completion_seen_by", []).append(me)
                save_requirement(r)
            st.rerun()

    # 提出人：已完成需求待评价（满意度，一期）
    _render_rating_notice(st.session_state.current_user_id)
    if st.session_state.get("_rating_req"):
        _rating_dialog(st.session_state["_rating_req"])

    # 改进闭环回告（三期，03 蓝图 §5.5）：站内提醒条与个人进展视图。
    # 渲染顺序：需求流转待办 → 已完成待评价 → 改进项进展回告 → 页面内容。
    # 站内可见性由「终态 + 未确认」决定，不依赖外发通道是否配置成功（队长 t11 硬要求）。
    _render_improvement_notice(st.session_state.current_user_id)
    _render_my_improvements(st.session_state.current_user_id)
    # 🌟 改进动态全员公示（F-05 裁决 A）：所有登录用户可见的已上线成果（只读，仅摘要+时间）
    _render_improvement_feed_public()

    # 负责人：需求进入需求池，提醒前往处理
    if pool_owner:
        n = len(pool_owner)
        titles = "、".join(f"「{r['title']}」" for r in pool_owner[:3])
        more = " 等" if n > 3 else ""
        st.info(f"📋 您有 {n} 条需求已进入需求池，请前往指派执行人处理：{titles}{more}。")
        if st.button("📋 前往「需求管理」处理", key="pool_owner_go"):
            me = st.session_state.current_user_id
            for r in pool_owner:
                r.setdefault("pool_seen_by", []).append(me)
                save_requirement(r)
            st.session_state["_pending_nav"] = True
            st.session_state["_pending_nav_target"] = "📦 需求管理"
            st.rerun()

    # 提出人：需求已被采纳
    if pool_requester:
        n = len(pool_requester)
        titles = "、".join(f"「{r['title']}」" for r in pool_requester[:3])
        more = " 等" if n > 3 else ""
        st.success(f"✅ 您提出的 {n} 条需求已被采纳，已进入需求池：{titles}{more}。")
        if st.button("✅ 知道了", key="pool_requester_ack"):
            me = st.session_state.current_user_id
            for r in pool_requester:
                r.setdefault("pool_seen_by", []).append(me)
                save_requirement(r)
            st.rerun()

    # 执行人：新分配的需求任务
    if assignments:
        n = len(assignments)
        titles = "、".join(f"「{r['title']}」" for r in assignments[:3])
        more = " 等" if n > 3 else ""
        st.info(f"📌 您有 {n} 条新分配的需求任务：{titles}{more}。请及时跟进。")
        if st.button("📋 前往「我的看板」查看", key="assign_go", type="primary"):
            me = st.session_state.current_user_id
            for r in assignments:
                r.setdefault("assign_seen_by", []).append(me)
                save_requirement(r)
            st.session_state["_pending_nav"] = True
            st.session_state["_pending_nav_target"] = "📋 我的看板"
            st.rerun()

    pages[selection]()


if __name__ == "__main__":
    main()
