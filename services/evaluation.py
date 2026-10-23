"""
services/evaluation.py — 从 app.py 拆分生成（函数体逐字保留）。
"""
from __future__ import annotations

# ---- 第三方 / 标准库 ----
from collections import Counter
from datetime import datetime
from datetime import timedelta
import db
import streamlit as st

# ---- 项目内部 ----
from config import KUI_BANDS, SURVEY_COOLDOWN_DAYS, SURVEY_MIN_SAMPLE, SURVEY_QUESTIONS
from services.notify import _notify
from services.requirements import get_requirements
from state import USER_ID_TO_NAME, get_current_user



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
