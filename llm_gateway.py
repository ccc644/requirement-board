"""
LLM Gateway - 统一的大模型调用封装
支持 OpenAI 兼容协议（通义千问 / DeepSeek / 智谱 GLM / OpenAI 等）
"""
from __future__ import annotations
import os
import json
import time
from typing import Optional
from dataclasses import dataclass


# 预置服务提供商
PROVIDERS = {
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
        "website": "https://platform.deepseek.com",
    },
    "dashscope": {
        "name": "通义千问（阿里云百炼）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "api_key_env": "DASHSCOPE_API_KEY",
        "website": "https://bailian.console.aliyun.com",
    },
    "zhipu": {
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4-flash",
        "api_key_env": "ZHIPU_API_KEY",
        "website": "https://open.bigmodel.cn",
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY",
        "website": "https://platform.openai.com",
    },
}


@dataclass
class LLMResponse:
    content: str
    success: bool
    error: Optional[str] = None
    latency_ms: int = 0
    model: str = ""
    provider: str = ""


def get_client(provider: str, api_key: str):
    """创建 OpenAI 兼容客户端"""
    from openai import OpenAI
    config = PROVIDERS[provider]
    return OpenAI(api_key=api_key, base_url=config["base_url"])


def chat(
    system_prompt: str,
    user_prompt: str,
    provider: str = "deepseek",
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.2,
    timeout: int = 30,
    max_retries: int = 1,
    messages: Optional[list] = None,
) -> LLMResponse:
    """
    调用 LLM 完成一次对话

    Args:
        system_prompt: 系统提示词
        user_prompt: 用户输入
        provider: 服务商
        api_key: API Key（不传则从环境变量读取）
        model: 模型名（不传则用服务商默认）
        temperature: 温度
        timeout: 超时秒数
        max_retries: 失败重试次数
        messages: 完整多轮消息列表（[{"role": "user"|"assistant", "content": ...}]）。
                  传入时忽略 user_prompt，system_prompt 仍作为首条 system 消息前置——
                  这样既有调用点（单轮）与新调用点（多轮）共用一个实现。
                  参考客诉系统同名底座 llm_chat(messages, ...) 的设计（ai_engine.py:500）。

    Returns:
        LLMResponse
    """
    if provider not in PROVIDERS:
        return LLMResponse(
            content="",
            success=False,
            error=f"未知服务商：{provider}，可选：{list(PROVIDERS.keys())}",
        )

    config = PROVIDERS[provider]
    if api_key is None:
        api_key = os.environ.get(config["api_key_env"], "")
    if not api_key:
        return LLMResponse(
            content="",
            success=False,
            error=f"未配置 API Key。请前往 {config['website']} 申请，并设置环境变量 {config['api_key_env']}",
            provider=provider,
        )

    if model is None:
        model = config["default_model"]

    if messages is not None:
        msg_list = ([{"role": "system", "content": system_prompt}] if system_prompt else []) + list(messages)
    else:
        msg_list = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    start = time.time()
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            client = get_client(provider, api_key)
            resp = client.chat.completions.create(
                model=model,
                messages=msg_list,
                temperature=temperature,
                timeout=timeout,
            )
            content = resp.choices[0].message.content or ""
            return LLMResponse(
                content=content,
                success=True,
                latency_ms=int((time.time() - start) * 1000),
                model=model,
                provider=provider,
            )
        except Exception as e:
            last_error = str(e)
            if attempt < max_retries:
                time.sleep(0.8)
            else:
                break

    return LLMResponse(
        content="",
        success=False,
        error=f"LLM 调用失败：{last_error}",
        latency_ms=int((time.time() - start) * 1000),
        model=model,
        provider=provider,
    )


def chat_stream(
    system_prompt: str,
    messages: list,
    provider: str = "deepseek",
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.4,
    timeout: int = 60,
):
    """流式多轮对话（A5C 智能体用）：yield 增量文本。

    与 chat() 的分工：chat() 返回完整 LLMResponse（单轮/JSON 场景）；
    chat_stream() 供打字机式输出，前置校验失败或调用异常直接 raise
    RuntimeError（文本带原因），由调用方决定降级路径。
    """
    if provider not in PROVIDERS:
        raise RuntimeError(f"未知服务商：{provider}，可选：{list(PROVIDERS.keys())}")
    config = PROVIDERS[provider]
    if api_key is None:
        api_key = os.environ.get(config["api_key_env"], "")
    if not api_key:
        raise RuntimeError(f"未配置 API Key（{config['api_key_env']}）")
    if model is None:
        model = config["default_model"]

    msg_list = ([{"role": "system", "content": system_prompt}] if system_prompt else []) + list(messages)
    try:
        client = get_client(provider, api_key)
        stream = client.chat.completions.create(
            model=model,
            messages=msg_list,
            temperature=temperature,
            timeout=timeout,
            stream=True,
        )
        for chunk in stream:
            if not getattr(chunk, "choices", None):
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
    except Exception as e:  # noqa: BLE001 —— 统一转为 RuntimeError，调用方降级
        raise RuntimeError(f"LLM 流式调用失败：{e}") from e


def parse_json_response(content: str) -> tuple[Optional[dict | list], Optional[str]]:
    """
    解析 LLM 返回的 JSON（容忍前后多余文字与代码块）
    """
    if not content:
        return None, "空响应"

    text = content.strip()
    # 去掉 ```json ... ``` 包裹
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        # 尝试截取第一段 JSON
        for start_char, end_char in [("{", "}"), ("[", "]")]:
            s = text.find(start_char)
            e_pos = text.rfind(end_char)
            if s != -1 and e_pos != -1 and e_pos > s:
                try:
                    return json.loads(text[s:e_pos + 1]), None
                except json.JSONDecodeError:
                    continue
        return None, f"JSON 解析失败：{e}；原始响应：{content[:200]}"
