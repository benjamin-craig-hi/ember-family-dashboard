"""LLM routing for Ember — local Ollama, Ollama cloud, or BYOK cloud.

Stdlib-only for the BYOK path (urllib); the `ollama` client for local/cloud.
A small provider abstraction (local / cloud / custom) keeps the three
backends behind one `chat()` entry point, with no new dependencies.

Provider types (settings key ``llm_provider``):
  - "local"        — local Ollama (http://host:11434), no key
  - "ollama_cloud" — ollama.com cloud (https://ollama.com), Bearer API key
  - "cloud"        — BYOK: OpenAI-compatible (OpenAI/OpenRouter/Mistral/Groq/
                     custom) with full tool-calling, or native Anthropic/Gemini
                     (chat only).

The API key is never logged and never returned by the settings API.
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error

PROVIDER_LOCAL = "local"
PROVIDER_OLLAMA_CLOUD = "ollama_cloud"
PROVIDER_CLOUD = "cloud"
VALID_PROVIDERS = (PROVIDER_LOCAL, PROVIDER_OLLAMA_CLOUD, PROVIDER_CLOUD)

DEFAULT_LOCAL_MODEL = "qwen3:8b-q8_0"
DEFAULT_OLLAMA_CLOUD_MODEL = "gemma4:31b"
OLLAMA_CLOUD_HOST = "https://ollama.com"

REQUEST_TIMEOUT = 120

# BYOK cloud providers. "format" tells us which wire protocol to speak.
# OpenAI-compatible providers get full tool-calling; Anthropic/Gemini are
# chat-only (for full tool-calling on those, route through OpenRouter).
CLOUD_PROVIDERS = {
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "format": "openai",
        "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"],
    },
    "openrouter": {
        "label": "OpenRouter (multi-provider)",
        "base_url": "https://openrouter.ai/api/v1",
        "format": "openai",
        "models": ["anthropic/claude-sonnet-4", "openai/gpt-4o-mini", "google/gemini-2.0-flash-001"],
    },
    "mistral": {
        "label": "Mistral AI",
        "base_url": "https://api.mistral.ai/v1",
        "format": "openai",
        "models": ["mistral-large-latest", "mistral-small-latest"],
    },
    "groq": {
        "label": "Groq (fast inference)",
        "base_url": "https://api.groq.com/openai/v1",
        "format": "openai",
        "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
    },
    "anthropic": {
        "label": "Anthropic (Claude)",
        "base_url": "https://api.anthropic.com/v1",
        "format": "anthropic",
        "models": ["claude-sonnet-4-20250514", "claude-haiku-4-20250414"],
    },
    "gemini": {
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "format": "gemini",
        "models": ["gemini-2.0-flash", "gemini-1.5-pro"],
    },
}


class ToolCall:
    """A normalized tool call, provider-agnostic."""

    def __init__(self, id, name, arguments):
        self.id = id or ""
        self.name = name
        self.arguments = arguments if isinstance(arguments, dict) else {}


class ChatResult:
    def __init__(self, content, tool_calls=None, model=""):
        self.content = content or ""
        self.tool_calls = tool_calls or []
        self.model = model


def _resolve_key(settings):
    return (
        (settings.get("llm_api_key") or "").strip()
        or os.environ.get("OLLAMA_API_KEY", "").strip()
        or os.environ.get("LLM_API_KEY", "").strip()
    )


def _default_model(provider):
    if provider == PROVIDER_OLLAMA_CLOUD:
        return DEFAULT_OLLAMA_CLOUD_MODEL
    return DEFAULT_LOCAL_MODEL


def _ollama_client(provider, key):
    from ollama import Client
    if provider == PROVIDER_OLLAMA_CLOUD:
        headers = {"Authorization": "Bearer " + key} if key else None
        return Client(host=OLLAMA_CLOUD_HOST, headers=headers)
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    return Client(host=host)


def _chat_ollama(messages, model, provider, key, tools, options):
    client = _ollama_client(provider, key)
    kwargs = {"model": model, "messages": messages}
    if tools:
        kwargs["tools"] = tools
    # num_ctx is a local KV-cache optimization; cloud models manage their own.
    if options and provider == PROVIDER_LOCAL:
        kwargs["options"] = options
    resp = client.chat(**kwargs)
    msg = resp.message
    tool_calls = []
    for tc in getattr(msg, "tool_calls", None) or []:
        fn = tc.function
        args = fn.arguments
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        tool_calls.append(ToolCall(getattr(tc, "id", ""), fn.name, args))
    return ChatResult(msg.content, tool_calls, getattr(resp, "model", "") or model)


def _chat_openai(messages, model, base_url, key, tools):
    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions" if url.endswith("/v1") else "/v1/chat/completions"
    payload = {"model": model, "messages": messages}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
        body = json.loads(r.read().decode())
    choice = (body.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content") or ""
    tool_calls = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        args = fn.get("arguments") or "{}"
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        tool_calls.append(ToolCall(tc.get("id", ""), fn.get("name", ""), args))
    return ChatResult(content, tool_calls, body.get("model", model))


def _chat_anthropic(messages, model, key):
    system = ""
    anthropic_msgs = []
    for m in messages:
        role = m.get("role")
        content = m.get("content") or ""
        if role == "system":
            system += content + "\n"
        elif role in ("user", "assistant"):
            anthropic_msgs.append({"role": role, "content": content})
    payload = {"model": model, "max_tokens": 1024, "messages": anthropic_msgs}
    if system.strip():
        payload["system"] = system.strip()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode(),
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
        body = json.loads(r.read().decode())
    content = "".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text")
    return ChatResult(content, [], body.get("model", model))


def _chat_gemini(messages, model, key):
    contents = []
    system = ""
    for m in messages:
        role = m.get("role")
        text = m.get("content") or ""
        if role == "system":
            system += text + "\n"
        elif role == "user":
            contents.append({"role": "user", "parts": [{"text": text}]})
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": text}]})
    payload = {"contents": contents}
    if system.strip():
        payload["systemInstruction"] = {"parts": [{"text": system.strip()}]}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
        body = json.loads(r.read().decode())
    parts = (body.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    content = "".join(p.get("text", "") for p in parts)
    return ChatResult(content, [], model)


def chat(messages, *, settings, tools=None, options=None):
    """Route a chat request to the configured provider.

    Returns a ChatResult with .content and .tool_calls (list of ToolCall).
    """
    provider = (settings.get("llm_provider") or PROVIDER_LOCAL).strip()
    if provider not in VALID_PROVIDERS:
        provider = PROVIDER_LOCAL
    model = (settings.get("llm_model") or "").strip() or _default_model(provider)
    key = _resolve_key(settings)

    if provider in (PROVIDER_LOCAL, PROVIDER_OLLAMA_CLOUD):
        return _chat_ollama(messages, model, provider, key, tools, options)

    # BYOK cloud
    cloud_provider = (settings.get("llm_cloud_provider") or "openai").strip()
    entry = CLOUD_PROVIDERS.get(cloud_provider) or CLOUD_PROVIDERS["openai"]
    base_url = (settings.get("llm_base_url") or "").strip() or entry["base_url"]
    fmt = entry["format"]
    if fmt == "anthropic":
        return _chat_anthropic(messages, model, key)
    if fmt == "gemini":
        return _chat_gemini(messages, model, key)
    return _chat_openai(messages, model, base_url, key, tools)


def assistant_message(content, tool_calls, provider):
    """Build the assistant-turn message dict for the given provider."""
    if provider in (PROVIDER_LOCAL, PROVIDER_OLLAMA_CLOUD):
        return {
            "role": "assistant",
            "content": content or "",
            "tool_calls": [
                {"function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                for tc in tool_calls
            ],
        }
    return {
        "role": "assistant",
        "content": content or None,
        "tool_calls": [
            {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
            for tc in tool_calls
        ],
    }


def tool_result_message(tool_call, result, provider):
    """Build the tool-result message dict for the given provider."""
    if provider in (PROVIDER_LOCAL, PROVIDER_OLLAMA_CLOUD):
        return {"role": "tool", "content": str(result), "tool_name": tool_call.name}
    return {"role": "tool", "tool_call_id": tool_call.id, "content": str(result)}
