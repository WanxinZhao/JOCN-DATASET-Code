import os
import sys
from pathlib import Path
from contextlib import contextmanager

import httpx


def _load_dotenv_if_present() -> None:
    candidates = [
        Path(__file__).resolve().parent / ".env",
        Path(__file__).resolve().parent / "utils" / ".env",
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


def _check_openai() -> None:
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    http_client = httpx.Client(trust_env=False, timeout=60.0)
    client = OpenAI(api_key=api_key, http_client=http_client)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Say hello"}],
        max_tokens=10,
    )
    print("OpenAI key works.")
    print("Response:", response.choices[0].message.content)


def _check_gemini() -> None:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set.")

    try:
        from google import genai
        from google.genai import types
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Gemini provider is configured, but the `google-genai` package is not installed."
        ) from exc

    model_name = os.getenv("COGNITIVE_KERNEL_MODEL", "gemini-1.5-pro")
    with _without_proxy_env():
        http_client = httpx.Client(trust_env=False, timeout=60.0)
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                apiVersion="v1beta",
                timeout=60_000,
                httpxClient=http_client,
            ),
        )
        response = client.models.generate_content(model=model_name, contents="Say hello")
    print("Gemini key works.")
    print("Response:", getattr(response, "text", response))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    _load_dotenv_if_present()
    provider = os.getenv("LLM_PROVIDER", "openai").strip().lower()
    print(f"LLM_PROVIDER={provider}")

    if provider == "openai":
        _check_openai()
        return
    if provider == "gemini":
        _check_gemini()
        return

    raise RuntimeError(f"Unsupported LLM_PROVIDER: {provider}")


if __name__ == "__main__":
    main()
