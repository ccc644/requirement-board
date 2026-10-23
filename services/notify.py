"""
services/notify.py — 从 app.py 拆分生成（函数体逐字保留）。
"""
from __future__ import annotations

# ---- 第三方 / 标准库 ----
import dingtalk
import email_notify

# ---- 项目内部 ----
from services.projects import _biz_groups_from_req
from state import USER_ID_TO_NAME, get_users



def _notify(user_ids: list[str], text: str, req: dict | None = None) -> dict:
    """双通道提醒：群消息按需求标题路由到对应业务群，邮件则私发给每位填了邮箱的相关人。

    优先级门控：传入 req 时仅 P0/P1/P2 推送；P3 与未填（未知/空）整套流程静默不发。
    通道规则（并行，可叠加）：
    - 群消息路由：需求标题含已配置的业务标签（如 A/B/C）→ 发对应业务群（命中几个发几个）；
      未命中 → 发默认群（DINGTALK_WEBHOOK）。无 req 的提醒（问卷/评价等）始终走默认群；
    - 在提醒群中（档案勾选「在钉钉提醒群中」且填了手机号/钉钉 userid）→ 群消息 @ 到人
      （@ 仅对该群成员高亮；不在路由群内的人靠邮件兜底）；
    - 填了邮箱（且 .env 配置了 SMTP）→ 邮件私发，与是否在群无关——在群里被 @ 的人同样收邮件；
    - 群消息只点名群内可达者——不在群的人不出现在群消息里；全部接收人都不可达群渠道时，
      干脆不发群消息，避免打扰群内无关用户；
    - 两条路都走不通的（不在群 + 无邮箱 / SMTP 未配置）：打印日志提示管理员补联系方式；
    - 任何发送失败只打印日志，不阻塞主流程。

    返回送达回执（三期新增，既有调用方可忽略）：{"delivered": [...], "dead": [...]}。
    delivered=已交由钉钉群@或邮件通道受理的 user_id（双通道可达者只记一次）；dead=两条通道都不可达者。
    改进闭环据此决定是否写 notified_at——外发失败不得把改进项标记为「已回告」，
    站内提醒条另由 ack 独立控制（见 db.pending_improvement_notice）。
    """
    # 优先级门控：P3 / 未填优先级的需求，所有场景都不提醒
    if req is not None:
        pr = (req.get("priority") or "").strip() or "未知"
        if pr not in ("P0", "P1", "P2"):
            return
    ids = list(dict.fromkeys(uid for uid in user_ids if uid))
    by_id = {u["id"]: u for u in get_users()}
    group_ok = dingtalk.any_group_configured()
    email_ok = email_notify.configured()

    group_ids, email_ids, dead_ids = [], [], []
    for uid in ids:
        u = by_id.get(uid)
        if not u:
            continue
        in_group = u.get("notify_by_group", True) and (u.get("phone") or u.get("dingtalk_userid")) and group_ok
        by_email = bool(u.get("email")) and email_ok
        if in_group:
            group_ids.append(uid)
        if by_email:
            email_ids.append(uid)
        if not in_group and not by_email:
            dead_ids.append(uid)

    if group_ids:
        phones = [by_id[i]["phone"] for i in group_ids if by_id[i].get("phone")]
        userids = [by_id[i]["dingtalk_userid"] for i in group_ids if by_id[i].get("dingtalk_userid")]
        names = "、".join(USER_ID_TO_NAME.get(i, i) for i in group_ids)
        # 业务群路由：标题命中已配置业务标签 → 逐个发对应业务群；未命中 → 默认群兜底
        for key in _biz_groups_from_req(req) or [None]:
            dingtalk.send_text(f"【需求看板】{names}：{text}", phones, userids, group=key)
    if email_ids:
        emails = [by_id[i]["email"] for i in email_ids]
        names = "、".join(USER_ID_TO_NAME.get(i, i) for i in email_ids)
        email_notify.send_mail(emails, "需求看板提醒", f"【需求看板】{names}：\n{text}")
    if dead_ids:
        names = "、".join(USER_ID_TO_NAME.get(i, i) for i in dead_ids)
        print(f"[notify] 无法送达（不在提醒群且无邮箱或 SMTP 未配置）：{names} — {text}")
    # 送达回执：调用方（改进闭环回告/催办）据此判断是否可标记「已回告」
    return {"delivered": list(dict.fromkeys(group_ids + email_ids)), "dead": dead_ids}
