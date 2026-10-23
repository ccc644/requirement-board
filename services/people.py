"""
services/people.py — 从 app.py 拆分生成（函数体逐字保留）。
"""
from __future__ import annotations

# ---- 第三方 / 标准库 ----
from collections import Counter
import os
import streamlit as st

# ---- 项目内部 ----
from services.requirements import get_requirements
from state import USER_ID_TO_NAME



def _owner_names(r: dict, default: str = "未指派") -> str:
    """执行人显示名（多人用顿号连接），空则返回 default"""
    ids = [i for i in r.get("owner_ids", []) if i]
    if not ids:
        return default
    return "、".join(USER_ID_TO_NAME.get(i, "未知") for i in ids)


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


def _user_task_count_map() -> dict:
    """一次遍历聚合所有用户的在跑任务数，避免每个工程师都全量扫一遍。"""
    counts = Counter()
    for r in _active_reqs():
        for uid in r.get("owner_ids", []):
            counts[uid] += 1
    return dict(counts)


def _user_load_map() -> dict:
    """一次遍历聚合所有用户的在跑总工作量。"""
    totals: dict = {}
    for r in _active_reqs():
        effort = float(r.get("effort_estimate_days") or 0)
        for uid in r.get("owner_ids", []):
            totals[uid] = totals.get(uid, 0.0) + effort
    return totals


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
