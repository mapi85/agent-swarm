"""Couche LLM : providers typés (anthropic / openai compatibles), détection des
limites de débit, bascule de secours, listing des modèles.

La conversation canonique est toujours au format « blocs Anthropic » :
- {"type":"text","text":...}
- {"type":"thinking","thinking":...,"signature":...}
- {"type":"tool_use","id":...,"name":...,"input":{...}}
Les tours assistant produits par un provider Anthropic restent des objets SDK
(préserve thinking/compaction) ; ceux produits par un provider OpenAI sont des
dicts. Les deux providers savent relire les deux représentations.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import anthropic
import httpx
from anthropic import AsyncAnthropic

from .crypto import decrypt_secret
from .models import Provider

ANTHROPIC_DEFAULT_BASE = "https://api.anthropic.com"


# --- lecture générique d'un bloc (objet SDK ou dict) ---

def block_type(b):
    return b.get("type") if isinstance(b, dict) else getattr(b, "type", None)


def block_get(b, key, default=None):
    if isinstance(b, dict):
        return b.get(key, default)
    return getattr(b, key, default)


class LLMResponse:
    def __init__(self, blocks, stop_reason, input_tokens, output_tokens):
        self.blocks = blocks
        self.stop_reason = stop_reason
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


_ANTHROPIC_TRANSIENT = (
    anthropic.RateLimitError,
    anthropic.APIConnectionError,
    anthropic.InternalServerError,
    anthropic.APITimeoutError,
)


def is_rate_limit(exc) -> bool:
    """True si l'erreur est une limite de débit/quota (429)."""
    if isinstance(exc, anthropic.RateLimitError):
        return True
    if isinstance(exc, anthropic.APIStatusError) and exc.status_code == 429:
        return True
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        return True
    return False


def resume_time(exc) -> str | None:
    """Heure de reprise annoncée par un provider limité (ISO UTC), si détectable :
    en-tête Retry-After (secondes ou date HTTP), en-têtes anthropic-ratelimit-*-reset,
    ou date ISO présente dans le message d'erreur."""
    now_utc = datetime.now(timezone.utc)
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None) or {}
    ra = headers.get("retry-after")
    if ra:
        try:
            return (now_utc + timedelta(seconds=float(ra))).isoformat(timespec="seconds")
        except ValueError:
            try:
                return parsedate_to_datetime(ra).astimezone(timezone.utc).isoformat(timespec="seconds")
            except (TypeError, ValueError):
                pass
    resets = []
    for k in headers:
        if k.lower().startswith("anthropic-ratelimit") and k.lower().endswith("-reset"):
            try:
                resets.append(datetime.fromisoformat(headers[k].replace("Z", "+00:00")))
            except ValueError:
                pass
    if resets:
        return max(resets).astimezone(timezone.utc).isoformat(timespec="seconds")
    m = re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?", str(exc))
    if m:
        try:
            dt = datetime.fromisoformat(m.group(0).replace(" ", "T").replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt > now_utc:
                return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
        except ValueError:
            pass
    return None


def fallback_model(agent_model: str, provider: Provider) -> str:
    """Modèle à utiliser sur un provider de secours : celui de l'agent s'il y est
    proposé, sinon le modèle par défaut du provider."""
    models = provider.models or []
    if models and agent_model not in models:
        return provider.default_model or agent_model
    return agent_model


class AnthropicProvider:
    """Provider Messages API d'Anthropic — ou tout endpoint compatible."""

    def __init__(self, name, base_url, api_key, native_features=True):
        self.name = name
        self.native = native_features
        kwargs = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self.client = AsyncAnthropic(**kwargs)

    @staticmethod
    def _supports_advanced(model: str) -> bool:
        # thinking adaptatif / effort / compaction : Opus 4.x, Sonnet 4.6, Fable — pas Haiku.
        return any(k in model for k in ("opus", "sonnet", "fable"))

    async def create(self, *, model, system, messages, tools, max_tokens, effort) -> LLMResponse:
        if self.native and self._supports_advanced(model):
            resp = await self.client.beta.messages.create(
                model=model,
                max_tokens=max_tokens,
                betas=["compact-2026-01-12"],
                context_management={"edits": [{"type": "compact_20260112"}]},
                thinking={"type": "adaptive"},
                output_config={"effort": effort},
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                tools=tools,
                messages=messages,
            )
        elif self.native:
            resp = await self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                tools=tools,
                messages=messages,
            )
        else:
            # Endpoint compatible sans fonctionnalités avancées : outils personnalisés seulement.
            custom_tools = [t for t in tools if "input_schema" in t]
            resp = await self.client.messages.create(
                model=model, max_tokens=max_tokens, system=system,
                tools=custom_tools, messages=messages,
            )
        u = resp.usage
        intok = u.input_tokens + (getattr(u, "cache_read_input_tokens", 0) or 0) \
            + (getattr(u, "cache_creation_input_tokens", 0) or 0)
        return LLMResponse(list(resp.content), resp.stop_reason, intok, u.output_tokens)

    def is_transient(self, exc) -> bool:
        if isinstance(exc, _ANTHROPIC_TRANSIENT):
            return True
        if isinstance(exc, anthropic.APIStatusError):
            return exc.status_code in (408, 409, 425, 429, 500, 502, 503, 504, 529)
        return False


