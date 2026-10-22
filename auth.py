"""
鉴权模块：密码哈希与校验（PBKDF2，纯标准库，无第三方依赖）+ 登录令牌表。

设计说明：
- 未来接入企业 SSO（钉钉/企业微信/OAuth2/AD）时，只需替换「登录校验」这一处，
  用户表（users）与界面逻辑无需改动。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

_ITERATIONS = 100_000


# ============================================================
# 登录令牌表（浏览器刷新不掉线的服务端状态）
# ============================================================
# 为什么放本模块：Streamlit 每次 rerun 都把主脚本从头到尾重新执行一遍，
# 主脚本里的模块级状态每轮归零（token 表放 app.py 会在「登录那轮」写入、
# 「刷新那轮」被清空，还原永远失败）；本模块经 import 只在进程内执行一次
# （sys.modules 缓存），表自然跨 rerun 存活。服务重启即清空（用户重登一次）。
_TOKENS: dict[str, tuple[str, float]] = {}
_TOKEN_TTL = 30 * 86400  # 30 天


def issue_token(user_id: str) -> str:
    """签发登录令牌：随机 32 字节，绑定 user_id，TTL 内有效。"""
    tok = secrets.token_urlsafe(32)
    _TOKENS[tok] = (user_id, time.time() + _TOKEN_TTL)
    return tok


def consume_token(tok: str | None) -> str | None:
    """校验令牌：有效且未过期返回 user_id；无效/过期返回 None（过期即清理）。"""
    if not tok:
        return None
    rec = _TOKENS.get(tok)
    if not rec:
        return None
    uid, exp = rec
    if time.time() > exp:
        _TOKENS.pop(tok, None)
        return None
    return uid


def revoke_token(tok: str | None) -> None:
    """立即作废令牌（退出登录用）。"""
    if tok:
        _TOKENS.pop(tok, None)


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """返回 (password_hash, salt)。salt 为空时自动生成。"""
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        _ITERATIONS,
    )
    return dk.hex(), salt


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    """校验密码是否匹配，使用常数时间比较。"""
    dk, _ = hash_password(password, salt)
    return hmac.compare_digest(dk, expected_hash)
