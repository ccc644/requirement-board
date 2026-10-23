"""
ui/styles.py — 全局与登录页样式注入。

样式本体已抽到 assets/*.css（原先 556 行 CSS 内嵌在 app.py 的字符串里），
本模块只负责按需读取并注入 <style>。读取按文件名缓存，避免每次 rerun 都读盘。
"""
from __future__ import annotations

import os

import streamlit as st

_ASSETS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets"
)


@st.cache_data(show_spinner=False)
def _read_asset(name: str) -> str:
    """读取 assets 下的样式表（按文件名缓存）。"""
    with open(os.path.join(_ASSETS_DIR, name), encoding="utf-8") as f:
        return f.read()


def _inject_global_css() -> None:
    """全局样式注入：纯 <style> 注入，不引任何外部框架 / CDN。"""
    st.markdown(f"<style>{_read_asset('app.css')}</style>", unsafe_allow_html=True)


def _inject_login_css() -> None:
    """登录页专属样式（独立于全局样式，互不影响）。"""
    st.markdown(f"<style>{_read_asset('login.css')}</style>", unsafe_allow_html=True)
