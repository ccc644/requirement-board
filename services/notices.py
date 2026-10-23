"""
services/notices.py — 从 app.py 拆分生成（函数体逐字保留）。
"""
from __future__ import annotations


# ---- 项目内部 ----
from services.requirements import get_requirements



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
