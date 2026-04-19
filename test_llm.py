"""
LLM connection test — validates Qwen3 API connectivity.

Usage:
  python test_llm.py

Setup:
  Copy .env.example to .env and fill in:
    LLM_BASE_URL=https://your-internal-qwen3-endpoint/v1
    LLM_API_KEY=your-api-key
    LLM_MODEL=qwen3

  Optional (corporate proxy):
    HTTP_PROXY=http://proxy-host:port
    HTTPS_PROXY=http://proxy-host:port
    SSL_VERIFY=false   # set to false if self-signed cert
"""

import os
import httpx
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

base_url   = os.getenv("LLM_BASE_URL")
api_key    = os.getenv("LLM_API_KEY", "no-key")
model      = os.getenv("LLM_MODEL", "qwen3")
http_proxy = os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
https_proxy= os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
ssl_verify = os.getenv("SSL_VERIFY", "true").lower() != "false"

if not base_url:
    print("ERROR: LLM_BASE_URL is not set in .env")
    exit(1)

print(f"Connecting to : {base_url}")
print(f"Model         : {model}")
print(f"Proxy         : {https_proxy or http_proxy or 'none'}")
print(f"SSL verify    : {ssl_verify}")
print()

# Build httpx client with proxy + SSL settings
# httpx >= 0.27 uses mounts instead of proxies
proxy_url = https_proxy or http_proxy
if proxy_url:
    transport = httpx.HTTPTransport(proxy=proxy_url, verify=ssl_verify)
    http_client = httpx.Client(transport=transport, verify=ssl_verify)
else:
    http_client = httpx.Client(verify=ssl_verify)

client = OpenAI(
    base_url=base_url,
    api_key=api_key,
    http_client=http_client,
)

try:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "user", "content": "Reply with exactly: CONNECTION OK"}
        ],
        max_tokens=20,
        temperature=0,
    )
    reply = response.choices[0].message.content.strip()
    print(f"Response : {reply}")
    print()
    print("LLM connection OK")

except Exception as e:
    print(f"CONNECTION FAILED")
    print(f"  Error type : {type(e).__name__}")
    print(f"  Details    : {e}")
    print()
    print("Checklist:")
    print("  [ ] LLM_BASE_URL correct in .env ?")
    print("  [ ] Reachable from this machine ? (try curl or browser)")
    print("  [ ] Proxy needed ? → add HTTP_PROXY / HTTPS_PROXY in .env")
    print("  [ ] Self-signed cert ? → add SSL_VERIFY=false in .env")
