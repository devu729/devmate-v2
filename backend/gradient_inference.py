import json
import logging
import os
import re
from typing import Optional
import httpx

log = logging.getLogger("devmate.gradient_inference")

INFERENCE_MODEL = "llama3.3-70b-instruct"
INFERENCE_URL = "https://inference.do-ai.run/v1/chat/completions"
MAX_TOKENS = 2048


def _client(api_key: str) -> httpx.AsyncClient:
    model_key = os.getenv("GRADIENT_MODEL_ACCESS_KEY", api_key)
    return httpx.AsyncClient(
        headers={"Authorization": f"Bearer {model_key}", "Content-Type": "application/json"},
        timeout=60,
    )


async def run_inference(api_key: str, prompt: str, system: str = "", context: str = "") -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    user_content = f"{context}\n\n{prompt}".strip() if context else prompt
    messages.append({"role": "user", "content": user_content})
    payload = {"model": INFERENCE_MODEL, "messages": messages, "max_tokens": MAX_TOKENS, "temperature": 0.1}
    async with _client(api_key) as client:
        r = await client.post(INFERENCE_URL, json=payload)
        if r.status_code >= 400:
            raise RuntimeError(f"Inference error {r.status_code}: {r.text[:200]}")
        data = r.json()
    choices = data.get("choices", [])
    if choices:
        return choices[0].get("message", {}).get("content", "")
    return str(data)


async def fetch_relevant_docs(api_key: str, library: str, version: str, question: str) -> str:
    return await run_inference(
        api_key=api_key,
        prompt=f"For {library} version {version}: {question}",
        system=f"You are a documentation expert for {library} {version}. Answer based on that exact version API.",
    )


async def check_for_deprecations(api_key: str, code: str, dependencies: dict) -> list[dict]:
    return []
