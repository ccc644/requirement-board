"""
ui/layout.py — 从 app.py 拆分生成（函数体逐字保留）。
"""
from __future__ import annotations

# ---- 第三方 / 标准库 ----
from llm_gateway import PROVIDERS
import os
import streamlit as st

# ---- 项目内部 ----
from config import APP_TITLE, NAV_GROUPS, NAV_STATE_KEY
from services.stats import _cached_db_stats
from state import _do_logout, get_current_user



def _resolve_nav(pages: dict) -> str:
    """解析当前页（t31 导航迁移，取代 nav_radio 三件套）。

    状态机：① 消费 _pending_nav/_pending_nav_target 跳转标记（跳转机制原样保留）；
    ② 当前页不在可选页（如角色不可见/换人登录残留）时回落到第一页。
    返回页键（= 导航项中文名，与页面显示名一致，供埋点与分发共用）。
    """
    keys = list(pages.keys())
    if st.session_state.pop("_pending_nav", None):
        target = st.session_state.pop("_pending_nav_target", keys[0])
        if target in keys:
            st.session_state[NAV_STATE_KEY] = target
    if st.session_state.get(NAV_STATE_KEY) not in keys:
        st.session_state[NAV_STATE_KEY] = keys[0]
    return st.session_state[NAV_STATE_KEY]


def _render_app_header(page_title: str, todo_count: int):
    """顶部 Header（t31）：当前页标题 + 全局搜索入口 + 通知铃铛 + 用户胶囊。

    - 搜索为「入口+联动」语义：输入后回车跳转 AI 搜索页，查询词经 _search_prefill
      预填到目标页输入框并自动执行一次 AI 解析（page_search 消费后 pop，不残留）；
    - 铃铛角标接现有待办总数（_pending_notices_all 七元组之和），点击前往「我的看板」；
    - 退出登录入口在侧边栏底部（参考客诉系统 AppSidebar），顶栏不再放退出。
    """
    # 整段包进 keyed 容器 kb_app_header，供全局 CSS 挂钩成白底顶栏。
    # 版式：标题 | 搜索 | 弹性占位 | 铃铛 | 用户胶囊。弹性占位列把右侧三件推成一组，
    # 避免「搜索框」和「铃铛」之间出现一段说不清来由的大空档（原来的列比就是这个效果）。
    with st.container(key="kb_app_header"):
        # 纵向对齐统一交给 vertical_alignment="center"；水平间距由 CSS 收窄（见第 6 节）。
        t_col, s_col, _fill, b_col, u_col = st.columns(
            [3.2, 2.6, 1.2, 0.7, 2.0], vertical_alignment="center", gap="small"
        )
        with t_col:
            # line-height 必须显式定：正文继承 1.6 会把 15.5px 标题的行盒撑到 ~24.8px，
            # 该行盒按列居中时文字重心比搜索框/铃铛高 8px（真实浏览器实测：
            # 标题中心 148 vs 搜索/铃铛 140）。收到 1.2 后行盒≈18.6px，三者中心齐平。
            st.markdown(
                f'<div style="font-size:15.5px;font-weight:600;color:#1F2329;'
                f'line-height:1.2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
                f'{page_title}</div>',
                unsafe_allow_html=True,
            )
        with s_col:
            # 提交后清空输入框（bug1 修复）。Streamlit 禁止在 widget 实例化之后改写其
            # session_state 键，所以不能在下面直接置空 _global_search_q——必须用
            # 「本轮立旗标 → rerun → 下一轮创建 widget 之前清空」的标准配方。
            # 旗标键走 _global_search 前缀，登出时的前缀清扫自动覆盖。
            if st.session_state.pop("_global_search_clear", False):
                st.session_state["_global_search_q"] = ""
            q = st.text_input("全局搜索", placeholder="🔍 搜索需求 / 编号 / 关键词",
                              label_visibility="collapsed", key="_global_search_q")
            # 仅在输入值「发生变化」时跳转一次（回车/提交触发重跑）：避免输入残留导致后续
            # 切到别的页又被弹回搜索页；查询词经 _search_prefill 带给搜索页成为对话第一句。
            if q != st.session_state.get("_global_search_last", ""):
                if q.strip():
                    st.session_state[NAV_STATE_KEY] = "🔍 AI 搜索"
                    st.session_state["_search_prefill"] = q
                    st.session_state["_global_search_last"] = ""   # 基准同步为清空后的状态
                    st.session_state["_global_search_clear"] = True  # 下一轮开头清空输入框
                    st.rerun()
                else:
                    st.session_state["_global_search_last"] = q
        with b_col:
            if st.button(f"🔔{'+' + str(todo_count) if todo_count else ''}", key="kb_bell",
                         use_container_width=True,
                         help=f"您有 {todo_count} 项待办，点击前往「我的看板」处理"):
                st.session_state[NAV_STATE_KEY] = "📋 我的看板"
                st.rerun()
        with u_col:
            cur = get_current_user()
            if cur:
                initial = (cur.get("name", "?") or "?")[:1]
                # 末尾放一个零宽字符：让用户块的 markdown 包装层高度等于整行行盒，
                # 否则包装层只包住头像行，flex 居中时会把整块往上带偏。
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:8px;'
                    f'white-space:nowrap;overflow:hidden;">'
                    f'<span class="kb-avatar">{initial}</span>'
                    f'<span class="kb-who">{cur["name"]} · {cur["role"]}</span></div>'
                    f'&#8203;',
                    unsafe_allow_html=True,
                )


