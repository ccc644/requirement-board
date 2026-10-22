"""外壳样式注入的冒烟检查（无 GUI）。

验证 _inject_global_css 能被真实调用且注入的 <style> 文本完整、关键选择器齐全。
只读：不写任何库表。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app as kbapp  # noqa: E402


class _FakeSt:
    """只截获 st.markdown，不做任何真实渲染。"""

    def __init__(self) -> None:
        self.captured: list[str] = []

    def markdown(self, body, **kwargs):
        self.captured.append(body)

    def __getattr__(self, name):
        def _noop(*a, **k):
            return None
        return _noop


def strip_comments(css: str) -> str:
    """去掉 /* ... */ 注释，只留真正的规则文本。

    为什么必须去：反向检查是「不得出现某个字符串」的否定式断言，
    而代码注释里**天然会写到**这些被否定的历史写法（例如「v1 曾用
    position: sticky; bottom: 0，空操作」）。不剥离注释就会把自己写的
    说明文字当成违规命中，产生假阳性——本轮就真的误报过一次。
    """
    import re
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def check_login_css() -> list[str]:
    """登录页专属样式自检（_inject_login_css）。返回缺失项名称列表。"""
    real_st = kbapp.st
    fake = _FakeSt()
    kbapp.st = fake
    try:
        kbapp._inject_login_css()
    finally:
        kbapp.st = real_st

    css = "\n".join(fake.captured)
    print(f"\n✅ _inject_login_css 执行成功，注入 {len(fake.captured)} 段样式，共 {len(css)} 字符")
    required = [
        ('[data-testid="stMain"] { background: #1E2A3A; }', "登录页深色底（与侧边栏同源 #1E2A3A）"),
        ('[data-testid="stMainBlockContainer"], [data-testid="stMain"] > div', "登录页居中容器（含兜底选择器）"),
        ("min-height: 100dvh !important", "登录容器撑满视口高度（垂直居中前提）"),
        ("justify-content: center !important", "登录容器纵向居中"),
        ('[class*="st-key-kb_login_card"]', "登录卡片挂钩（keyed 容器）"),
        ("max-width: 400px !important", "卡片宽度约束"),
        ("margin: 0 auto !important", "卡片水平居中落点"),
        (".kb-login-brand { width: 100%;", "品牌区给宽度（否则收缩致整体左偏）"),
        ("box-shadow: 0 12px 36px", "卡片阴影"),
        (".kb-login-logo {", "登录页徽标样式"),
        (".kb-login-title {", "登录页主标题样式"),
        (".kb-login-sub {", "登录页副标题样式"),
        ('[class*="st-key-kb_login_card"] [data-testid="stExpander"]', "卡片内折叠区样式（用 stExpander 挂钩）"),
        ("[data-testid=\"stAppViewContainer\"]:has([class*=\"st-key-kb_login_card\"]) [data-testid=\"stSidebar\"]",
         "登录态隐藏侧边栏（:has 精确限定，不影响登录后）"),
    ]
    missing = []
    for token, name in required:
        ok = token in css
        print(f"   {'✓' if ok else '✗'} {name}")
        if not ok:
            missing.append(name)
    # 反向检查：登录页样式不得误伤业务页卡片挂钩
    if '[class*="st-key-kb_card_"]' in css:
        print("❌ 登录页样式不应包含业务页卡片挂钩 st-key-kb_card_（会互相干扰）")
        missing.append("误伤业务页卡片选择器")
    # 反向检查：死选择器——引用了 st-key-* 就必须确保该容器真的带 key。
    # 教训：kb_login_hint 曾写了规则但 expander 没给 key，规则静默失效。
    if "st-key-kb_login_hint" in css:
        print("❌ 登录页又引用了未加 key 的 st-key-kb_login_hint（死选择器）")
        missing.append("死选择器 st-key-kb_login_hint")
    return missing


def main() -> int:
    real_st = kbapp.st
    fake = _FakeSt()
    kbapp.st = fake
    try:
        kbapp._inject_global_css()
    finally:
        kbapp.st = real_st

    if not fake.captured:
        print("❌ 未捕获到任何 st.markdown 调用")
        return 1
    css = "\n".join(fake.captured)
    print(f"✅ _inject_global_css 执行成功，注入 {len(fake.captured)} 段样式，共 {len(css)} 字符")

    required = [
        ('[data-testid="stSidebar"]', "侧边栏根基"),
        ("width: var(--kb-sidebar-w) !important", "侧边栏宽度锁死 224px"),
        ("--kb-sidebar-w: 224px", "侧边栏宽度变量（顶栏偏移同源）"),
        ("--kb-header-h: 57px", "顶栏高度变量（顶栏/占位/品牌区三处同源）"),
        ('[data-testid="stSidebarHeader"] { display: none !important; }',
         "隐藏侧边栏顶行（品牌区上移，与顶栏标题对齐）"),
        ("height: var(--kb-header-h); box-sizing: border-box;",
         "品牌区与顶栏等高（两侧标题同一水平线）"),
        ('[data-testid="stSidebarContent"]', "侧边栏滚动槽修正"),
        ('[data-testid="stSidebarUserContent"]', "侧边栏底部留白"),
        ("border-top: 1px solid rgba(255,255,255,.10)", "侧边栏底部用户区分隔线（容器边框，非 <hr>）"),
        ("margin-top: auto !important;", "底部用户区推到底部（贴底）"),
        ("position: sticky; bottom: 0;", "贴底兜底：内容超一屏时 footer 钉在可视区底部"),
        ("div:has(> [class*=\"st-key-kb_sidebar_footer\"])",
         "贴底作用层用 :has 按内容命中（不依赖子级位置/嵌套深度）"),
        ("justify-content: flex-end;", "贴底双保险（auto 外边距失效也压到底）"),
        ("padding-top: calc(var(--kb-header-h) + 4px) !important;", "主区占位消费顶栏高度变量"),
        # v5 贴底机制：从滚动容器传导高度，不再用「视口高 - 手估常量」
        ('[data-testid="stSidebarContent"] {\n        display: flex !important; flex-direction: column !important;',
         "高度源头：滚动容器拉成 flex 列"),
        ('[data-testid="stSidebarHeader"] { flex: 0 0 auto; }', "收起按钮行不压缩"),
        ('[data-testid="stSidebarUserContent"] {\n        flex: 1 0 auto !important;',
         "用户区吃满剩余高度"),
        ('[data-testid="stSidebarUserContent"] div:has(> [class*="st-key-kb_sidebar_footer"])',
         "footer 包装层按内容命中（:has，不再依赖子级位置）"),
        (".kb-sb-user {", "底部用户块样式"),
        ('[data-testid="stMarkdownContainer"] p { margin: 0; }', "markdown 段落默认外边距清零"),
        ('[data-testid="stSidebar"] hr {', "侧边栏 hr 兜底样式"),
        ('[class*="st-key-kb_nav"] button[kind="secondary"]', "导航普通态"),
        ('[class*="st-key-kb_nav"] button[kind="primary"]', "导航当前页"),
        ("font-size: 13.5px", "导航字号 13.5px"),
        ("padding: 5px 10px", "导航内边距 5px 10px"),
        ('[data-testid="stSidebar"] [class*="st-key-kb_nav"] { padding-top: .625rem; }',
         "导航区顶部留白（按钮下移，分隔线不动）"),
        ('[class*="st-key-kb_card_"]', "白卡片挂钩"),
        ("margin: 0 -16px 12px; padding: 0 16px 12px;", "卡片标题条整宽分隔线（含 1px 边框）"),
        ('[data-testid="stMetric"] [data-testid="stMetricLabel"]', "KPI 指标名"),
        ('[data-testid="stMetric"] [data-testid="stMetricValue"]', "KPI 指标值"),
        ("font-size: 25px", "KPI 数值 25px"),
        (".kb-page-title { font-size: 19px; font-weight: 600; color: #1F2329; line-height: 1.15; }",
         "页头标题行高定死（修按钮与标题 7.6px 错位）"),
        (".kb-page-sub { font-size: 12px; color: #8A94A6; line-height: 1.15; }",
         "页头副标题行高定死"),
        ('[class*="st-key-kb_app_header"]', "顶栏容器"),
        ("position: fixed; top: 0; right: 0; z-index: 1000;", "顶栏滚动冻结（fixed）"),
        ("left: var(--kb-sidebar-w);", "顶栏左偏移与侧边栏宽度同源"),
        ('[data-testid="stHorizontalBlock"] > div > [data-testid="stVerticalBlock"]',
         "顶栏各件锚到同一垂直中线"),
        ("border-bottom: 1px solid #EDEFF2", "顶栏下边框"),
        ('[data-testid="stMainBlockContainer"] {\n        padding-top: calc(var(--kb-header-h) + 4px) !important;',
         "顶栏占位块消费高度变量（顶栏高 + 4px 呼吸空隙）"),
        ("gap: .75rem; align-items: center !important; height: 36px;",
         "顶栏行高锁 36px（占位值由构造确定的前提）"),
        ("padding: .625rem 1rem;", "顶栏上下内边距 10px（收薄，总高 57px）"),
        ("padding-left: 1rem !important; padding-right: 1rem !important;",
         "内容区左右基线固定 1rem（消除 wide 布局 5rem 边沟导致的 64px 错位）"),
        ('[data-testid="stMainBlockContainer"] h2 {',
         "页面标题 h2 归一到 19px/600（与系统评估页一致）"),
        ("font-size: 19px !important; font-weight: 600 !important;",
         "h2 标题字号字重规格"),
        (".kb-who { border-left", "用户胶囊分隔线"),
        # ---- 横向标签菜单（参考客诉系统 SystemEval Tab）----
        ('[class*="st-key-kb_tabs_"]', "横向标签菜单容器"),
        ("border-bottom: 1px solid #E5E7EB;", "标签条底部分隔线"),
        ("border-bottom: 2px solid transparent !important;", "非当前项透明下边框（保留占位防抖动）"),
        ('[class*="st-key-kb_tabs_"] button[kind="primary"]', "当前项高亮（主题色下边框）"),
        ("margin-bottom: -1px !important;", "标签负下外边距压住容器线"),
        # ---- 对话头像（emoji 溢出修复：改用库内置图标头像）----
        ('[data-testid="stChatMessageAvatarUser"] { background: #409EFF !important; }',
         "用户头像底色（主题蓝）"),
        ('[data-testid="stChatMessageAvatarAssistant"] { background: #1E2A3A !important; }',
         "助手头像底色（侧边栏深蓝灰）"),
    ]
    missing = [name for token, name in required if token not in css]
    for token, name in required:
        print(f"   {'✓' if token in css else '✗'} {name}")

    # 反向检查只看「真正的规则文本」，注释里的历史写法说明不算违规
    rules = strip_comments(css)

    # 反向检查：v2 的两处硬伤必须已消除——
    #   ① 顶栏左偏移不得再写死像素（必须走 --kb-sidebar-w 变量）
    #   ② 不得再有 left: 240px 这类与侧边栏宽度不同源的写死值
    # 注意：position: fixed 现在是**有意采用**的方案（v4），不算残留，故不再列入。
    leftovers = [t for t in ("left: 240px", "left: 240", "width: 240px") if t in rules]
    if leftovers:
        print(f"❌ 仍存在与侧边栏宽度不同源的写死值：{leftovers}")
        return 1

    # 反向检查：死选择器。历史教训（21 号审查）：卡片白底曾挂在 Streamlit 1.61 里
    # 并不存在的 stVerticalBlockBorderWrapper 上，导致 13 处卡片静默失效。
    # 这里挡两类已知不存在的挂钩：列没有自己的 testid；旧 border 容器 testid 已废弃。
    dead = [t for t in ('[data-testid="column"]', "stVerticalBlockBorderWrapper") if t in rules]
    if dead:
        print(f"❌ 检测到死选择器（包内不存在该挂钩）：{dead}")
        return 1

    # 贴底机制的前置条件校验（取代原来的「一律禁止 sticky」）。
    # 早期结论「sticky 是空操作」的**前提**是：侧边栏 <section> 被 react-resizable 内联
    # 设成 height:auto，滚动容器没有确定高度——那时 sticky 确实无效。
    # v5 起加了 `[data-testid="stSidebar"] { min-height:100dvh }`，而 stSidebarContent
    # 自带 height:100% + overflow:auto，滚动容器有了确定高度，sticky 才真正有意义：
    #   · 内容不满一屏 → flex 分余量把 footer 沉底（auto 外边距 + justify-content:flex-end）
    #   · 内容超一屏   → sticky bottom:0 让 footer 钉在可视区底部，不被滚走
    # 两者互补。所以这里不再禁止 sticky，而是校验「用了 sticky 就必须有高度源头」，
    # 这正是当年那条禁令真正想防的东西。
    if "position: sticky; bottom: 0;" in rules and "min-height: 100dvh !important" not in rules:
        print("❌ 用了 sticky 贴底，但侧边栏缺少高度源头（min-height:100dvh）——sticky 会是空操作")
        return 1

    # 反向检查：贴底不得再用「视口高 - 手估常量」。
    # v3/v4 用 min-height: calc(100dvh - 7rem)，机制对但常量错：侧边栏顶部真实占用
    # 约 3.2rem（收起按钮行 + 下边距），7rem 多减约 2.8rem，footer 永远碰不到底边，
    # 换主题/字号还会再失准。v5 改为从滚动容器 height:100% 传导高度，无像素常量。
    import re as _re
    brittle = _re.findall(r"min-height:\s*calc\(100dvh\s*-\s*[0-9.]+rem\)", rules)
    if brittle:
        print(f"❌ 贴底又用回了「视口高 - 手估常量」：{brittle}（应改成从滚动容器传导高度）")
        return 1
    if missing:
        print(f"❌ 缺失关键规则：{missing}")
        return 1

    # ---- 登录页专属样式 ----
    login_missing = check_login_css()
    if login_missing:
        print(f"❌ 登录页样式缺失：{login_missing}")
        return 1

    print("\n✅ 冒烟检查通过：外壳 + 登录页样式完整、关键规则齐全、反向检查全过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
