"""
services/requirements.py — 从 app.py 拆分生成（函数体逐字保留）。
"""
from __future__ import annotations

# ---- 第三方 / 标准库 ----
from datetime import datetime
import db
import re
import streamlit as st

# ---- 项目内部 ----
from config import TODAY, TODAY_STR
from services.cache import _invalidate_attachment_cache, _invalidate_data_caches, _mark_own_write
from services.projects import get_projects
from state import USER_ID_TO_NAME



_SOURCE_DEPT_MIGRATION = {
    "战略需求": "总经办",
    "订单需求": "销售",
    "打样需求": "售前",
    "售后需求": "客服",
    "技术攻关": "研发",
    "设计变更": "工程",
}


def get_requirements() -> list[dict]:
    return st.session_state.requirements


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


def gen_req_id() -> str:
    yyyymm = TODAY.strftime("%Y%m")
    seq = db.next_seq(yyyymm)
    return f"REQ-{yyyymm}-{seq:04d}"


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
