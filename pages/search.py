"""
pages/search.py — 从 app.py 拆分生成（函数体逐字保留）。
"""
from __future__ import annotations

# ---- 第三方 / 标准库 ----
from collections import Counter
from llm_gateway import parse_json_response
import ai_helper
import db
import json
import streamlit as st

# ---- 项目内部 ----
from config import TODAY_STR
from services.evaluation import _can_view_rating_detail, _ratings, improvement_health, satisfaction_stats
from services.people import _owner_names, _user_load_map, _user_task_count_map
from services.requirements import apply_filter, get_requirement, get_requirements, simple_keyword_match
from state import USER_ID_TO_NAME
from ui.components import priority_badge, status_badge



_MAX_TOOL_STEPS = 4  # 智能体单轮最多工具调用次数（成本护栏，防死循环）


_MAX_FORMAT_RETRY = 2  # 工具调用 JSON 格式错误的自我修正次数（超过才降级兜底）


_TOOL_HEAD_CHARS = 400  # 判定「工具调用 vs 最终回答」时先缓冲的字符数


_MAX_TOOL_RESULT_CHARS = 8000  # 单条工具结果注入上下文的上限（成本护栏，超出截断并告知模型）


_FILTER_KEYS = {
    "keyword", "owner", "requester", "source", "status", "priority", "keywords",
    "project", "created_after", "created_before", "due_before", "sort", "limit",
}


_NO_ARG_TOOLS = {
    "engineer_load": ("👷 查看工程师负载", _tool_engineer_load),
    "stats_overview": ("📊 查看整体状况", _tool_stats_overview),
    "improvement_status": ("🔄 查看改进闭环", _tool_improvement_status),
    "activity_stats": ("👥 查看使用活跃", _tool_activity_stats),
}


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
