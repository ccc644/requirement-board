"""AppTest 复现：AI 对话搜索的两个 bug。

bug1: 冻结栏搜索提交后，输入框文本不清空
bug2: 对话发生后，点击侧边栏其它菜单无法跳转

连隔离 schema（uicheck），生产 public 零写入。
用法： mvp\\.venv\\Scripts\\python.exe mvp/tools/_repro_chat_bugs.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["PGOPTIONS"] = "-c search_path=uicheck,public"
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402


def ss(at: AppTest, key: str, default=None):
    """AppTest 的 SafeSessionState 没有 .get()，统一用本助手读键。"""
    try:
        return at.session_state[key]
    except KeyError:
        return default


def main() -> int:
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    print("[1] 首跑元素数：text_input =", len(at.text_input), "；button =", len(at.button))

    # ---- 登录 ----
    at.text_input[0].set_value("员工01")
    at.text_input[1].set_value("123456")
    # 表单提交按钮（st.form_submit_button 在 AppTest 里也归 at.button）
    clicked = False
    for b in at.button:
        try:
            if (b.label or "").strip() in ("登 录", "登录"):
                b.click()
                clicked = True
                break
        except Exception:
            continue
    if not clicked:
        print("❌ 没找到登录按钮；button 标签 =", [getattr(b, "label", "?") for b in at.button])
        return 1
    at.run()
    logged = ss(at, "logged_in")
    print("[2] 登录后 logged_in =", logged)
    if not logged:
        return 1

    # ---- 直达 AI 搜索页并种入一段对话（模拟「对话完」的状态）----
    at.session_state["_nav_page"] = "🔍 AI 搜索"
    reqs_stub = [{
        "id": "REQ-202608-0001", "title": "复现用需求", "status": "进行中", "priority": "P1",
        "owner_ids": [], "source": "研发", "created_at": "2026-08-01", "description": "",
        "customer": "", "tags": [],
    }]
    at.session_state["chat_msgs"] = [
        {"role": "user", "content": "进行中的需求"},
        {"role": "assistant", "content": "找到 1 条。", "matched": reqs_stub, "filter": {"status": "进行中"}},
    ]
    at.run()
    print("[3] 搜索页渲染后：chat_msgs 长度 =", len(ss(at, "chat_msgs", [])))
    print("    当前页 =", ss(at, "_nav_page"))

    # ---- bug2 复现：点击侧边栏「📊 中心总览」----
    nav_btn = None
    for b in at.button:
        if getattr(b, "key", "") == "nav_btn_📊 中心总览":
            nav_btn = b
            break
    if nav_btn is None:
        print("❌ 没找到导航按钮 nav_btn_📊 中心总览")
        print("    现有按钮 key =", [getattr(b, "key", "") for b in at.button][:20])
        return 1
    nav_btn.click()
    at.run()
    page_after = ss(at, "_nav_page")
    print("[4] 点击「中心总览」后 _nav_page =", repr(page_after), "→",
          "✅ 跳转成功" if page_after == "📊 中心总览" else "❌ bug2 复现：未跳转")

    # ---- bug1 复现：顶栏搜索提交后输入框是否残留 ----
    # 回到 AI 搜索页，模拟顶栏输入一次查询
    at.session_state["_nav_page"] = "🔍 AI 搜索"
    at.run()
    # 顶栏 text_input key=_global_search_q
    gs = None
    for t in at.text_input:
        if getattr(t, "key", "") == "_global_search_q":
            gs = t
            break
    if gs is None:
        print("[5] ⚠️ 顶栏搜索框未找到（key=_global_search_q）——页面可能没到搜索态，跳过 bug1 检查")
    else:
        gs.set_value("P1 需求")
        at.run()  # 触发变化分支 → 跳搜索页 + prefill
        left = ss(at, "_global_search_q")
        last = ss(at, "_global_search_last")
        print(f"[5] 提交后 _global_search_q = {left!r}（应为 ''），_global_search_last = {last!r}")
        print("    →", "✅ 已清空" if left == "" else "❌ bug1 复现：文本残留")

    # ---- bug2 真实路径补充：经 st.chat_input 提交一轮对话后再点导航 ----
    at.session_state["_nav_page"] = "🔍 AI 搜索"
    at.session_state["chat_msgs"] = []
    at.run()
    ci = at.chat_input[0] if len(at.chat_input) else None
    if ci is None:
        print("[6] ⚠️ AppTest 未捕获 chat_input，跳过真实路径检查")
    else:
        ci.set_value("进行中的需求")
        at.run()  # 模拟提交：走 page_search 的 if prompt 分支（无 API Key → 关键词兜底）
        n_after_submit = len(ss(at, "chat_msgs", []))
        print(f"[6] chat_input 提交后 chat_msgs 长度 = {n_after_submit}（预期 2：user+assistant）")
        # 再跑一次（无新输入）：消息数不应增长（不重复处理）
        at.run()
        n_after_rerun = len(ss(at, "chat_msgs", []))
        print(f"    再次运行后长度 = {n_after_rerun}", "✅ 无重复处理" if n_after_rerun == n_after_submit
              else "⚠️ 有重复处理（注意：AppTest 的值不清空行为与真实前端不同，仅供观察）")
        nav2 = None
        for b in at.button:
            if getattr(b, "key", "") == "nav_btn_📅 周报":
                nav2 = b
                break
        if nav2:
            nav2.click()
            at.run()
            print("    提交对话后点击「周报」→ _nav_page =", repr(ss(at, "_nav_page")), "→",
                  "✅ 跳转成功" if ss(at, "_nav_page") == "📅 周报" else "❌ bug2 复现")
        else:
            print("    ⚠️ 未找到 nav_btn_📅 周报")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
