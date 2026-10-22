"""
钉钉通知模块：群机器人消息（自定义机器人 + 加签）与企业通讯录同步（企业内部应用）。

两个互相独立的能力，分别配置：
1) 群机器人（.env）：
   - 默认群：DINGTALK_WEBHOOK / DINGTALK_SECRET（未识别业务标签时发这里）；
   - 业务群：DINGTALK_WEBHOOK_<标签> / DINGTALK_SECRET_<标签>（如 DINGTALK_WEBHOOK_A，
     标签大写；需求标题含对应标签时发对应群，新增业务群只改 .env 不改代码）；
   消息发到群里，@手机号（atMobiles）或 @钉钉 userid（atUserIds）实现提醒到人；
2) 企业内部应用（.env：DINGTALK_APP_KEY / DINGTALK_APP_SECRET）
   拉取企业通讯录，按「姓名 → userid/手机号」自动匹配，免去逐人手工填写。
   创建：钉钉开放平台（open.dingtalk.com）→ 应用开发 → 企业内部应用；
   权限：通讯录部门信息读权限 + 通讯录个人信息读权限（可选：企业员工手机号信息）。

与 AI 调用同一原则：任何失败只打印日志并返回 None / False，绝不阻塞主流程。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

_TIMEOUT = 10


# ============================================================
# 群机器人消息（默认群 + 业务群路由）
# ============================================================

def configured() -> bool:
    """是否已配置群机器人（未配置时调用方直接跳过发送）。"""
    return bool(os.getenv("DINGTALK_WEBHOOK", "").strip())


def group_keys() -> list[str]:
    """已配置的业务群标签列表（.env 里 DINGTALK_WEBHOOK_<标签> 的 <标签>，保持大写）。"""
    prefix = "DINGTALK_WEBHOOK_"
    return [k[len(prefix):].upper() for k in os.environ if k.startswith(prefix) and os.environ[k].strip()]


def group_configured(key: str) -> bool:
    """指定业务群是否已配置 webhook。"""
    return bool(key) and bool(os.getenv(f"DINGTALK_WEBHOOK_{(key or '').strip().upper()}", "").strip())


def any_group_configured() -> bool:
    """默认群或任一业务群是否已配置（有任一群可发即视为群通道可用）。"""
    return configured() or bool(group_keys())


def _signed_url(webhook: str, secret: str) -> str | None:
    """返回带时间戳与签名的请求地址；webhook 为空返回 None。"""
    if not webhook:
        return None
    if not secret:
        return webhook
    timestamp = str(round(time.time() * 1000))
    sign = urllib.parse.quote_plus(base64.b64encode(
        hmac.new(secret.encode("utf-8"), f"{timestamp}\n{secret}".encode("utf-8"), hashlib.sha256).digest()
    ))
    return f"{webhook}&timestamp={timestamp}&sign={sign}"


def send_text(content: str, at_mobiles: list[str] | None = None,
              at_user_ids: list[str] | None = None, group: str | None = None) -> bool:
    """向机器人所在群发文本消息；@ 指定手机号 / 钉钉 userid。

    group=None 发默认群（DINGTALK_WEBHOOK）；group="A" 发业务群（DINGTALK_WEBHOOK_A，
    加签密钥取 DINGTALK_SECRET_A，未设则不加签）。业务群未配置直接返回 False。
    失败返回 False，不抛异常（网络不通 / 密钥错误 / 限流都不影响系统主流程）。
    """
    if group:
        key = group.strip().upper()
        webhook = os.getenv(f"DINGTALK_WEBHOOK_{key}", "").strip()
        secret = os.getenv(f"DINGTALK_SECRET_{key}", "").strip()
    else:
        webhook = os.getenv("DINGTALK_WEBHOOK", "").strip()
        secret = os.getenv("DINGTALK_SECRET", "").strip()
    url = _signed_url(webhook, secret)
    if not url:
        if group:
            print(f"[dingtalk] 业务群 {group.upper()} 未配置 webhook，跳过发送")
        return False
    mobiles = [m.strip() for m in (at_mobiles or []) if m and m.strip()]
    userids = [u.strip() for u in (at_user_ids or []) if u and u.strip()]
    payload = {
        "msgtype": "text",
        "text": {"content": content + "".join(f" @{m}" for m in mobiles)},
        "at": {"atMobiles": mobiles, "atUserIds": userids, "isAtAll": False},
    }
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        if result.get("errcode") != 0:
            print(f"[dingtalk] 发送被拒绝：{result}")
            return False
        return True
    except Exception as exc:
        print(f"[dingtalk] 发送失败：{exc}")
        return False


# ============================================================
# 企业内部应用：通讯录同步（姓名 → userid / 手机号）
# ============================================================

def app_configured() -> bool:
    """是否已配置企业内部应用（未配置时「用户管理」不显示同步功能）。"""
    return bool(os.getenv("DINGTALK_APP_KEY", "").strip() and os.getenv("DINGTALK_APP_SECRET", "").strip())


_token_cache: dict = {"token": "", "expire_at": 0.0}


def _http_json(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _access_token() -> str | None:
    """获取（并缓存）企业应用 access_token，失败返回 None。"""
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expire_at"]:
        return _token_cache["token"]
    try:
        result = _http_json(
            "https://api.dingtalk.com/v1.0/oauth2/accessToken",
            {"appKey": os.getenv("DINGTALK_APP_KEY", "").strip(),
             "appSecret": os.getenv("DINGTALK_APP_SECRET", "").strip()},
        )
        token = result.get("accessToken", "")
        if not token:
            print(f"[dingtalk] 获取 access_token 失败：{result}")
            return None
        _token_cache["token"] = token
        _token_cache["expire_at"] = now + int(result.get("expireIn", 7200)) - 300
        return token
    except Exception as exc:
        print(f"[dingtalk] 获取 access_token 失败：{exc}")
        return None


def list_org_users() -> tuple[dict[str, str], dict[str, str], list[str]] | None:
    """拉取全企业通讯录，返回 ({姓名: userid}, {姓名: 手机号}, [重名姓名])，失败返回 None。

    - 只含「姓名在企业内唯一」的成员；重名者无法自动匹配，由调用方提示手工维护；
    - 手机号字段需应用额外开通「企业员工手机号信息」权限，未开通时对应值为空串。
    """
    token = _access_token() if app_configured() else None
    if not token:
        return None
    try:
        userid_to_name: dict[str, str] = {}
        userid_to_mobile: dict[str, str] = {}

        def collect_dept_users(dept_id: int):
            """分页拉取单个部门下的成员（每人可能属于多部门，按 userid 去重）"""
            cursor = 0
            while True:
                data = _http_json(
                    f"https://oapi.dingtalk.com/topapi/v2/user/list?access_token={token}",
                    {"dept_id": dept_id, "cursor": cursor, "size": 100,
                     "contain_access_limit": False, "language": "zh_CN"},
                )
                if data.get("errcode") != 0:
                    raise RuntimeError(f"拉取部门 {dept_id} 成员失败：{data.get('errmsg')}")
                result = data.get("result", {})
                for m in result.get("list", []):
                    if m.get("userid") and m.get("name"):
                        userid_to_name[m["userid"]] = m["name"]
                        userid_to_mobile[m["userid"]] = m.get("mobile") or ""
                if not result.get("has_more"):
                    break
                cursor = result.get("next_cursor") or 0
                if not cursor:
                    break

        # BFS 遍历部门树（根部门固定为 1）
        visited, queue = {1}, [1]
        while queue:
            dept_id = queue.pop(0)
            collect_dept_users(dept_id)
            sub = _http_json(
                f"https://oapi.dingtalk.com/topapi/v2/department/listsub?access_token={token}",
                {"dept_id": dept_id},
            )
            if sub.get("errcode") != 0:
                raise RuntimeError(f"拉取部门 {dept_id} 子部门失败：{sub.get('errmsg')}")
            for d in sub.get("result", []):
                did = d.get("dept_id")
                if did and did not in visited:
                    visited.add(did)
                    queue.append(did)

        # 姓名 → userid / 手机号；重名标记为歧义
        names: dict[str, list[str]] = {}
        for uid, name in userid_to_name.items():
            names.setdefault(name, []).append(uid)
        name_to_userid: dict[str, str] = {}
        name_to_mobile: dict[str, str] = {}
        ambiguous: list[str] = []
        for name, uids in names.items():
            if len(uids) == 1:
                name_to_userid[name] = uids[0]
                name_to_mobile[name] = userid_to_mobile.get(uids[0], "")
            else:
                ambiguous.append(name)
        return name_to_userid, name_to_mobile, ambiguous
    except Exception as exc:
        print(f"[dingtalk] 通讯录同步失败：{exc}")
        return None