def _nav_tabs(tabs: list[tuple[str, str]], state_key: str) -> str:
    """横向标签菜单（版式参考客诉系统 SystemEval 的 Tab 切换条）。

    tabs：[(键, 显示文案), ...]；state_key：记住当前项的 session 键。
    返回当前选中的键；点击其它项时写状态并 st.rerun（与本文件既有跳转机制一致）。

    为什么用一组 st.button 而不是 st.tabs：
    ① st.tabs 是「同时渲染全部面板」的语义，本文件多处是「只渲染当前视图」，
       用它会白白执行另外两个分支（含各自的数据查询），既慢又可能触发副作用；
    ② 参考系统的样式是「当前项底部一道短横线」，靠 kind 区分 primary/secondary 即可
       精确命中，不需要给 st.tabs 的内部 DOM 打补丁（那种做法随版本升级极易失效）。
    键名统一前缀 kb_tab_，CSS 据此挂钩（与 kb_nav / kb_card_ 同一套路）。
    """
    if not tabs:
        return ""
    keys = [k for k, _ in tabs]
    if st.session_state.get(state_key) not in keys:
        st.session_state[state_key] = keys[0]
    with st.container(key=f"kb_tabs_{state_key}"):
        cols = st.columns(len(tabs), gap="small")
        for col, (key, label) in zip(cols, tabs):
            with col:
                if st.button(
                    label,
                    key=f"kb_tab_{state_key}_{key}",
                    use_container_width=True,
                    type="primary" if key == st.session_state[state_key] else "secondary",
                ):
                    if key != st.session_state[state_key]:
                        st.session_state[state_key] = key
                        st.rerun()
    return st.session_state[state_key]


