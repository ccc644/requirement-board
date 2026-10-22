"""
评估体系与改进闭环 · 无头自检脚本（评价体系三期）

用途：不启动 Streamlit、不点页面，直接驱动**真实的**评估统计函数与改进闭环逻辑，
覆盖统计口径、边界（无数据 / 全 dismissed / 部分评价 / 历史单值格式）、
问卷→改进项→状态机→回告→确认的全链路，以退出码表达成败（0=全部通过，1=存在失败）。

隔离方式（绝不触碰真实业务数据）：
- 运行账号（.env 的 DB_USER）在服务器上无 CREATEDB 权限，无法另建临时数据库；
  因此在**同一实例**内建一次性临时 SCHEMA，并把 db.py 的全部连接（连接池 + 直连兜底）
  通过 options=-c search_path=<临时schema> 锁定到该 schema。
  search_path 中不含 public，未限定 schema 的 SQL 物理上无法解析到业务表；
  脚本还前后快照 public 各业务表行数，结束时逐一核对未被触碰，并 DROP 临时 SCHEMA。
- 自检绝不调用 _notify 外发（若部署环境配置了钉钉/SMTP 会产生真实消息），外发分支
  以「站内可见性不依赖 notified_at」的结构性断言覆盖（见 S9）。

用法：mvp/.venv/Scripts/python.exe mvp/tools/eval_sim.py
"""
from __future__ import annotations

import os
import re
import sys
import time
import traceback
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
MVP = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, MVP)

# Windows 控制台默认 GBK，✓/✗ 与中文会触发 UnicodeEncodeError，这里统一切到 UTF-8 输出
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, cond, evidence: str = "") -> None:
    """记录一条断言结果并即时打印。"""
    ok = bool(cond)
    _RESULTS.append((ok, name, evidence))
    mark = "✓" if ok else "✗"
    print(f"  [{mark}] {name}" + (f" —— {evidence}" if evidence else ""))


def section(title: str) -> None:
    print(f"\n== {title} ==")