def _to_openai_messages(system, messages):
    out = [{"role": "system", "content": system}]
    for m in messages:
        role = m["role"]
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if role == "assistant":
            text_parts, tool_calls = [], []
            for b in content:
                t = block_type(b)
                if t == "text":
                    text_parts.append(block_get(b, "text", ""))
                elif t == "tool_use":
                    tool_calls.append({
                        "id": block_get(b, "id"),
                        "type": "function",
                        "function": {
                            "name": block_get(b, "name"),
                            "arguments": json.dumps(block_get(b, "input", {}) or {}, ensure_ascii=False),
                        },
                    })
            msg = {"role": "assistant", "content": "\n".join(p for p in text_parts if p)}
            if tool_calls:
                msg["tool_calls"] = tool_calls
                if not msg["content"]:
                    msg["content"] = None
            out.append(msg)
        else:  # user
            text_parts = []
            for b in content:
                t = block_type(b)
                if t == "tool_result":
                    c = block_get(b, "content", "")
                    if isinstance(c, list):
                        c = "\n".join(block_get(x, "text", "") for x in c if block_type(x) == "text")
                    out.append({"role": "tool", "tool_call_id": block_get(b, "tool_use_id"),
                                "content": str(c)})
                elif t == "text":
                    text_parts.append(block_get(b, "text", ""))
            if text_parts:
                out.append({"role": "user", "content": "\n".join(text_parts)})
    return out


def _to_openai_tools(tools):
    return [
        {"type": "function", "function": {
            "name": t["name"], "description": t.get("description", ""),
            "parameters": t["input_schema"],
        }}
        for t in tools if "input_schema" in t
    ]


class OpenAIProvider:
    """Provider OpenAI Chat Completions — ou tout endpoint compatible."""

    def __init__(self, name, base_url, api_key, model):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    async def create(self, *, model, system, messages, tools, max_tokens, effort) -> LLMResponse:
        payload = {
            "model": model or self.model,
            "messages": _to_openai_messages(system, messages),
            "max_tokens": max_tokens,
        }
        oai_tools = _to_openai_tools(tools)
        if oai_tools:
            payload["tools"] = oai_tools
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=20.0)) as c:
            r = await c.post(
                f"{self.base_url}/chat/completions", json=payload,
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
            )
            r.raise_for_status()
            data = r.json()

        choice = data["choices"][0]
        msg = choice.get("message", {})
        blocks = []
        if msg.get("content"):
            blocks.append({"type": "text", "text": msg["content"]})
        for tc in msg.get("tool_calls") or []:
            args = tc.get("function", {}).get("arguments", "{}")
            try:
                parsed = json.loads(args) if isinstance(args, str) else (args or {})
            except (json.JSONDecodeError, TypeError):
                parsed = {}
            blocks.append({"type": "tool_use", "id": tc.get("id") or f"call_{len(blocks)}",
                           "name": tc.get("function", {}).get("name"), "input": parsed})
        fr = choice.get("finish_reason")
        stop = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens"}.get(fr, "end_turn")
        if any(block_type(b) == "tool_use" for b in blocks):
            stop = "tool_use"
        usage = data.get("usage") or {}
        return LLMResponse(blocks, stop, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))

    def is_transient(self, exc) -> bool:
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in (408, 409, 425, 429, 500, 502, 503, 504, 529)
        return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout,
                                httpx.ReadTimeout, httpx.TimeoutException, httpx.RemoteProtocolError))


# --- fabrique ---

def provider_api_key(provider: Provider) -> str:
    return decrypt_secret(provider.api_key_enc) if provider.api_key_enc else ""


def build_provider(provider: Provider):
    """Instancie un client LLM à partir d'une ligne de la table providers."""
    api_key = provider_api_key(provider)
    if provider.ptype == "openai":
        return OpenAIProvider(provider.name, provider.base_url, api_key, provider.default_model)
    return AnthropicProvider(provider.name, provider.base_url, api_key, provider.native_features)


# --- listing dynamique des modèles ---

async def fetch_models(ptype: str, base_url: str, api_key: str) -> list[str]:
    """Interroge l'endpoint /models du provider et renvoie les ids de modèles."""
    if ptype == "openai":
        url = (base_url.rstrip("/") or "https://api.openai.com/v1") + "/models"
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as c:
            r = await c.get(url, headers={"Authorization": f"Bearer {api_key}"})
            r.raise_for_status()
            data = r.json()
        return sorted(m.get("id") for m in (data.get("data") or []) if m.get("id"))
    kwargs = {}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    client = AsyncAnthropic(**kwargs)
    page = await client.models.list(limit=1000)
    return [m.id for m in page.data if getattr(m, "id", None)]
