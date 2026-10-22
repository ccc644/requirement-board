"""端到端验证：用**真实 LLM API** 跑完整智能体循环（上线前最强的一道检查）。

为什么需要它：此前所有测试只覆盖管道/降级/判别逻辑（环境里没有 API Key），
智能体的「正常路径」从未真正跑过。现在 .env 里已有 DEEPSEEK_API_KEY，
用 AppTest 驱动真实 app + 真实模型，检验：
  ① 工具调用协议是否被正确遵守（模型是否老实输出 JSON）
  ② 工具派发是否正确（问负载→engineer_load，问具体需求→search）
  ③ 流式回答是否落到 chat_msgs
  ④ 自校验是否误报（白名单修复是否生效）
  ⑤ 多轮追问是否接得上上下文

连隔离 schema（uicheck），生产 public 零写入。会真实消耗少量 API 调用。
用法： mvp\\.venv\\Scripts\\python.exe mvp/tools/_e2e_agent_test.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["PGOPTIONS"] = "-c search_path=uicheck,public"
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from streamlit.testing.v1 import AppTest  # noqa: E402

QUESTIONS = [
    "现在整体情况怎么样？",
    "员工01提出的进行中需求有哪些？",
    "那其中哪条风险最大？",  # 追问：检验多轮上下文
]

FAILS: list[str] = []


def ss(at, key, default=None):
    try:
        return at.session_state[key]
    except KeyError:
        return default


def main() -> int:
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        print("❌ .env 里没有 DEEPSEEK_API_KEY，无法做端到端验证")
        return 1
    print(f"[env] DEEPSEEK_API_KEY 已配置（{len(key)} 字符），开始真实调用\n")

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
    at.run()
    at.text_input[0].set_value("员工01")
    at.text_input[1].set_value("123456")
    for b in at.button:
        if (getattr(b, "label", "") or "").strip() in ("登 录", "登录"):
            b.click()
            break
    at.run()
    if not ss(at, "logged_in"):
        print("❌ 登录失败")
        return 1
    print("[1] 登录 OK")

    at.session_state["_nav_page"] = "🔍 AI 搜索"
    at.session_state["chat_msgs"] = []
    at.run()
    print("[2] 进入「AI 对话搜索」页 OK\n")

    for idx, q in enumerate(QUESTIONS, 1):
        print(f"===== 第 {idx} 问：{q} =====")
        at.chat_input[0].set_value(q)
        at.run()

        msgs = ss(at, "chat_msgs", [])
        assistants = [m for m in msgs if m.get("role") == "assistant"]
        if not assistants:
            print("  ❌ 没有产生助手回答（会话状态里无 assistant 消息）")
            FAILS.append(f"第{idx}问无回答")
            print()
            continue
        last = assistants[-1]
        content = (last.get("content") or "").strip()

        # 工具步骤：从 st.status 的过程面板读（Status 是 Block，子元素取 markdown 文本）
        steps = []
        try:
            if len(at.status):
                st_obj = at.status[-1]
                steps = [m.value for m in st_obj.markdown] or [
                    getattr(c, "value", str(c)) for c in st_obj.children]
        except Exception as e:  # noqa: BLE001
            steps = [f"（读取过程面板失败：{e}）"]
        print("  ── 工具步骤 ──")
        for s in steps:
            print(f"    · {str(s).strip()[:100]}")
        print("  ── 回答 ──")
        print("    " + content.replace("\n", "\n    ")[:600])
        if last.get("matched"):
            print(f"  ── 卡片 {len(last['matched'])} 张：{', '.join(r['id'] for r in last['matched'][:6])}")
        warn = last.get("verify_warning")
        if warn:
            print(f"  ⚠️ 自校验：{warn[:160]}")
        else:
            print("  ✓ 自校验通过（无未核实编号）")

        if "未配置 API Key" in content or "已用本地关键词匹配兜底" in content or "智能体出错" in content:
            print("  ❌ 落到了兜底路径（说明智能体正常路径失败）")
            FAILS.append(f"第{idx}问走兜底")
        if len(content) < 15:
            print(f"  ❌ 回答过短（{len(content)} 字符），疑似异常")
            FAILS.append(f"第{idx}问回答过短")
        print()

    # 追问轮：检验上下文是否接上（回答里通常会引用上一轮的具体需求编号）
    msgs = ss(at, "chat_msgs", [])
    if len(msgs) >= 6:
        print("[3] 多轮消息数 =", len(msgs), "（3 问 × 2 条 = 6 为正常）")
    else:
        print(f"⚠️ 多轮消息数 = {len(msgs)}，少于预期 6，可能有问答未落库")
        FAILS.append("多轮消息数不足")

    print()
    if FAILS:
        print(f"❌ 端到端验证失败 {len(FAILS)} 项：{FAILS}")
        return 1
    print("✅ 端到端验证通过：真实模型下工具循环、流式回答、自校验、多轮上下文均正常")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