def render_sidebar(pages: dict, current_page: str, my_todo: int = 0):
    """三段式布局之侧边栏（t31）：品牌区 → 分组导航 → 底部问卷入口 + LLM 配置。

    pages：角色过滤后的页字典（不可见页已由 main() pop）；
    current_page：当前页键（active 高亮）；my_todo：待办总数（「我的看板」角标）。
    导航改为按钮 + session_state（NAV_STATE_KEY），取代旧 nav_radio。
    """
    with st.sidebar:
        # ---- 1) 品牌区（副标题按要求去掉，只留主标题）----
        st.markdown(
            f'<div class="kb-brand"><div class="t">🛠️ {APP_TITLE}</div></div>',
            unsafe_allow_html=True,
        )

        # ---- 2) 分组导航（同一页面值不触发重跑；跳转由 _resolve_nav 统一消费）。
        # t34 F-03：整段包进 keyed 容器 kb_nav，导航态 CSS 只作用于该作用域，不误伤问卷 CTA ----
        with st.container(key="kb_nav"):
            # 分组标题（需求 / 管理与评估）按要求去掉，NAV_GROUPS 仍决定条目顺序
            for _group, items in NAV_GROUPS:
                for label in items:
                    if label not in pages:
                        continue  # 角色不可见（如系统评估）——过滤逻辑在 main() 完成
                    shown = label
                    if label == "📋 我的看板" and my_todo:
                        # 草图红点角标在 st.button 内无法内嵌 HTML，一期以「· N」文本计数近似
                        shown = f"{label} · {my_todo}"
                    if st.button(shown, key=f"nav_btn_{label}", use_container_width=True,
                                 type="primary" if label == current_page else "secondary"):
                        if label != current_page:
                            st.session_state[NAV_STATE_KEY] = label
                            st.rerun()

        # ---- 3) 分隔线（原「系统问卷入口」已按要求删除；问卷机制本身保留：
        #      自动弹窗仍由 main() 的 _maybe_show_survey() 按冷却期触发，
        #      数据与统计（系统评估页 D2 区块）不受影响） ----
        st.markdown("---")

        # ---- 4) LLM 配置收进折叠 expander（session_state 键名与读取行为不变）----
        with st.expander("⚙️ LLM 配置", expanded=False):
            provider_options = {k: v["name"] for k, v in PROVIDERS.items()}
            st.selectbox(
                "服务商",
                options=list(provider_options.keys()),
                format_func=lambda x: provider_options[x],
                key="provider",
            )

            provider_cfg = PROVIDERS[st.session_state.provider]
            api_key_env = provider_cfg["api_key_env"]
            env_value = os.environ.get(api_key_env, "")

            st.text_input(
                f"API Key（环境变量 {api_key_env}）",
                value=st.session_state.api_key or env_value,
                type="password",
                placeholder=f"前往 {provider_cfg['website']} 申请",
                key="api_key",
            )

            st.text_input(
                "模型（留空用默认）",
                value=st.session_state.model,
                placeholder=f"默认：{provider_cfg['default_model']}",
                key="model",
            )

            model_used = st.session_state.model or provider_cfg["default_model"]
            st.caption(f"当前模型：`{model_used}`")

            connected = bool(st.session_state.api_key)
            if connected:
                st.success("✅ LLM 已就绪")
            else:
                st.warning("⚠️ 未配置 API Key，AI 调用将失败但可演示流程")

        # ---- 5) 系统信息（按用户要求收纳）：数据统计 + 问卷冷却提示
        # 这两行小字原先平铺在侧边栏底部，与导航/问卷按钮/LLM 挤在一起，视觉嘈杂。
        # 收进一个默认折叠的「ℹ️ 系统信息」区块后，底部只剩：导航 → 问卷按钮 → LLM 配置 → 用户区。
        stats = _cached_db_stats()
        with st.expander("ℹ️ 系统信息", expanded=False):
            if stats["exists"]:
                st.caption(
                    f"已保存：需求 {stats['req_count']} 条 · 项目 {stats.get('proj_count', 0)} 个 "
                    f"· 附件 {stats['attach_count']} 份"
                )
            else:
                st.caption("数据库未初始化")

        # ---- 6) 底部用户区 + 退出登录（参考客诉系统 AppSidebar：用户卡片 + 退出入口在最底部）
        # 分隔线由容器 CSS 的 border-top 提供，不再用 st.markdown("---")——
        # 那个 <hr> 会被容器的 padding-top 下移，再被 sticky 的不透明背景切掉上半部分，
        # 结果就是一条残线压在用户名上（用户截图可见的那处重合）。
        me_sb = get_current_user()
        if me_sb:
            with st.container(key="kb_sidebar_footer"):
                st.markdown(
                    f'<div class="kb-sb-user">'
                    f'<span class="kb-avatar">{(me_sb.get("name", "?") or "?")[:1]}</span>'
                    f'<div class="kb-sb-user-meta">'
                    f'<div class="kb-sb-user-name">{me_sb["name"]}</div>'
                    f'<div class="kb-sb-user-role">{me_sb["role"]}</div>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )
                if st.button("🚪 退出登录", key="kb_logout_sb", use_container_width=True):
                    _do_logout()
