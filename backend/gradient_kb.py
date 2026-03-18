import logging
import time
import re

log = logging.getLogger("devmate.gradient_kb")

async def create_knowledge_base(api_key: str, repo_name: str) -> str:
    kb_id = f"local-kb-{re.sub(r'[^a-zA-Z0-9]', '-', repo_name).lower()}-{int(time.time())}"
    log.info("Local KB created: %s", kb_id)
    return kb_id

async def upload_files_to_kb(api_key: str, kb_id: str, files: list[dict]) -> int:
    log.info("KB %s — %d files registered for prompt injection", kb_id, len(files))
    return len(files)

async def trigger_indexing(api_key: str, kb_id: str) -> None:
    log.info("KB %s — indexing complete (prompt-injection mode)", kb_id)

async def wait_for_indexing(api_key: str, kb_id: str, timeout: int = 600) -> str:
    return "ready"

async def delete_knowledge_base(api_key: str, kb_id: str) -> None:
    log.info("KB %s deleted", kb_id)
