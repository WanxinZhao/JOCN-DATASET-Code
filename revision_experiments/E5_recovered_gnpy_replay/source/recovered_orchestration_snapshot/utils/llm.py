import os
import json
import time
from pathlib import Path
from contextlib import contextmanager
from typing import Callable

import httpx


def _load_dotenv_if_present() -> None:
    candidates = [
        Path(__file__).resolve().parent / ".env",
        Path(__file__).resolve().parents[1] / ".env",
    ]

    for env_path in candidates:
        if not env_path.exists():
            continue
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@contextmanager
def _without_proxy_env():
    proxy_keys = [
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ]
    saved = {key: os.environ.get(key) for key in proxy_keys}
    try:
        for key in proxy_keys:
            os.environ.pop(key, None)
        yield
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value


def _call_openai(prompt: str) -> str:
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    timeout_seconds = _llm_timeout_seconds()
    http_client = httpx.Client(trust_env=False, timeout=timeout_seconds)
    base_url = os.getenv("OPENAI_BASE_URL") or None
    client = OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are an optical network expert."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("OpenAI returned an empty response.")
    return content


def _call_gemini(prompt: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set.")

    try:
        from google import genai
        from google.genai import types
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "LLM_PROVIDER=gemini, but the `google-genai` package is not installed."
        ) from exc

    model_name = os.getenv("COGNITIVE_KERNEL_MODEL", "gemini-1.5-pro")
    timeout_seconds = _llm_timeout_seconds()
    with _without_proxy_env():
        http_client = httpx.Client(trust_env=False, timeout=timeout_seconds)
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                apiVersion="v1beta",
                timeout=int(timeout_seconds * 1000),
                httpxClient=http_client,
            ),
        )
        response = client.models.generate_content(
            model=model_name,
            contents=[
                "You are an optical network expert. Return plain text or JSON exactly as requested.",
                prompt,
            ],
        )
    content = getattr(response, "text", None)
    if not content:
        raise RuntimeError("Gemini returned an empty response.")
    return content


def _llm_timeout_seconds() -> float:
    raw_value = os.getenv("LLM_TIMEOUT_SECONDS", "90").strip()
    try:
        return max(10.0, float(raw_value))
    except ValueError:
        return 90.0


def _llm_retry_attempts() -> int:
    raw_value = os.getenv("LLM_MAX_RETRIES", "3").strip()
    try:
        return max(1, int(raw_value))
    except ValueError:
        return 3


def _retry_delay_seconds(attempt_index: int) -> float:
    return min(8.0, float(2 ** max(0, attempt_index - 1)))


def _is_retryable_error(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)):
        return True

    message = str(exc).upper()
    retry_markers = (
        "DEADLINE_EXCEEDED",
        "TIMEOUT",
        "TIMED OUT",
        "429",
        "500",
        "502",
        "503",
        "504",
        "RESOURCE_EXHAUSTED",
        "UNAVAILABLE",
    )
    return any(marker in message for marker in retry_markers)


def _call_with_retry(provider: str, call: Callable[[str], str], prompt: str) -> str:
    attempts = _llm_retry_attempts()
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            return call(prompt)
        except Exception as exc:
            last_error = exc
            if attempt >= attempts or not _is_retryable_error(exc):
                break
            time.sleep(_retry_delay_seconds(attempt))

    raise RuntimeError(
        f"{provider} request failed after {attempts} attempt(s): {last_error}"
    ) from last_error


def _fallback_provider(primary_provider: str) -> str | None:
    configured = os.getenv("LLM_FALLBACK_PROVIDER", "").strip().lower()
    if configured and configured != primary_provider:
        return configured

    if primary_provider == "gemini" and os.getenv("OPENAI_API_KEY"):
        return "openai"
    if primary_provider == "openai" and os.getenv("GEMINI_API_KEY"):
        return "gemini"
    return None


def call_llm(prompt: str) -> str:
    _load_dotenv_if_present()
    provider = os.getenv("LLM_PROVIDER", "openai").strip().lower()

    if provider == "openai":
        try:
            return _call_with_retry("OpenAI", _call_openai, prompt)
        except Exception:
            fallback = _fallback_provider(provider)
            if fallback == "gemini":
                return _call_with_retry("Gemini", _call_gemini, prompt)
            raise
    if provider == "gemini":
        try:
            return _call_with_retry("Gemini", _call_gemini, prompt)
        except Exception:
            fallback = _fallback_provider(provider)
            if fallback == "openai":
                return _call_with_retry("OpenAI", _call_openai, prompt)
            raise

    raise RuntimeError(f"Unsupported LLM_PROVIDER: {provider}")


def parse_llm_json(response_text: str):
    text = response_text.strip()

    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "{[":
            continue
        try:
            result, _ = decoder.raw_decode(text[index:])
            return result
        except json.JSONDecodeError:
            continue

    raise ValueError(f"LLM response did not contain valid JSON: {response_text}")
