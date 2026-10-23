"""
config.py — 从 app.py 拆分生成（函数体逐字保留）。
"""
from __future__ import annotations

# ---- 第三方 / 标准库 ----
from datetime import datetime
from datetime import timedelta



APP_TITLE = "需求看板 AI 助手"


APP_SUBTITLE = "需求全流程管理 · AI 辅助"


TODAY = datetime.now().date()


TODAY_STR = TODAY.strftime("%Y-%m-%d")


MONTH_START = TODAY.replace(day=1)                    # 本月 1 号


WEEK_START = TODAY - timedelta(days=TODAY.weekday())  # 本周一


MONTH_START_STR = MONTH_START.strftime("%Y-%m-%d")


WEEK_START_STR = WEEK_START.strftime("%Y-%m-%d")


PROJECT_STAGES = [
    "技术预研/需求确认",
    "研发设计",
    "物料下单",
    "生产装配",
    "厂内调试",
    "厂外调试",
    "售后处理",
]


PROJECT_GROUPS = ["EQ-200", "EQ-400", "EQ-700", "研发AI赋能", "其他"]


SATISFACTION_LABELS = {5: "非常满意", 4: "满意", 3: "一般", 2: "不满意", 1: "很不满意"}


SATISFACTION_COLORS = {5: "#43A047", 4: "#7CB342", 3: "#FB8C00", 2: "#F4511E", 1: "#E53935"}


SURVEY_COOLDOWN_DAYS = 3  # 同一用户两次被触达问卷的最短间隔


SURVEY_QUESTIONS = [
    "我在这个系统里能顺畅地完成我的工作",
    "我需要查找的信息（需求、进度、附件）很容易找到",
    "AI 助手对我的实际工作有帮助",
    "我愿意继续使用这个系统",
]


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


SURVEY_MIN_SAMPLE = 5


KUI_BANDS = [
    ("A · 良好可用", 4.0, None, "#43A047"),   # KUI ≥ 4.0
    ("B · 基本可用", 3.0, 4.0, "#1E88E5"),    # 3.0 ≤ KUI < 4.0
    ("C · 体验不佳", 2.0, 3.0, "#FB8C00"),    # 2.0 ≤ KUI < 3.0
    ("D · 需重点改进", None, 2.0, "#E53935"),  # KUI < 2.0
]


KUI_NOTE = "KUI 为自研 4 题短问卷指数（1.0-5.0），不可与标准 SUS（0-100）对比"


SURVEY_QUESTION_SET = "v1"


IMPROVEMENT_CATEGORIES = ["功能", "体验", "AI", "数据", "其他"]


NAV_GROUPS = [
    ("", ["📊 中心总览", "📋 我的看板"]),
    ("需求", ["📦 需求管理", "📂 项目视图", "➕ 新建需求", "📄 需求详情", "🔍 AI 搜索", "📅 周报"]),
    ("管理与评估", ["📋 系统评估", "👥 用户管理"]),
]


NAV_STATE_KEY = "_nav_page"


VALID_SOURCES = ["总经办", "售前", "销售", "客服", "研发", "工程", "生产", "研究院", "其他"]


VALID_STATUSES = ["待确认", "需求池", "待启动", "进行中", "暂停", "已完成", "已终止"]


VALID_PRIORITIES = ["P0", "P1", "P2", "P3"]


VALID_RATINGS = ["高", "中", "低", "未评", ""]
