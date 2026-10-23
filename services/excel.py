"""
services/excel.py — 从 app.py 拆分生成（函数体逐字保留）。
"""
from __future__ import annotations

# ---- 第三方 / 标准库 ----
from datetime import datetime
import streamlit as st

# ---- 项目内部 ----
from config import TODAY_STR, VALID_PRIORITIES, VALID_RATINGS, VALID_SOURCES, VALID_STATUSES
from services.projects import get_projects
from services.requirements import _sync_sub_requirements
from state import USER_ID_TO_NAME, USER_NAME_TO_ID



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
