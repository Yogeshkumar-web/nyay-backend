"""
Shared LLM client factory for all VakilSuite agents.

Provider routing via settings.AI_PROVIDER:
  "gemini"   → Google Gemini (multimodal, free tier available)
  "claude"   → Anthropic Claude (multimodal, production quality)
  "deepseek" → DeepSeek via OpenAI-compat API (text-only, cheap)
  "openai"   → OpenAI GPT (text + vision)

Import call_llm() — never instantiate clients directly in node files.

NOTE: DeepSeek does not support image inputs. When AI_PROVIDER=deepseek,
images_b64 is silently ignored and a text-only call is made.
"""
import asyncio
import base64
import logging
import re
from functools import lru_cache

logger = logging.getLogger(__name__)

# ── Backward-compat constants (some nodes import these) ───────────────────────
AGENT_MODEL = "claude-sonnet-4-6"
AGENT_TIMEOUT = 45.0


# ── Lazy cached clients ───────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def get_anthropic_client():
    import anthropic
    from app.core.config import settings

    return anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)


@lru_cache(maxsize=1)
def get_openai_client(base_url: str, api_key: str):
    """Returns an AsyncOpenAI client — works for OpenAI, DeepSeek, or any
    OpenAI-compatible endpoint (base_url differentiates them)."""
    from openai import AsyncOpenAI

    return AsyncOpenAI(api_key=api_key, base_url=base_url)


# ── Unified public call ───────────────────────────────────────────────────────


async def call_llm(
    prompt: str,
    *,
    images_b64: list[str] | None = None,
    max_tokens: int = 1024,
) -> str:
    """
    Send a prompt (+ optional page images) to the configured LLM.

    Args:
        prompt:     Full text prompt.
        images_b64: Base64-encoded PNG page images. Ignored by text-only
                    providers (deepseek). Empty strings are skipped.
        max_tokens: Maximum response tokens.

    Returns:
        str — model response text, stripped.

    Raises:
        Any network/API exception — callers must catch and apply fallback.
    """
    from app.core.config import settings

    imgs = [img for img in (images_b64 or []) if img]
    provider = (settings.AI_PROVIDER or "gemini").lower()

    if provider == "gemini":
        return await _call_gemini(prompt, imgs, max_tokens, settings)
    elif provider == "claude":
        return await _call_claude(prompt, imgs, max_tokens, settings)
    elif provider == "deepseek":
        return await _call_openai_compat(
            prompt=prompt,
            imgs=[],  # DeepSeek: no vision support — drop images
            max_tokens=max_tokens,
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
            model=settings.DEEPSEEK_MODEL,
            provider_name="deepseek",
        )
    elif provider == "openai":
        return await _call_openai_compat(
            prompt=prompt,
            imgs=imgs,
            max_tokens=max_tokens,
            api_key=settings.OPENAI_API_KEY,
            base_url="https://api.openai.com/v1",
            model=settings.OPENAI_MODEL,
            provider_name="openai",
        )
    else:
        logger.error(
            "call_llm: unknown AI_PROVIDER=%r — falling back to gemini", provider
        )
        return await _call_gemini(prompt, imgs, max_tokens, settings)


# ── Gemini implementation ─────────────────────────────────────────────────────


async def _call_gemini(
    prompt: str,
    imgs: list[str],
    max_tokens: int,
    settings,
) -> str:
    from google import genai  # lazy — optional dep
    from google.genai import types as genai_types

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    model = settings.GEMINI_MODEL or "gemini-2.5-flash"

    # Build parts: text first, then images
    parts: list = [prompt]
    for b64 in imgs:
        parts.append(
            genai_types.Part.from_bytes(
                data=base64.b64decode(b64),
                mime_type="image/png",
            )
        )

    # Build config — disable thinking so it doesn't consume output token budget
    config_kwargs: dict = {
        "max_output_tokens": max_tokens,
        "temperature": 0.3,
    }
    try:
        config_kwargs["thinking_config"] = genai_types.ThinkingConfig(thinking_budget=0)
    except AttributeError:
        pass  # older SDK version without ThinkingConfig — skip

    gen_config = genai_types.GenerateContentConfig(**config_kwargs)

    # ── Retry loop for 429 rate-limit errors ──────────────────────────────
    _MAX_RETRIES = 4
    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = await client.aio.models.generate_content(
                model=model,
                contents=parts,
                config=gen_config,
            )
        except Exception as exc:
            err = str(exc)
            is_429 = "429" in err or "RESOURCE_EXHAUSTED" in err
            if is_429 and attempt < _MAX_RETRIES:
                # Extract retryDelay suggested by the API, default 15 s
                m = re.search(r"retryDelay['\"]?\s*[=:]\s*['\"]?(\d+)", err)
                delay = int(m.group(1)) + 3 if m else 15
                logger.warning(
                    "_call_gemini: 429 rate-limit — retrying in %ds (attempt %d/%d) | model=%s",
                    delay,
                    attempt + 1,
                    _MAX_RETRIES,
                    model,
                )
                await asyncio.sleep(delay)
                continue
            raise  # non-429 or out of retries — let caller handle

        # ── Extract text from response ────────────────────────────────────
        # response.text raises ValueError when blocked by safety filters.
        try:
            text = response.text
            if text:
                return text.strip()
        except (ValueError, AttributeError):
            pass

        # Candidate-level fallback
        candidates = getattr(response, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            if not content:
                continue
            for part in getattr(content, "parts", []):
                part_text = getattr(part, "text", None)
                if part_text:
                    return part_text.strip()

        logger.warning(
            "_call_gemini: empty response from model=%s finish_reason=%s",
            model,
            candidates[0].finish_reason if candidates else "no_candidates",
        )
        return ""

    return ""  # unreachable — satisfies type checker


# ── Anthropic implementation ──────────────────────────────────────────────────


async def _call_claude(
    prompt: str,
    imgs: list[str],
    max_tokens: int,
    settings=None,
) -> str:
    client = get_anthropic_client()
    model = (settings.ANTHROPIC_MODEL if settings else None) or AGENT_MODEL

    content: list[dict] = [{"type": "text", "text": prompt}]
    content.extend(
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": img},
        }
        for img in imgs
    )

    resp = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        timeout=AGENT_TIMEOUT,
        messages=[{"role": "user", "content": content}],
    )
    return resp.content[0].text.strip()


# ── OpenAI-compatible implementation (OpenAI, DeepSeek, etc.) ─────────────────


async def _call_openai_compat(
    prompt: str,
    imgs: list[str],
    max_tokens: int,
    api_key: str,
    base_url: str,
    model: str,
    provider_name: str,
) -> str:
    """
    Calls any OpenAI-compatible API (OpenAI, DeepSeek, Together, etc.).
    Images are included only when imgs is non-empty — providers that don't
    support vision should pass imgs=[] (call_llm already handles this).
    """
    client = get_openai_client(base_url=base_url, api_key=api_key)

    # Build message content
    if imgs:
        # Vision: interleave text + image_url blocks
        content: list[dict] = [{"type": "text", "text": prompt}]
        content.extend(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{img}", "detail": "high"},
            }
            for img in imgs
        )
    else:
        # Text-only (deepseek, or openai without images)
        content = prompt  # type: ignore[assignment]

    resp = await client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        temperature=0.3,
        messages=[{"role": "user", "content": content}],
    )

    text = resp.choices[0].message.content or ""
    logger.debug(
        "_call_%s: model=%s tokens_used=%s",
        provider_name,
        model,
        getattr(resp.usage, "total_tokens", "?"),
    )
    return text.strip()
