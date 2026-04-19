"""
Shared LLM client — OpenAI-compatible, Qwen3 internal BNP.
Handles SSL and proxy from .env configuration.
"""

import os
import httpx
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()


def build_client() -> OpenAI:
    """Build and return a configured OpenAI-compatible client."""
    base_url    = os.getenv("LLM_BASE_URL")
    api_key     = os.getenv("LLM_API_KEY", "no-key")
    http_proxy  = os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
    https_proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
    ssl_verify  = os.getenv("SSL_VERIFY", "true").lower() != "false"

    if not base_url:
        raise ValueError("LLM_BASE_URL is not set in .env")

    proxy_url = https_proxy or http_proxy
    if proxy_url:
        transport = httpx.HTTPTransport(proxy=proxy_url, verify=ssl_verify)
        http_client = httpx.Client(transport=transport, verify=ssl_verify)
    else:
        http_client = httpx.Client(verify=ssl_verify)

    return OpenAI(base_url=base_url, api_key=api_key, http_client=http_client)


def get_model() -> str:
    return os.getenv("LLM_MODEL", "qwen3")
