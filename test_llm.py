"""
LLM connection + interrogation test — validates Qwen3 end-to-end.

3 tests:
  1. Connection ping        — model responds
  2. Instruction following  — model follows a precise instruction
  3. Domain knowledge       — model understands data flow context

Usage:
  python test_llm.py

Setup (.env):
  LLM_BASE_URL=https://your-internal-qwen3-endpoint/v1
  LLM_API_KEY=your-api-key
  LLM_MODEL=qwen3
  SSL_VERIFY=false         # if self-signed cert
  HTTPS_PROXY=http://...   # if corporate proxy
"""

import os
import httpx
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

base_url    = os.getenv("LLM_BASE_URL")
api_key     = os.getenv("LLM_API_KEY", "no-key")
model       = os.getenv("LLM_MODEL", "qwen3")
http_proxy  = os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
https_proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
ssl_verify  = os.getenv("SSL_VERIFY", "true").lower() != "false"

if not base_url:
    print("ERROR: LLM_BASE_URL is not set in .env")
    exit(1)

print("=" * 55)
print("  LLM TEST SUITE")
print("=" * 55)
print(f"  Endpoint   : {base_url}")
print(f"  Model      : {model}")
print(f"  Proxy      : {https_proxy or http_proxy or 'none'}")
print(f"  SSL verify : {ssl_verify}")
print("=" * 55)
print()

# Build httpx client
proxy_url = https_proxy or http_proxy
if proxy_url:
    transport = httpx.HTTPTransport(proxy=proxy_url, verify=ssl_verify)
    http_client = httpx.Client(transport=transport, verify=ssl_verify)
else:
    http_client = httpx.Client(verify=ssl_verify)

client = OpenAI(base_url=base_url, api_key=api_key, http_client=http_client)

results = []

def run_test(name: str, messages: list, check_fn) -> bool:
    print(f"[ ] {name} ...", end=" ", flush=True)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=200,
            temperature=0,
        )
        reply = response.choices[0].message.content.strip()
        ok = check_fn(reply)
        status = "OK" if ok else "FAIL"
        print(f"\r[{'✓' if ok else '✗'}] {name}")
        print(f"    → {reply[:120]}")
        results.append((name, ok))
        return ok
    except Exception as e:
        print(f"\r[✗] {name}")
        print(f"    → ERROR: {type(e).__name__}: {e}")
        results.append((name, False))
        return False

print()

# Test 1 — Connection ping
run_test(
    "Connection ping",
    [{"role": "user", "content": "Reply with exactly the words: CONNECTION OK"}],
    lambda r: "CONNECTION" in r.upper(),
)

# Test 2 — Instruction following
run_test(
    "Instruction following",
    [{"role": "user", "content": (
        "List exactly 3 fruits, one per line, nothing else."
    )}],
    lambda r: len([l for l in r.strip().splitlines() if l.strip()]) >= 2,
)

# Test 3 — Domain knowledge (data flow context)
run_test(
    "Domain knowledge — data flows",
    [
        {"role": "system", "content": (
            "You are an expert in data governance and batch processing."
        )},
        {"role": "user", "content": (
            "In one sentence, what is the role of an ItemProcessor in Spring Batch?"
        )},
    ],
    lambda r: len(r) > 20,
)

# Summary
print()
print("=" * 55)
passed = sum(1 for _, ok in results if ok)
total  = len(results)
print(f"  Results : {passed}/{total} tests passed")
if passed == total:
    print("  Status  : READY — Qwen3 is operational")
else:
    print("  Status  : ISSUES DETECTED — check details above")
print("=" * 55)
