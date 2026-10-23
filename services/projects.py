"""
services/projects.py — 从 app.py 拆分生成（函数体逐字保留）。
"""
from __future__ import annotations

# ---- 第三方 / 标准库 ----
import dingtalk
import streamlit as st

# ---- 项目内部 ----
from config import PROJECT_GROUPS



_TITLE_TAG_TO_GROUP = [("A", "EQ-200"), ("B", "EQ-400"), ("C", "EQ-700"), ("AI", "研发AI赋能")]


def get_projects() -> list[dict]:
    return st.session_state.get("projects", [])


def _project_display_name(p: dict) -> str:
    """项目显示名：有「核心亮点/定制需求」时显示「名称（亮点）」，否则只显示名称"""
    hl = (p.get("highlight") or "").strip()
    return f"{p['name']}（{hl}）" if hl else p["name"]


def _project_name_by_id() -> dict:
    """project_id -> 显示名。筛选/行渲染会逐条需求调用，预先建表避免 O(n×m)。"""
    return {p["id"]: _project_display_name(p) for p in get_projects()}


def _req_project_name(r: dict) -> str:
    """需求关联项目的显示名（含亮点后缀），未关联返回空串"""
    pid = r.get("project_id", "")
    if not pid:
        return ""
    return _project_name_by_id().get(pid, "")


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
