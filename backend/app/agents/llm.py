"""The ONLY module in ORCA that talks to a language model.

Two functions, and nothing else crosses this boundary:

    plan(prompt, schema)        -> a validated instance of `schema`
    verbalize(prompt, context)  -> a string

No graph node imports a provider SDK, knows a provider's name, or sets a
sampling parameter. That is not tidiness for its own sake -- provider quirks
are sharp and provider-specific:

  * Anthropic's Sonnet 5 REJECTS temperature/top_p/top_k with a 400.
  * Gemini's OpenAI-compatible endpoint wants temperature and ignores some
    JSON Schema keywords Pydantic emits by default.

If those details leaked into graph.py, changing provider would mean editing
every node and re-testing the agent. Here it is one file and two config lines.

WIRE FORMAT: OpenAI-compatible chat completions. Gemini exposes one, so this
module speaks a single dialect and any OpenAI-compatible provider drops in by
changing llm_base_url and llm_model.

FREE-TIER REALITY: free Gemini quota returns 429 constantly. Three defences,
because the failure mode to avoid is a hung request or an invented answer:
  1. exponential backoff with jitter, honouring Retry-After when sent;
  2. a hard per-request timeout AND a total wall-clock deadline across all
     retries, so backoff can never outlast the user;
  3. a typed LLMError carrying a sentence meant for a person, which the API
     turns into a 503. Never a fabricated answer, never a silent hang.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Providers whose key we know how to find, and the env var that carries it.
_KEY_ENV = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}


class LLMError(RuntimeError):
    """Anything that stopped us getting a usable answer.

    `user_message` is shown to a person, so it says what to do rather than
    what broke internally.
    """

    def __init__(self, user_message: str, *, detail: str = "") -> None:
        self.user_message = user_message
        self.detail = detail
        super().__init__(f"{user_message} ({detail})" if detail else user_message)


class LLMUnavailable(LLMError):
    """No credential configured. Distinct because it is a setup problem, not
    an outage, and the fix is a line in backend/.env."""


def _api_key() -> str:
    provider = settings.llm_provider
    key = {"gemini": settings.gemini_api_key,
           "openai": settings.openai_api_key}.get(provider)
    if not key:
        env = _KEY_ENV.get(provider, f"{provider.upper()}_API_KEY")
        raise LLMUnavailable(
            f"No API key for provider {provider!r}. Set {env} in backend/.env. "
            f"ORCA's deterministic layer -- ingest, risk, geofencing, the map "
            f"and every /api endpoint except /api/chat -- works without it."
        )
    return key


# ---------------------------------------------------------------------------
# JSON Schema sanitising
# ---------------------------------------------------------------------------
def _inline_defs(node: Any, defs: dict[str, Any]) -> Any:
    """Resolve $ref/$defs and drop keywords Gemini's schema validator rejects.

    Pydantic emits `$defs` + `$ref` for nested models and
    `anyOf: [{...}, {"type": "null"}]` for `X | None`. Gemini's OpenAI-compat
    layer accepts a narrower dialect, and a rejected schema is a 400 that looks
    like an auth or model error. Inlining and flattening up front avoids
    debugging that at demo time.
    """
    if isinstance(node, dict):
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            return _inline_defs(defs.get(name, {}), defs)

        # X | None -> the non-null branch, marked nullable.
        if "anyOf" in node:
            branches = [b for b in node["anyOf"]
                        if not (isinstance(b, dict) and b.get("type") == "null")]
            nullable = len(branches) != len(node["anyOf"])
            if len(branches) == 1:
                inner = _inline_defs(branches[0], defs)
                if isinstance(inner, dict):
                    inner = dict(inner)
                    if nullable:
                        inner["nullable"] = True
                    for k in ("description", "title"):
                        if k in node and k not in inner:
                            inner[k] = node[k]
                    return inner

        out = {}
        for k, v in node.items():
            if k in ("$defs", "$schema", "additionalProperties", "default",
                     "discriminator", "examples", "const"):
                continue
            out[k] = _inline_defs(v, defs)
        return out

    if isinstance(node, list):
        return [_inline_defs(v, defs) for v in node]
    return node


def schema_for(model: type[BaseModel]) -> dict[str, Any]:
    raw = model.model_json_schema()
    return _inline_defs(raw, raw.get("$defs", {}))


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------
_RETRYABLE = {408, 409, 429, 500, 502, 503, 504}


def _sleep_for(attempt: int, response: httpx.Response | None) -> float:
    """Backoff, preferring the server's own Retry-After.

    Jitter matters on a shared free-tier key: without it, several sessions
    that hit the same 429 retry in lockstep and 429 together again.
    """
    if response is not None:
        hdr = response.headers.get("retry-after")
        if hdr:
            try:
                return min(float(hdr), 30.0)
            except ValueError:
                pass
        # Google often reports the delay in the JSON body instead.
        try:
            body = response.json()
            m = re.search(r"(\d+(?:\.\d+)?)s", json.dumps(body.get("error", {})))
            if m:
                return min(float(m.group(1)), 30.0)
        except Exception:
            pass
    return min(settings.llm_backoff_base_s * (2 ** attempt), 20.0) * (
        0.6 + 0.8 * random.random()
    )


async def _bounded_sleep(delay: float, started: float) -> None:
    """Back off, but never past the deadline.

    Used by EVERY retry path. Clamping only the HTTP-status path left the
    timeout path able to sleep past the budget it had just exhausted, which
    showed up as a 5s deadline taking 7s.
    """
    left = settings.llm_deadline_s - (time.monotonic() - started)
    await asyncio.sleep(max(0.0, min(delay, left)))


async def _post(payload: dict[str, Any]) -> dict[str, Any]:
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {_api_key()}",
               "Content-Type": "application/json"}
    started = time.monotonic()
    last: str = ""

    async with httpx.AsyncClient(timeout=settings.llm_timeout_s) as client:
        for attempt in range(settings.llm_max_retries + 1):
            # The deadline is a TRUE bound on wall clock, not just a gate on
            # starting new attempts: each request gets whichever is smaller of
            # the per-request timeout and the time left in the budget.
            # Checking only before an attempt would let a full-length timeout
            # overshoot the deadline by llm_timeout_s -- with the defaults that
            # is 165s on a "120s" budget, which the caller has already given up
            # waiting for.
            remaining = settings.llm_deadline_s - (time.monotonic() - started)
            if remaining <= 0:
                raise LLMError(
                    "The language model did not respond within "
                    f"{settings.llm_deadline_s:.0f}s. The computed data is "
                    f"still available on the map and the other endpoints.",
                    detail=f"deadline exceeded after {attempt} attempts; {last}",
                )
            try:
                r = await client.post(
                    url, headers=headers, json=payload,
                    timeout=min(settings.llm_timeout_s, remaining),
                )
            except httpx.TimeoutException:
                last = "request timed out"
                if attempt >= settings.llm_max_retries:
                    raise LLMError(
                        "The language model timed out. Try again, or read the "
                        "computed values directly from the map.",
                        detail=last,
                    )
                await _bounded_sleep(_sleep_for(attempt, None), started)
                continue
            except httpx.HTTPError as e:
                raise LLMError(
                    "Could not reach the language model. Check network access.",
                    detail=str(e),
                ) from e

            if r.status_code == 200:
                return r.json()

            last = f"HTTP {r.status_code}: {r.text[:300]}"

            if r.status_code in (401, 403):
                raise LLMError(
                    "The language model rejected the API key. Check "
                    f"{_KEY_ENV.get(settings.llm_provider, 'the provider key')} "
                    f"in backend/.env.",
                    detail=last,
                )
            if r.status_code == 400:
                # Not retryable, and usually our fault (schema or parameters).
                raise LLMError(
                    "The language model rejected the request.", detail=last
                )
            if r.status_code not in _RETRYABLE:
                raise LLMError("The language model returned an error.", detail=last)

            if attempt >= settings.llm_max_retries:
                if r.status_code == 429:
                    raise LLMError(
                        "The language model is rate limited right now (free "
                        "tier quota). Wait a minute and ask again — the "
                        "computed risk and boundary data on the map is "
                        "unaffected.",
                        detail=last,
                    )
                raise LLMError("The language model is unavailable.", detail=last)

            delay = _sleep_for(attempt, r)
            log.warning("llm %s -> retrying in %.1fs (attempt %d/%d)",
                        r.status_code, delay, attempt + 1, settings.llm_max_retries)
            await _bounded_sleep(delay, started)

    raise LLMError("The language model is unavailable.", detail=last)


def _sampling() -> dict[str, Any]:
    """Provider-specific sampling parameters, quarantined here.

    Gemini's OpenAI-compatible endpoint takes temperature. Anthropic's 4.6+
    models reject it with a 400. Graph nodes must never have to know which.
    """
    if settings.llm_provider in ("gemini", "openai"):
        return {"temperature": settings.llm_temperature}
    return {}


def _usage(body: dict[str, Any]) -> dict[str, int]:
    u = body.get("usage") or {}
    return {"input_tokens": u.get("prompt_tokens", 0),
            "output_tokens": u.get("completion_tokens", 0)}


def _content(body: dict[str, Any]) -> str:
    try:
        return (body["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError) as e:
        raise LLMError("The language model returned an empty response.",
                       detail=json.dumps(body)[:300]) from e


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


def _loads(text: str) -> Any:
    """Parse JSON that may arrive wrapped in a markdown fence."""
    cleaned = _FENCE.sub("", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


# ---------------------------------------------------------------------------
# Public interface -- the only two things the rest of ORCA may call
# ---------------------------------------------------------------------------
async def plan(prompt: str, schema: type[T], *, system: str = "",
               history: list[dict[str, str]] | None = None) -> tuple[T, dict[str, int]]:
    """Structured extraction. Returns a validated `schema` instance and usage.

    Tries strict json_schema mode, falls back to json_object with the schema in
    the prompt if the provider rejects the schema, then validates with Pydantic
    regardless -- the provider's constraint is a convenience, Pydantic is the
    actual contract.
    """
    js = schema_for(schema)
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.extend(history or [])
    messages.append({"role": "user", "content": prompt})

    base = {"model": settings.llm_model, "messages": messages,
            "max_tokens": settings.llm_max_tokens, **_sampling()}

    modes: list[dict[str, Any]] = []
    if settings.llm_structured_mode == "json_schema":
        modes.append({"response_format": {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "schema": js, "strict": True},
        }})
    modes.append({"response_format": {"type": "json_object"}})

    last_error: LLMError | None = None
    for i, mode in enumerate(modes):
        payload = dict(base, **mode)
        if mode["response_format"]["type"] == "json_object":
            payload["messages"] = messages[:-1] + [{
                "role": "user",
                "content": messages[-1]["content"]
                + "\n\nReturn ONLY a JSON object matching this schema:\n"
                + json.dumps(js),
            }]
        try:
            body = await _post(payload)
        except LLMError as e:
            # A 400 on the strict path usually means the provider disliked the
            # schema; the json_object path is the point of the fallback.
            last_error = e
            if i + 1 < len(modes) and "HTTP 400" in (e.detail or ""):
                log.warning("structured mode rejected, falling back: %s", e.detail)
                continue
            raise

        text = _content(body)
        try:
            return schema.model_validate(_loads(text)), _usage(body)
        except (json.JSONDecodeError, ValidationError) as e:
            last_error = LLMError(
                "The language model returned a malformed plan.",
                detail=f"{type(e).__name__}: {str(e)[:200]}",
            )
            if i + 1 < len(modes):
                continue
            raise last_error from e

    raise last_error or LLMError("The language model returned no usable plan.")


async def verbalize(prompt: str, context: str, *, system: str = "") -> tuple[str, dict[str, int]]:
    """Free text. `context` is the verified material the answer must stay
    inside; guards.py checks afterwards that it did.
    """
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": f"{prompt}\n\n{context}".strip()})

    body = await _post({"model": settings.llm_model, "messages": messages,
                        "max_tokens": settings.llm_max_tokens, **_sampling()})
    return _content(body), _usage(body)


def provider_info() -> dict[str, Any]:
    return {"provider": settings.llm_provider, "model": settings.llm_model,
            "base_url": settings.llm_base_url,
            "key_env": _KEY_ENV.get(settings.llm_provider),
            "key_present": bool({"gemini": settings.gemini_api_key,
                                 "openai": settings.openai_api_key}
                                .get(settings.llm_provider))}
