"""
gradient_agent.py
DO Gradient Serverless Inference — chat, generate, debug.
"""
import os
import json
import logging
import re
import time
from typing import Optional

import httpx

log = logging.getLogger("devmate.gradient_agent")

GRADIENT_API = "https://api.digitalocean.com/v2/gen-ai"
INFERENCE_MODEL = "llama3.3-70b-instruct"
INFERENCE_URL = "https://inference.do-ai.run/v1/chat/completions"
MAX_TOKENS = 4096


def _client() -> httpx.AsyncClient:
    model_key = os.getenv("GRADIENT_MODEL_ACCESS_KEY", os.getenv("DO_GRADIENT_API_KEY", ""))
    return httpx.AsyncClient(
        headers={
            "Authorization": f"Bearer {model_key}",
            "Content-Type": "application/json",
        },
        timeout=120,
    )


def _raise_for_status(response: httpx.Response, context: str) -> None:
    if response.status_code == 401:
        raise RuntimeError("DO Gradient: Unauthorized — check GRADIENT_MODEL_ACCESS_KEY")
    if response.status_code >= 400:
        raise RuntimeError(f"DO Gradient error {response.status_code} — {context}: {response.text[:300]}")


async def _inference(api_key: str, messages: list[dict]) -> str:
    payload = {
        "model": INFERENCE_MODEL,
        "messages": messages,
        "max_tokens": MAX_TOKENS,
        "temperature": 0.2,
    }
    async with _client() as client:
        r = await client.post(INFERENCE_URL, json=payload)
        _raise_for_status(r, "inference")
        data = r.json()
    choices = data.get("choices", [])
    if choices:
        return choices[0].get("message", {}).get("content", "")
    return str(data)


def _build_system(repo_context: dict) -> str:
    repo_name = repo_context.get("repo_name", "repo")
    deps = repo_context.get("dependencies", {})
    patterns = repo_context.get("code_patterns", {})
    file_paths = repo_context.get("file_paths", [])[:100]

    dep_lines = []
    for eco, pkgs in deps.items():
        for pkg, ver in list(pkgs.items())[:15]:
            dep_lines.append(f"  {pkg}: {ver}")

    return f"""You are DevMate — an expert AI coding assistant for the {repo_name} codebase.

FRAMEWORK: {patterns.get('framework', 'unknown')}
NAMING: {patterns.get('naming_conventions', 'unknown')}
IMPORTS: {patterns.get('import_style', 'unknown')}
TESTS: {patterns.get('test_framework', 'unknown')}

DEPENDENCIES:
{chr(10).join(dep_lines) or '  none detected'}

FILE TREE (first 100 files):
{chr(10).join('  ' + p for p in file_paths)}

RULES:
1. Only reference real files from the file tree above
2. Use exact dependency versions listed — never suggest upgrades
3. Match naming convention: {patterns.get('naming_conventions', 'unknown')}
4. Generate code that fits the existing {patterns.get('framework', '')} patterns
5. Never suggest deprecated APIs
"""


async def create_agent(api_key: str, kb_id: str, repo_context: dict) -> str:
    repo_name = repo_context.get("repo_name", "repo")
    agent_id = f"agent-{re.sub(r'[^a-zA-Z0-9]', '-', repo_name).lower()}-{int(time.time())}"
    log.info("Agent created: %s", agent_id)
    return agent_id


async def chat(api_key: str, agent_id: str, message: str, history: list[dict]) -> str:
    messages = []
    for turn in history[-10:]:
        if turn.get("role") in ("user", "assistant"):
            messages.append(turn)
    messages.append({"role": "user", "content": message})

    full_messages = [
        {"role": "system", "content": "You are DevMate, a helpful AI coding assistant. Answer questions about the codebase clearly and concisely."}
    ] + messages

    return await _inference(api_key, full_messages)


async def generate_code(api_key: str, agent_id: str, feature_request: str, context: dict) -> dict:
    system = _build_system(context)
    prompt = f"""Generate code for this feature in the {context.get('repo_name', 'repo')} codebase.

FEATURE REQUEST: {feature_request}

Return ONLY valid JSON in this exact structure:
{{
  "code_blocks": [
    {{
      "file": "path/to/file.py",
      "action": "create|modify|append",
      "code": "complete code here",
      "explanation": "one sentence"
    }}
  ],
  "explanation": "overall explanation",
  "files_to_change": ["list", "of", "paths"]
}}"""

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]

    raw = await _inference(api_key, messages)
    return _parse_json(raw, {
        "code_blocks": [],
        "explanation": raw,
        "files_to_change": [],
    })


async def debug_code(api_key: str, agent_id: str, error: str, stack_trace: str, context: dict) -> dict:
    system = _build_system(context)
    prompt = f"""Debug this error in the {context.get('repo_name', 'repo')} codebase.

ERROR: {error}

STACK TRACE:
{stack_trace}

Return ONLY valid JSON:
{{
  "root_cause": "precise description",
  "fix": [
    {{
      "file": "path/to/file.py",
      "code": "corrected code",
      "explanation": "what changed and why"
    }}
  ],
  "prevention": "how to prevent this"
}}"""

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]

    raw = await _inference(api_key, messages)
    return _parse_json(raw, {
        "root_cause": raw,
        "fix": [],
        "prevention": "See raw response above.",
    })


async def delete_agent(api_key: str, agent_id: str) -> None:
    log.info("Agent %s deleted", agent_id)


def _parse_json(raw: str, default: dict) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE)
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    default["_raw_response"] = raw
    return default