def main() -> int:
    import psycopg2
    from dotenv import load_dotenv

    load_dotenv(os.path.join(MVP, ".env"))
    host = os.environ.get("DB_HOST") or "localhost"
    port = os.environ.get("DB_PORT") or "5432"
    user = os.environ.get("DB_USER") or "postgres"
    pwd = os.environ.get("DB_PASSWORD") or ""
    dbname = os.environ.get("DB_NAME") or "kanban_demo"

    schema = f"eval_sim_{os.getpid()}_{int(time.time())}"
    assert re.fullmatch(r"[a-z_][a-z0-9_]*", schema), f"临时 schema 名不合法：{schema}"

    # ---- 0. 维护连接（不受 search_path 影响）：快照 public 业务表行数 ----
    admin = psycopg2.connect(host=host, port=port, dbname=dbname, user=user, password=pwd,
                             connect_timeout=10)
    admin.autocommit = True
    acur = admin.cursor()

    def public_counts() -> dict:
        """快照 public 业务表行数；表尚不存在（如未跑过新 DDL 的库）记为 absent。"""
        out = {}
        for t in ("requirements", "projects", "users", "sus_survey", "eval_improvement",
                  "user_activity_log", "meta"):
            acur.execute("SELECT to_regclass(%s)", (f"public.{t}",))
            if acur.fetchone()[0] is None:
                out[t] = "absent"
            else:
                acur.execute(f'SELECT count(*) FROM public."{t}"')
                out[t] = acur.fetchone()[0]
        return out

    before = public_counts()
    print(f"临时 schema：{schema}（public 业务表行数快照：{before}）")
    acur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    acur.execute(f"CREATE SCHEMA {schema}")

    try:
        # ---- 1. 把 db.py 的全部连接锁定到临时 schema（在第一次连接之前完成）----
        import db  # noqa: E402  导入本身不建连（连接池懒加载）
        db._CONN_KWARGS["options"] = f"-c search_path={schema}"
        assert db._POOL is None, "db 连接池已被占用，无法安全切换 search_path"
        section("S0 隔离与幂等建表")
        cur_schema, db_now = db._query_one("SELECT current_schema(), current_database()")
        schemas = db._query_one("SELECT current_schemas(false)")[0]
        check("db 连接已锁定临时 schema", cur_schema == schema,
              f"current_schema={cur_schema}, db={db_now}, schemas={schemas}")
        check("search_path 不含 public（未限定 SQL 无法触碰业务表）", "public" not in schemas)
        db.init_db([], None, None)
        db._init_db_once([], None, None)   # 进程内二次执行 DDL（幂等性真实验证）
        db._init_db_once([], None, None)   # 再来一次：全部 IF NOT EXISTS，不得报错
        n_tables = db._query_one(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = %s "
            "AND table_name IN ('requirements','projects','users','sus_survey','eval_improvement',"
            "'user_activity_log','meta')",
            (schema,))[0]
        check("init_db 重复执行不报错且 7 张表齐备（幂等建表）", n_tables == 7, f"{n_tables}/7 张表")

        # sus_survey 既有 12 列零改动 + 仅新增 question_set（兼容约束 C6）
        cols = [r[0] for r in db._query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = 'sus_survey' ORDER BY ordinal_position",
            (schema,))]
        expect_legacy = ["id", "user_id", "username", "role", "department", "response_type",
                         "score", "answers", "features", "comment", "created_at", "submitted_at"]
        check("sus_survey 既有 12 列保持原序原样", cols[:12] == expect_legacy, f"{cols[:12]}")
        check("sus_survey 仅新增 question_set 列", cols[12:] == ["question_set"], f"{cols[12:]}")
        imp_cols = [r[0] for r in db._query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = 'eval_improvement' ORDER BY ordinal_position",
            (schema,))]
        check("eval_improvement 16 列齐备",
              imp_cols == ["id", "source_type", "source_id", "proposer_id", "proposer_name",
                           "summary", "detail", "category", "status", "owner_id", "resolution_note",
                           "created_at", "updated_at", "closed_at", "notified_at",
                           "proposer_ack_at"], f"{len(imp_cols)} 列")

        # 测试用户走真实 save_user 路径（落临时 schema 的 users 表，供 load_improvements 联查）
        for i, (uid, name, role, dept) in enumerate([
                ("U001", "甲管理", "管理层", "研发中心"), ("U002", "乙主管", "主管", "A组"),
                ("U003", "丙工程", "工程师", "B组")]):
            db.save_user({"id": uid, "name": name, "role": role, "department": dept}, password="x")

        # ---- 2. 评估统计函数（真实实现）----
        import app  # 无头导入：仅用其纯统计/口径函数，不渲染任何页面

        section("S1 空库边界（无数据）")
        q = app._survey_stats()
        check("问卷：触达=0 提交=0 均分=0", (q["reached"], q["submitted"], q["avg"]) == (0, 0, 0.0))
        check("问卷：空库小样本预警生效", q["low_sample"] is True and q["min_sample"] == 5)
        check("问卷：KUI 分档全 0",
              [c for _n, c, _col in q["kui_dist"]] == [0, 0, 0, 0], str(q["kui_dist"]))
        check("问卷：逐题统计为空", q["per_question"] == {})
        s = app.satisfaction_stats(reqs=[])
        check("评价：done=0 rated=0 expected=0 avg=0",
              (s["done"], s["rated"], s["expected"], s["avg"]) == (0, 0, 0, 0.0))
        h = app.improvement_health([])
        check("闭环：total=0 backlog=0 terminal=0 notify_rate=0",
              (h["total"], h["backlog"], h["terminal"], h["notify_rate"]) == (0, 0, 0, 0.0))
        check("闭环：load_improvements 为空", db.load_improvements() == [])

        section("S2 全 dismissed（触达口径与冷却期，C1 机制未破坏）")
        rec_ids = {}
        for uid, name, role, dept in [("U001", "甲管理", "管理层", "研发中心"),
                                      ("U002", "乙主管", "主管", "A组"),
                                      ("U003", "丙工程", "工程师", "B组")]:
            rec_ids[uid] = db.new_survey_dismissed({"id": uid, "name": name,
                                                    "role": role, "department": dept})
        q = app._survey_stats()
        check("触达=3 提交=0（打开即写 dismissed）", (q["reached"], q["submitted"]) == (3, 0))
        check("评分类统计不算 dismissed", q["avg"] == 0.0 and q["comments"] == [])
        check("冷却期内 _survey_due=False（触达时刻起算）",
              app._survey_due("U001") is False and app._survey_due("U003") is False)
        check("从未触达的用户 _survey_due=True", app._survey_due("U999") is True)
        check("last_survey_at 返回最近触达时间（不分 response_type）",
              (db.last_survey_at("U001") or "") != "")

        section("S3 部分提交：逐题统计 / KUI 分档 / question_set")
        qs = app.SURVEY_QUESTIONS
        ans1 = {qs[0]: 5, qs[1]: 4, qs[2]: 4, qs[3]: 5}   # 均分 4.5 → A 档
        ans2 = {qs[0]: 2, qs[1]: 3, qs[2]: 1, qs[3]: 2}   # 均分 2.0 → C 档
        db.submit_survey(rec_ids["U001"], 4.5, ans1, ["AI 搜索", "周报"],
                         "希望AI搜索能搜到附件内容", question_set=app.SURVEY_QUESTION_SET)
        db.submit_survey(rec_ids["U002"], 2.0, ans2, [], "", question_set=app.SURVEY_QUESTION_SET)
        q = app._survey_stats()
        check("触达=3 提交=2 KUI均分=3.25",
              (q["reached"], q["submitted"], round(q["avg"], 4)) == (3, 2, 3.25))
        check("提交率口径=提交/触达（精确口径）", q["submitted"] / q["reached"] == 2 / 3)
        pq = q["per_question"].get("v1", [])
        got = [(t[:10], round(a, 4), c) for t, a, c in pq]
        want = [(qs[0][:10], 3.5, 2), (qs[1][:10], 3.5, 2), (qs[2][:10], 2.5, 2), (qs[3][:10], 3.5, 2)]
        check("逐题均分按题干聚合（Q1=3.5 Q2=3.5 Q3=2.5 Q4=3.5，各 n=2）", got == want, str(got))
        check("KUI 分档：A=1 C=1 其余 0",
              [c for _n, c, _col in q["kui_dist"]] == [1, 0, 1, 0], str(q["kui_dist"]))
        check("小样本预警（2<5）", q["low_sample"] is True)
        check("comment 只出现在 submitted 记录", len(q["comments"]) == 1
              and "附件" in q["comments"][0]["comment"])
        row = db._query_one("SELECT question_set FROM sus_survey WHERE id = %s", (rec_ids["U001"],))
        check("新提交写入 question_set='v1'", row and row[0] == "v1")
        # 旧行 NULL 一律视作 v1（题干冻结纪律，03 蓝图 §4.3）
        import json as _json
        legacy_id = f"SV-U998-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        db._execute(
            "INSERT INTO sus_survey (id, user_id, username, role, department, response_type, score, "
            "answers, features, comment, created_at, submitted_at) "
            "VALUES (%s,'U998','旧用户','工程师','X','submitted',5,%s,'[]','',%s,%s)",
            (legacy_id, _json.dumps({qs[0]: 5, qs[1]: 5}, ensure_ascii=False),
             "2026-01-01 08:00:00", "2026-01-01 08:01:00"))
        q = app._survey_stats()
        pq = dict((t, (round(a, 4), c)) for t, a, c in q["per_question"].get("v1", []))
        check("旧行 NULL 视作 v1 并入逐题统计（Q1: (5+2+5)/3=4.0 n=3；Q2: (4+3+5)/3=4.0 n=3）",
              pq.get(qs[0]) == (4.0, 3) and pq.get(qs[1]) == (4.0, 3)
              and pq.get(qs[2]) == (2.5, 2) and pq.get(qs[3]) == (3.5, 2), str(pq))
        check("跨版本不混算：仅存在 v1 一个版本", list(q["per_question"].keys()) == ["v1"])

        section("S4 需求评价统计与历史单值格式兼容（C2/C3）")
        req_legacy = {  # 早期版本：单值 satisfaction + satisfaction_by
            "id": "REQ-2026-0001", "title": "旧格式需求", "status": "已完成",
            "requester_id": "U001", "requested_owner_id": "U002", "owner_ids": ["U003"],
            "satisfaction": 4, "satisfaction_by": "U001",
            "satisfaction_comment": "旧评语", "satisfaction_at": "2026-01-02 10:00",
        }
        ratings = app._ratings(req_legacy)
        check("_ratings 归一化历史单值为 {U001:{score:4}}",
              set(ratings) == {"U001"} and int(ratings["U001"]["score"]) == 4
              and ratings["U001"]["comment"] == "旧评语")
        req_dict = {  # 新格式：多评价人 dict
            "id": "REQ-2026-0002", "title": "新格式需求", "status": "已完成",
            "requester_id": "U001", "requested_owner_id": "U002", "owner_ids": ["U003"],
            "satisfaction": {
                "U001": {"score": 5, "comment": "", "at": "2026-01-03 09:00"},
                "U002": {"score": 2, "comment": "交付偏慢", "at": "2026-01-03 10:00"},
            },
        }
        s = app.satisfaction_stats(reqs=[req_legacy, req_dict])
        # done=2；应评价=每条 3 人=6；已评价=1+2=3；均分=(4+5+2)/3；满意率(4~5星)=2/3
        check("done=2 rated=3 expected=6 missing=3",
              (s["done"], s["rated"], s["expected"], s["missing"]) == (2, 3, 6, 3))
        check("平均分=(4+5+2)/3≈3.6667", abs(s["avg"] - 11 / 3) < 1e-9, f"{s['avg']:.4f}")
        check("满意率=2/3≈66.7%", abs(s["positive_rate"] - 200 / 3) < 1e-6, f"{s['positive_rate']:.2f}%")
        check("按角色分组：提出人[4,5] 建议责任人[2] 执行人[]",
              sorted(s["by_role"].get("提出人", [])) == [4, 5]
              and s["by_role"].get("建议责任人") == [2] and "执行人" not in s["by_role"])

        section("S5 问卷建议自动建项（复刻 _survey_dialog 提交分支的真实调用序列）")
        # 与 app._survey_dialog 提交分支完全相同的两步：submit_survey → create_improvement
        imp_survey = db.create_improvement(
            source_type="survey", source_id=rec_ids["U001"],
            proposer_id="U001", proposer_name="甲管理",
            detail="希望AI搜索能搜到附件内容")
        imp_survey_dup = db.create_improvement(
            source_type="survey", source_id=rec_ids["U001"],
            proposer_id="U001", proposer_name="甲管理", detail="重复提交不应新增")
        n_survey = db._query_one(
            "SELECT count(*) FROM eval_improvement WHERE source_type='survey'")[0]
        check("问卷建议自动建项成功", bool(imp_survey) and imp_survey.startswith("IMP-"))
        check("同一问卷记录防重：重复建项返回同一 id 且不增行",
              imp_survey_dup == imp_survey and n_survey == 1, f"{imp_survey} / 行数={n_survey}")
        imp_obj = db.get_improvement(imp_survey)
        check("summary 缺省取 detail 前 30 字 / 初始状态=待处理",
              imp_obj["summary"] == "希望AI搜索能搜到附件内容"[:30] and imp_obj["status"] == "待处理")
        check("无建议（comment 空）的问卷不建项（U002）",
              db._query_one("SELECT count(*) FROM eval_improvement WHERE source_id = %s",
                            (rec_ids["U002"],))[0] == 0)

        section("S6 评价转化与手动建项（rating_comment / manual）")
        a1 = db.create_improvement(source_type="rating_comment", source_id="REQ-2026-0002::U002",
                                   proposer_id="U002", proposer_name="乙主管",
                                   detail="需求 REQ-2026-0002 获 2 星评价（建议责任人）：交付偏慢")
        a2 = db.create_improvement(source_type="rating_comment", source_id="REQ-2026-0002::U002",
                                   proposer_id="U002", proposer_name="乙主管", detail="重复转化")
        check("评价一键转改进项 + 唯一索引防重", a1 == a2)
        m1 = db.create_improvement(source_type="manual", source_id=None, proposer_id="U001",
                                   proposer_name="甲管理", summary="周报支持导出 PDF", detail="周报页增加导出")
        m2 = db.create_improvement(source_type="manual", source_id=None, proposer_id="U001",
                                   proposer_name="甲管理", summary="周报支持导出 PDF", detail="同秒第二条")
        check("manual 合法可多条（不参与防重）", m1 != m2)
        check("load_improvements 联查提出人角色/部门",
              (db.get_improvement(imp_survey)["proposer_role"], db.get_improvement(imp_survey)["proposer_dept"])
              == ("管理层", "研发中心"))
        anon = db.load_improvements(with_proposer=False)
        check("匿名取数（with_proposer=False）不返回姓名",
              all(i["proposer_name"] in (None, "") for i in anon))

        section("S7 状态机：白名单流转 / 非法拒绝 / 越权拒绝")
        want_trans = {"待处理": {"处理中", "不采纳", "已搁置"},
                      "处理中": {"已上线", "不采纳", "已搁置"},
                      "已上线": set(), "不采纳": set(), "已搁置": {"待处理"}}
        check("流转白名单与蓝图 §5.3 一致",
              {k: set(v) for k, v in db.IMPROVEMENT_TRANSITIONS.items()} == want_trans)
        ok, msg = db.update_improvement_status(imp_survey, "已上线", "管理层", resolution_note="x")
        check("非法流转被拒：待处理 →已上线（跳步）", not ok and "非法流转" in msg, msg)
        ok, msg = db.update_improvement_status(imp_survey, "处理中", "主管")
        check("越权被拒：主管推进状态", not ok and "仅管理层" in msg, msg)
        ok, msg = db.update_improvement_status(imp_survey, "处理中", "工程师")
        check("越权被拒：工程师推进状态", not ok and "仅管理层" in msg, msg)
        ok, msg = db.update_improvement_status(imp_survey, "已上线", "管理层", "魔法状态兜底")  # 状态值非法
        ok2, msg2 = db.update_improvement_status(imp_survey, "待处理", "管理层")
        check("同状态重复流转被拒", not ok2 and "已是" in msg2, msg2)
        ok, msg = db.update_improvement_status(imp_survey, "处理中", "管理层")
        check("合法：待处理 →处理中", ok, msg)
        ok, msg = db.update_improvement_status(imp_survey, "待处理", "管理层")
        check("非法流转被拒：处理中 →待处理（回退）", not ok and "非法流转" in msg, msg)
        ok, msg = db.update_improvement_status(imp_survey, "已上线", "管理层",
                                               resolution_note="AI 搜索已支持附件内容检索")
        check("合法：处理中 →已上线（附上线说明）", ok, msg)
        obj = db.get_improvement(imp_survey)
        check("进入终态写 closed_at + 记录结论",
              bool(obj["closed_at"]) and "附件" in obj["resolution_note"])
        ok, msg = db.update_improvement_status(imp_survey, "处理中", "管理层")
        check("终态封闭：已上线 →处理中 被拒", not ok and "非法流转" in msg, msg)
        ok, msg = db.update_improvement_status(imp_survey, "不采纳", "管理层")
        check("终态封闭：已上线 →不采纳 被拒", not ok and "非法流转" in msg, msg)
        # 已搁置唯一回流路径
        sh = db.create_improvement(source_type="manual", source_id=None, proposer_id="U002",
                                   proposer_name="乙主管", summary="搁置项", detail="二期再议")
        db.update_improvement_status(sh, "已搁置", "管理层", resolution_note="暂无资源")
        check("合法：待处理 →已搁置", db.get_improvement(sh)["status"] == "已搁置")
        ok, msg = db.update_improvement_status(sh, "待处理", "管理层")
        check("唯一终态回流：已搁置 →待处理", ok, msg)
        check("回流后 closed_at 清空（处理时长口径不被旧值污染）",
              not db.get_improvement(sh)["closed_at"])
        db.update_improvement_status(sh, "不采纳", "管理层", resolution_note="重复建议")
        check("合法：待处理 →不采纳", db.get_improvement(sh)["status"] == "不采纳")

        section("S8 指派与元信息（DAO 层角色二次校验）")
        ok, msg = db.assign_improvement_owner(m2, "U003", "主管")
        check("越权指派被拒", not ok and "仅管理层" in msg, msg)
        ok, msg = db.assign_improvement_owner(m2, "U003", "管理层")
        check("合法指派负责人（非终态项）", ok and db.get_improvement(m2)["owner_id"] == "U003")
        ok, msg = db.assign_improvement_owner(imp_survey, "U001", "管理层")
        check("终态不可再指派", not ok and "终态" in msg, msg)
        ok, msg = db.update_improvement_meta(m1, "主管", summary="x")
        check("越权改摘要被拒", not ok and "仅管理层" in msg, msg)
        ok, msg = db.update_improvement_meta(m1, "管理层", summary="周报支持导出 PDF（修订）",
                                             category="功能")
        obj = db.get_improvement(m1)
        check("合法改摘要/归类", ok and obj["summary"].endswith("（修订）") and obj["category"] == "功能")
        ok, msg = db.update_improvement_meta(m1, "管理层", summary="  ")
        check("空摘要被拒", not ok and "不能为空" in msg, msg)

        section("S9 回告闭环（队长 t11 硬要求：站内可见性独立于外发成败）")
        # imp_survey 已是「已上线」终态且从未外发：站内提醒必须已经可见
        pend = db.pending_improvement_notice("U001")
        check("进入需回告终态即站内可见（尚未外发）",
              any(p["id"] == imp_survey for p in pend), f"{len(pend)} 条待确认")
        ok, msg = db.mark_improvement_notified(imp_survey, "主管")
        check("越权回告被拒", not ok and "仅管理层" in msg, msg)
        ok, msg = db.mark_improvement_notified(imp_survey, "管理层")
        t1 = db.get_improvement(imp_survey)["notified_at"]
        check("管理层回告标记成功（notified_at 落库）", ok and bool(t1))
        ok, msg = db.mark_improvement_notified(imp_survey, "管理层")
        check("回告幂等：重复标记不重发", ok and "幂等" in msg
              and db.get_improvement(imp_survey)["notified_at"] == t1)
        ok, msg = db.ack_improvement(imp_survey, "U002")
        check("非提出人确认被拒", not ok and "提出人本人" in msg, msg)
        ok, msg = db.ack_improvement(imp_survey, "U001")
        check("提出人确认成功（站内提醒条消除开关）", ok)
        ok, msg = db.ack_improvement(imp_survey, "U001")
        check("确认幂等", ok and "幂等" in msg)
        check("确认后站内提醒不再出现",
              all(p["id"] != imp_survey for p in db.pending_improvement_notice("U001")))
        # 已搁置默认不打扰；批量补告（notified_at）后纳入提醒
        sh2 = db.create_improvement(source_type="manual", source_id=None, proposer_id="U003",
                                    proposer_name="丙工程", summary="深色模式", detail="护眼")
        db.update_improvement_status(sh2, "处理中", "管理层")
        db.update_improvement_status(sh2, "已搁置", "管理层", resolution_note="二期排期")
        check("已搁置未补告 → 不打扰（不出现在站内提醒）",
              all(p["id"] != sh2 for p in db.pending_improvement_notice("U003")))
        db.mark_improvement_notified(sh2, "管理层")
        check("已搁置批量补告后 → 纳入站内提醒",
              any(p["id"] == sh2 for p in db.pending_improvement_notice("U003")))
        db.ack_improvement(sh2, "U003")
        check("补告项确认后提醒消除",
              all(p["id"] != sh2 for p in db.pending_improvement_notice("U003")))
        # 不采纳（sh，proposer=U002）未 ack：应持续可见
        check("不采纳终态未确认 → 站内提醒持续可见",
              any(p["id"] == sh for p in db.pending_improvement_notice("U002")))

        section("S10 D3 健康度口径（真实 improvement_health 读临时库数据）")
        # 把时间戳钉死，验证平均处理时长/回告率/已确认率的精确口径
        db._execute("UPDATE eval_improvement SET created_at='2026-01-01 00:00:00', "
                    "closed_at='2026-01-03 12:00:00' WHERE id = %s", (imp_survey,))
        db._execute("UPDATE eval_improvement SET created_at='2026-01-02 00:00:00', "
                    "closed_at='2026-01-03 00:00:00' WHERE id = %s", (sh,))
        imps = db.load_improvements()
        h = app.improvement_health(imps)
        # 终项（已上线/不采纳）= imp_survey(已上线,已回告,已确认) + sh(不采纳,未回告,未确认)
        check("total/by_status 计数正确",
              h["total"] == len(imps) and h["by_status"].get("已上线") == 1
              and h["by_status"].get("不采纳") == 1 and h["by_status"].get("已搁置") == 1)
        check("平均处理时长=mean(2.5天,1.0天)=1.75 天",
              abs(h["avg_days"] - 1.75) < 1e-9, f"{h['avg_days']:.4f}")
        check("回告率=1/2=50%（已搁置不计入终项分母）", abs(h["notify_rate"] - 50.0) < 1e-9)
        check("已确认率=1/2=50%（ack 为站内触达观察口径）", abs(h["ack_rate"] - 50.0) < 1e-9)
        check("待处理积压计数（待处理=a1+m1+m2 共 3 条；>3 才预警，3 条不预警）",
              h["backlog"] == 3 and h["backlog_warn"] is False, f"backlog={h['backlog']}")

        section("S11 全链路无断点复核（用户视角）")
        # U001 在问卷里留了建议（S3）→ 自动建项（S5）→ 管理层上线（S7）→ 站内提醒可见并确认（S9）
        mine = db.load_improvements(proposer_id="U001")
        flow = [i for i in mine if i["id"] == imp_survey][0]
        check("用户建议 → 改进项 → 已上线 → 回告+确认 全链路状态齐备",
              flow["status"] == "已上线" and flow["notified_at"] and flow["proposer_ack_at"],
              f"{flow['id']}: {flow['status']} / 回告 {flow['notified_at']} / 确认 {flow['proposer_ack_at']}")
        check("改进动态数据源：load_improvements(status='已上线') 含该项",
              any(i["id"] == imp_survey for i in db.load_improvements(status="已上线")))

        section("S13 用户活跃埋点与统计口径（D4，t20）")
        # 空库边界：activity_stats 必须安全返回空结构（评估页空态依据 has_data）
        a = db.activity_stats()
        check("活跃：空库 dau/wau/mau=0 且 has_data=False（空态标记）",
              (a["dau"], a["wau"], a["mau"], a["has_data"]) == (0, 0, 0, False))
        check("活跃：空库 page_heat/user_activity 为空列表",
              a["page_heat"] == [] and a["user_activity"] == [])
        # 埋点写入（真实 DAO，与 app.py 两处埋点点同一函数）：登录 + 页面切换
        db.log_user_activity("U001", "甲管理", "管理层", "", "login")
        db.log_user_activity("U001", "甲管理", "管理层", "📊 中心总览", "page_view")
        db.log_user_activity("U001", "甲管理", "管理层", "📋 我的看板", "page_view")
        db.log_user_activity("U002", "乙主管", "主管", "📊 中心总览", "page_view")
        a = db.activity_stats()
        check("活跃：今日写入 → dau=2 wau=2 mau=2（去重 user_id）",
              (a["dau"], a["wau"], a["mau"]) == (2, 2, 2), f"{a['dau']}/{a['wau']}/{a['mau']}")
        check("页面热度：login 与空页面不计入，按计数倒序",
              a["page_heat"] == [("📊 中心总览", 2), ("📋 我的看板", 1)], str(a["page_heat"]))
        u001 = [u for u in a["user_activity"] if u["user_id"] == "U001"][0]
        u002 = [u for u in a["user_activity"] if u["user_id"] == "U002"][0]
        check("逐人聚合：U001 visits=3（login+2 页面）active_days=1 姓名角色快照齐全",
              u001["visits"] == 3 and u001["active_days"] == 1
              and u001["user_name"] == "甲管理" and u001["role"] == "管理层", str(u001))
        check("逐人聚合：按 visits 倒序（U001 在前）且 U002 visits=1",
              a["user_activity"][0]["user_id"] == "U001" and u002["visits"] == 1)
        check("last_active 均非空", all(u["last_active"] for u in a["user_activity"]))
        # 时间窗口边界：30 天窗口外不计入、窗口内计入（文本时间戳字典序比较）
        _now = datetime.now()
        out_ts = (_now - timedelta(days=30, seconds=5)).strftime("%Y-%m-%d %H:%M:%S")   # 恰出窗 → 排除
        in_ts = (_now - timedelta(days=29, hours=23)).strftime("%Y-%m-%d %H:%M:%S")     # 窗口内 → 纳入
        db._execute(
            "INSERT INTO user_activity_log (user_id, user_name, role, page_path, action, created_at) "
            "VALUES ('U003','丙工程','工程师','📊 中心总览','page_view', %s)", (out_ts,))
        a = db.activity_stats()
        check("窗口边界：30 天前的记录不计入 mau / user_activity",
              a["mau"] == 2 and len(a["user_activity"]) == 2, f"mau={a['mau']}")
        db._execute(
            "INSERT INTO user_activity_log (user_id, user_name, role, page_path, action, created_at) "
            "VALUES ('U003','丙工程','工程师','📊 中心总览','page_view', %s)", (in_ts,))
        a = db.activity_stats()
        check("窗口边界：29 天前的记录计入 mau=3，但不计入 7 天窗口（wau=2）",
              a["mau"] == 3 and a["wau"] == 2 and a["dau"] == 2,
              f"dau={a['dau']} wau={a['wau']} mau={a['mau']}")
        # 未迁移态降级（与 F-04 同款）：DROP 表后统计不抛异常、返回空结构
        db._execute("DROP TABLE user_activity_log")
        a = db.activity_stats()
        check("未迁移态降级：DROP user_activity_log 后 activity_stats 返回空结构不抛异常",
              (a["dau"], a["wau"], a["mau"], a["has_data"]) == (0, 0, 0, False)
              and a["page_heat"] == [] and a["user_activity"] == [])

        section("S12 兼容降级与 public 业务库未被触碰")
        # F-06：以旧列名直查（不含 question_set），断言 submit_survey 后旧列值可正常取回——
        # 证明新增列没有破坏旧代码的显式列名读取路径（零 DDL 回滚依据的可执行证据）
        old_row = db._query_one(
            "SELECT id, user_id, score, answers, comment FROM sus_survey WHERE id = %s",
            (rec_ids["U001"],))
        check("旧列名直查可取回提交值（id/user_id/score/answers/comment 均正常）",
              old_row is not None and old_row[0] == rec_ids["U001"] and old_row[1] == "U001"
              and abs(float(old_row[2]) - 4.5) < 1e-9 and "附件" in (old_row[4] or ""),
              f"score={old_row[2] if old_row else None}")
        # F-04：未迁移态优雅降级——DROP 新表后读路径必须返回空且不抛异常（真实库，无 mock）
        db._execute("DROP TABLE eval_improvement")
        check("未迁移态降级：DROP 表后 pending_improvement_notice 返回 [] 不抛异常",
              db.pending_improvement_notice("U001") == [])
        check("未迁移态降级：DROP 表后 load_improvements 返回 [] 不抛异常",
              db.load_improvements() == [])
        db._POOL = None   # 先丢弃带 search_path 的池连接，再核对 public
        after = public_counts()
        check("public 各业务表行数前后一致（零写入）", after == before, f"{after}")
        acur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        acur.execute("SELECT count(*) FROM pg_namespace WHERE nspname = %s", (schema,))
        check("临时 schema 已删除", acur.fetchone()[0] == 0)

    finally:
        try:
            db._POOL = None
        except Exception:
            pass
        try:
            acur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            admin.close()
        except Exception:
            traceback.print_exc()

    n_fail = sum(1 for ok, _n, _e in _RESULTS if not ok)
    n_pass = len(_RESULTS) - n_fail
    print(f"\n==== 自检完成：{n_pass} 项通过，{n_fail} 项失败 ====")
    if n_fail:
        for ok, name, ev in _RESULTS:
            if not ok:
                print(f"  ✗ {name} —— {ev}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
