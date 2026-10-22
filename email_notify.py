"""
邮件通知模块（SMTP，标准库实现）。

设计说明：
- 只依赖标准库（smtplib + email），无新增安装依赖；
- 凭据从 .env 读取：
    SMTP_HOST      服务器地址（钉邮/阿里企业邮箱：smtp.qiye.aliyun.com；QQ：smtp.qq.com；163：smtp.163.com）
    SMTP_PORT      端口，默认 465；465 走 SSL，587 走 STARTTLS，其他端口明文
    SMTP_USER      发信账号（默认也用作发件人地址）
    SMTP_PASSWORD  密码；QQ/163 等个人邮箱必须填邮箱设置里生成的「授权码」，不是登录密码
    SMTP_FROM      可选，发件人地址，默认同 SMTP_USER
- 与钉钉通道同一原则：任何失败只打印日志并返回 False，绝不阻塞主流程。
"""
from __future__ import annotations

import os
import smtplib
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

_TIMEOUT = 10


def configured() -> bool:
    """是否已配置邮件发信（未配置时调用方直接跳过发送）。"""
    return bool(os.getenv("SMTP_HOST", "").strip() and os.getenv("SMTP_USER", "").strip())


def send_mail(to_addrs: list[str], subject: str, body: str) -> bool:
    """向一组邮箱发纯文本邮件。失败返回 False，不抛异常。"""
    host = os.getenv("SMTP_HOST", "").strip()
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    sender = os.getenv("SMTP_FROM", "").strip() or user
    try:
        port = int(os.getenv("SMTP_PORT", "465").strip() or "465")
    except ValueError:
        port = 465
    if not host or not user:
        return False
    recipients = [a.strip() for a in to_addrs if a and a.strip()]
    if not recipients:
        return False

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header("需求看板", "utf-8")), sender))
    msg["To"] = ", ".join(recipients)

    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=_TIMEOUT)
        else:
            server = smtplib.SMTP(host, port, timeout=_TIMEOUT)
        try:
            if port == 587:
                server.starttls()
            if password:
                server.login(user, password)
            server.sendmail(sender, recipients, msg.as_string())
        finally:
            server.quit()
        return True
    except Exception as exc:
        print(f"[email] 发送失败：{exc}")
        return False
