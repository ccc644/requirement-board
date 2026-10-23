"""P0 自检：自校验 / 失败自愈 / 四个只读工具。

覆盖：
1) ai_helper.extract_req_ids 的编号提取（含子需求 -S 后缀、去重）
2) _chat_verify_reply 的三种情形：干净、编造（须报出未知名）、无编号
3) 四个工具函数在各缺失分支下都返回可读文案、不抛异常
4) 接线完整性：_NO_ARG_TOOLS 四个 action 都在，提示词里都写了
5) 护栏常量仍存在（步数上限 / 格式自愈次数）

连隔离 schema（uicheck），生产 public 零写入。
用法： mvp\\.venv\\Scripts\\python.exe mvp/tools/_p0_smoke.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["PGOPTIONS"] = "-c search_path=uicheck,public"
sys.path.insert(0, str(ROOT))

import ai_helper  # noqa: E402
import pages.search as chat  # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"   {'✓' if ok else '✗'} {name}" + (f" —— {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def main() -> int:
    print("== 1) 编号提取 ==")
    txt = "看 REQ-202608-0007 和 REQ-202608-0008，还有子需求 REQ-202608-0007-S1；重复 REQ-202608-0007"
    ids = ai_helper.extract_req_ids(txt)
    check("提取全部编号并去重", ids == ["REQ-202608-0007", "REQ-202608-0007-S1", "REQ-202608-0008"], str(ids))
    check("空文本安全", ai_helper.extract_req_ids("") == [])

    print("\n== 2) 回答自校验 ==")
    allowed = {"REQ-202608-0007", "REQ-202608-0008"}
    check("干净回答不报错",
          chat._chat_verify_reply("共 2 条：REQ-202608-0007、REQ-202608-0008", allowed) == "")
    w = chat._chat_verify_reply("其中 REQ-202608-0099 最紧急", allowed)
    check("编造编号被抓出", "REQ-202608-0099" in w and "自校验未通过" in w, w[:48] + "…")
    check("无编号回答不报错", chat._chat_verify_reply("你好，我可以帮你查需求。", allowed) == "")
    w2 = chat._chat_verify_reply("REQ-202608-0001 已终止", set())
    check("白名单为空时任何编号都算可疑", "REQ-202608-0001" in w2)

    print("\n== 3) 四个只读工具（打桩数据，不依赖真实库内容）==")
    stub = [
        {"id": "REQ-A", "title": "甲", "status": "进行中", "priority": "P1", "owner_ids": ["U010"],
         "requester_id": "U001", "source": "研发", "created_at": "2026-08-01",
         "requested_due_date": "2020-01-01", "tags": [], "description": ""},
        {"id": "REQ-B", "title": "乙", "status": "已完成", "priority": "P2", "owner_ids": ["U010"],
         "requester_id": "U001", "source": "销售", "created_at": "2026-08-02",
         "requested_due_date": "", "tags": [], "description": ""},
    ]
    chat.get_requirements = lambda: stub
    chat._user_task_count_map = lambda: {"U010": 1}
    chat._user_load_map = lambda: {"U010": 3.5}
    chat._can_view_rating_detail = lambda: True

    load = chat._tool_engineer_load()
    check("engineer_load 含人名与条数", "U010" in load or "张" in load or "1 条在跑" in load, load.replace("\n", " / ")[:60])
    ov = chat._tool_stats_overview()
    check("stats_overview 含总数与逾期", "需求总数 2" in ov and "逾期未完成：1" in ov, ov.replace("\n", " / ")[:70])
    check("stats_overview 满意度行在位", "满意度" in ov)
    imp = chat._tool_improvement_status()
    check("improvement_status 可读", ("改进项" in imp or "暂无改进项" in imp), imp.replace("\n", " / ")[:60])
    act = chat._tool_activity_stats()
    check("activity_stats 可读", ("活跃" in act or "暂无活跃记录" in act), act.replace("\n", " / ")[:60])

    print("\n== 3b) 空数据分支不抛异常 ==")
    chat.get_requirements = lambda: []
    check("空需求下 stats_overview 有兜底文案", "暂无需求" in chat._tool_stats_overview())
    chat._user_task_count_map = lambda: {}
    chat._user_load_map = lambda: {}
    check("无负载时 engineer_load 有兜底文案", "没有" in chat._tool_engineer_load())

    print("\n== 3c) 回归：聚合工具输出里的编号必须进白名单（用户实测踩到的 bug）==")
    chat.get_requirements = lambda: [
        {"id": "REQ-202608-0007", "title": "RX老机型小摆臂设计", "status": "进行中", "priority": "P1",
         "owner_ids": ["U010"], "requester_id": "U001", "source": "研发", "created_at": "2026-08-01",
         "requested_due_date": "2026-09-15", "tags": [], "description": ""},
    ]
    ov2 = chat._tool_stats_overview()
    ov_ids = ai_helper.extract_req_ids(ov2)
    check("stats_overview 逾期清单里带编号", ov_ids == ["REQ-202608-0007"], str(ov_ids))
    # 白名单来源 = 工具结果文本本身 → 聚合工具命中的编号也能被认可
    check("聚合工具结果文本可提取编号（白名单来源）",
          chat._chat_verify_reply(f"有 1 条逾期：{ov_ids[0]}，建议跟进", set(ov_ids)) == "")
    # 反向：没进白名单时仍要报错（自校验不能被削weak）
    check("未进白名单的编号仍会被抓出",
          "自校验未通过" in chat._chat_verify_reply(f"看 {ov_ids[0]}", set()))

    print("\n== 3d) 跨轮白名单种子 ==")
    hist = [
        {"role": "user", "content": "查一下 0007"},
        {"role": "assistant", "content": "REQ-202608-0007 已超期", "ids": ["REQ-202608-0007"]},
        {"role": "user", "content": "那条呢"},
    ]
    seed = chat._chat_whitelist_from_history(hist)
    check("历史里已核实的编号进入白名单", seed == {"REQ-202608-0007"}, str(seed))
    check("跨轮引用不再误报",
          chat._chat_verify_reply("REQ-202608-0007 仍在进行中", seed) == "")
    check("用户提问里的编号不算白名单（只说没查=应报错）",
          chat._chat_whitelist_from_history([{"role": "user", "content": "REQ-202608-0099 呢"}]) == set())

    print("\n== 4) 接线完整性 ==")
    for a in ("engineer_load", "stats_overview", "improvement_status", "activity_stats"):
        check(f"_NO_ARG_TOOLS 含 {a}", a in chat._NO_ARG_TOOLS)
        check(f"提示词声明了 {a}", a in ai_helper.A5C_AGENT_SYSTEM)

    print("\n== 5) 护栏常量 ==")
    check("单轮工具步数上限存在", chat._MAX_TOOL_STEPS >= 1, f"_MAX_TOOL_STEPS={chat._MAX_TOOL_STEPS}")
    check("格式自愈次数存在", chat._MAX_FORMAT_RETRY >= 1, f"_MAX_FORMAT_RETRY={chat._MAX_FORMAT_RETRY}")

    print("\n== 6) 工具调用 vs 最终回答的判别（线上弱模型易踩）==")
    check("纯 JSON 工具调用 → 判定为工具调用",
          chat._chat_is_tool_call('{"action": "search", "filter": {"status": "进行中"}}') is True)
    check("```json 围栏包裹 → 仍判定为工具调用",
          chat._chat_is_tool_call('```json\n{"action": "engineer_load"}\n```') is True)
    check("前面带一句「好的我来查」→ 仍判定为工具调用",
          chat._chat_is_tool_call('好的，我来查询一下。\n{"action": "search", "filter": {}}') is True)
    check("自然语言回答 → 判定为最终回答",
          chat._chat_is_tool_call("一共 6 条需求，其中 5 条在跑。") is False)
    check("含编号的自然语言回答 → 最终回答（不误判）",
          chat._chat_is_tool_call("REQ-202608-0007 已超期 6 天，建议优先跟进。") is False)
    check("空串安全", chat._chat_is_tool_call("") is False)
    check("忘记 action 包装、只给筛选条件 → 按工具调用处理",
          chat._chat_is_tool_call('{"status": "进行中", "priority": "P1"}') is True)
    check("筛选字段集合包含协议里的全部字段",
          chat._FILTER_KEYS >= {"keyword", "owner", "requester", "source", "status",
                              "priority", "keywords", "project", "created_after",
                              "created_before", "due_before", "sort", "limit"})
    # 超长 JSON 被截断且 action 键在截断点之后（开头是 { → 仍按调用处理）
    long_json = '{"filter": {"keywords": ["' + "甲" * 500 + '"]}, "action": "search"}'
    check("超长 JSON 截断后仍判定为工具调用",
          chat._chat_is_tool_call(long_json[:chat._TOOL_HEAD_CHARS]) is True)

    print()
    if FAILS:
        print(f"❌ P0 自检失败 {len(FAILS)} 项：{FAILS}")
        return 1
    print("✅ P0 自检全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
