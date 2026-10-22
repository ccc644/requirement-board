"""界面改版的浏览器真实量测驱动（Python + CDP）。

为什么用 Python 而不是 PowerShell：
    Windows PowerShell 5.1 的 ClientWebSocket.ReceiveAsync + Task.Wait 在本沙箱内会死锁
    （同步阻塞与 WebSocket 的异步实现冲突）。Python 的 asyncio + websockets 稳定可靠。

做什么：
    启动本机 Edge 无头模式 → 连 CDP → 登录 → 逐页切导航 → 执行 tools/ui_probe 下的量测脚本
    → 落盘 JSON（供改版前后对比）+ 全页截图（供人工目视）。

安全：只填登录表单、读 DOM、截图，不点击任何业务按钮；目标是隔离 schema 实例
      （PGOPTIONS=search_path=uicheck,public），生产 public 表不受影响。

用法：
    mvp\\.venv\\Scripts\\python.exe mvp/tools/_ui_measure.py --port 8555 ^
        --pages mvp/tools/ui_probe/pages.json ^
        --out mvp/out/_measure_after.json ^
        --shot mvp/out/_shot_after.png
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
import websockets

ROOT = Path(__file__).resolve().parents[2]
PROBE_DIR = Path(__file__).resolve().parent / "ui_probe"
EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]
SIDEBAR_SEL = '[data-testid="stSidebar"]'


def find_browser() -> str:
    for p in EDGE_CANDIDATES:
        if Path(p).exists():
            return p
    raise SystemExit("未找到 Edge/Chrome 可执行文件")


def read_js(name: str) -> str:
    p = PROBE_DIR / name
    if not p.exists():
        raise SystemExit(f"探针脚本缺失：{p}")
    return p.read_text(encoding="utf-8")


def fill(js: str, mapping: dict[str, str]) -> str:
    for k, v in mapping.items():
        js = js.replace(k, v)
    return js


def jstr(value: str) -> str:
    """安全嵌入 JS 的字符串字面量。"""
    return json.dumps(value, ensure_ascii=False)


class Cdp:
    def __init__(self, ws) -> None:
        self._ws = ws
        self._id = 0

    async def call(self, method: str, params: dict | None = None, timeout: float = 90.0) -> dict:
        self._id += 1
        mid = self._id
        await self._ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                raise TimeoutError(f"CDP 超时：{method}")
            raw = await asyncio.wait_for(self._ws.recv(), timeout=remain)
            msg = json.loads(raw)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"CDP 错误 {method}: {msg['error']}")
                return msg.get("result", {})

    async def evaluate(self, expr: str, timeout: float = 90.0):
        res = await self.call(
            "Runtime.evaluate",
            {"expression": expr, "returnByValue": True, "awaitPromise": True},
            timeout=timeout,
        )
        if res.get("exceptionDetails"):
            detail = res["exceptionDetails"]
            desc = (detail.get("exception") or {}).get("description") or detail.get("text")
            raise RuntimeError(f"JS 异常：{desc}")
        return res.get("result", {}).get("value")


def wait_for_cdp(port: int, timeout: float = 40.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = requests.get(f"http://127.0.0.1:{port}/json/list", timeout=2)
            if r.ok:
                targets = r.json()
                pages = [t for t in targets if t.get("type") == "page"]
                if pages:
                    return pages[0]
        except Exception:
            pass
        time.sleep(0.4)
    raise SystemExit(f"无法连接 CDP 127.0.0.1:{port}")


async def run(args: argparse.Namespace) -> int:
    browser = find_browser()
    profile = Path(tempfile.gettempdir()) / "kb_uicheck_edge"
    if profile.exists():
        shutil.rmtree(profile, ignore_errors=True)

    url = f"http://localhost:{args.port}"
    cmd = [
        browser, "--headless=new", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", "--disable-extensions",
        f"--remote-debugging-port={args.cdp_port}", f"--user-data-dir={profile}",
        f"--window-size={args.width},{args.height}", "--hide-scrollbars", url,
    ]
    print(f"[cdp] 启动无头浏览器 → {url}")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        try:
            target = wait_for_cdp(args.cdp_port)
        except SystemExit:
            # 端口没起来（常见原因：同端口已有残留 Edge 实例）。清一次再试。
            print("[cdp] 首次连接失败，清理残留浏览器进程后重试")
            subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"], capture_output=True)
            time.sleep(2)
            if profile.exists():
                shutil.rmtree(profile, ignore_errors=True)
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            target = wait_for_cdp(args.cdp_port, timeout=60)
        print(f"[cdp] 已连接目标：{target.get('url')}")
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024) as ws:
            cdp = Cdp(ws)
            # 不调用 Runtime.enable：它会持续推送 executionContextCreated 等事件，
            # 对「一次性求值」没有价值，反而淹没响应通道。Runtime.evaluate 无需启用。
            await cdp.evaluate("1")

            # ---- 登录 ----
            login_js = fill(read_js("login.js"), {"__USER__": jstr(args.user), "__PASS__": jstr(args.password)})
            r = await cdp.evaluate(login_js)
            print(f"[login] {json.dumps(r, ensure_ascii=False)}")
            if not isinstance(r, dict) or not r.get("ok"):
                raise SystemExit(f"登录失败：{r}")
            visible = await cdp.evaluate(read_js("wait_sidebar.js"))
            print(f"[login] 侧边栏可见 = {visible}")
            if not visible:
                raise SystemExit("登录后侧边栏未出现")
            await asyncio.sleep(2)

            # ---- 环境自证 + 注入公共探针 ----
            await cdp.evaluate(read_js("common.js"))
            env = await cdp.evaluate(read_js("env.js"))
            print(f"[env] viewport={env['viewport']['w']}x{env['viewport']['h']} "
                  f"keyed={len(env['keyed'])} testids={len(env['testids'])}")

            measure_js = fill(read_js("measure.js"), {"__SIDEBAR__": SIDEBAR_SEL})
            click_tpl = fill(read_js("click_nav.js"), {"__SIDEBAR__": SIDEBAR_SEL})

            labels = args.labels
            results: dict[str, dict] = {}
            for label in labels:
                click_js = fill(click_tpl, {"__LABEL__": jstr(label)})
                clicked = await cdp.evaluate(click_js)
                if not isinstance(clicked, dict) or not clicked.get("ok"):
                    print(f"[warn] 导航点击失败 [{label}]：{json.dumps(clicked, ensure_ascii=False)}")
                    continue
                want = label
                wait_js = fill(read_js("wait_page.js"), {"__LABEL__": jstr(want)})
                switched = await cdp.evaluate(wait_js)
                await asyncio.sleep(0.9)
                m = await cdp.evaluate(measure_js)
                results[label] = {"switched": switched, "measure": m}
                print(f"[measure] {label} switched={switched} nav={m['nav']['count']} "
                      f"cards={len(m['cards'])} metrics={len(m['metrics'])}")

            payload = {
                "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "appUrl": url,
                "environment": env,
                "pages": results,
            }
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"[out] {out}")

            # ---- 截图 ----
            if args.shot:
                await cdp.evaluate("(function(){window.scrollTo(0,0);return 1;})()")
                await asyncio.sleep(0.6)
                cap = await cdp.call("Page.captureScreenshot",
                                     {"format": "png", "captureBeyondViewport": True})
                shot = Path(args.shot)
                shot.parent.mkdir(parents=True, exist_ok=True)
                shot.write_bytes(base64.b64decode(cap["data"]))
                print(f"[shot] {shot}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    print("[done]")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8555, help="Streamlit 端口")
    ap.add_argument("--cdp-port", type=int, default=9222)
    ap.add_argument("--user", default="员工01")
    ap.add_argument("--password", default="123456")
    ap.add_argument("--pages", default=str(PROBE_DIR / "pages.json"), help="页面标签 JSON 文件")
    ap.add_argument("--out", required=True, help="量测结果 JSON 输出路径")
    ap.add_argument("--shot", default="", help="截图输出路径")
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=1000)
    args = ap.parse_args()

    pages_file = Path(args.pages)
    if not pages_file.exists():
        raise SystemExit(f"页面标签文件缺失：{pages_file}")
    args.labels = json.loads(pages_file.read_text(encoding="utf-8"))
    if not isinstance(args.labels, list) or not args.labels:
        raise SystemExit("页面标签文件必须是 JSON 数组")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
