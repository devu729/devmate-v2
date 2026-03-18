"""
session_store.py
In-memory session store with auto-expiry (4 hours).
"""

import uuid
import time
from typing import Optional
from pydantic import BaseModel, Field


SESSION_TTL_SECONDS = 4 * 60 * 60  # 4 hours


class Session(BaseModel):
    session_id: str
    github_url: str
    repo_name: str

    # DO Gradient resource IDs
    kb_id: Optional[str] = None
    agent_id: Optional[str] = None

    # Lifecycle
    status: str = "pending"         # pending | indexing | ready | error
    status_message: str = "Initializing..."

    # Progress tracking
    file_count: int = 0
    indexed_count: int = 0

    # Repo intelligence
    dependencies: dict = Field(default_factory=dict)   # {python:{pkg:ver}, node:{...}, ...}
    code_patterns: dict = Field(default_factory=dict)  # {framework, folder_structure, ...}
    file_paths: list[str] = Field(default_factory=list)

    # Timestamps (unix epoch floats)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    def touch(self):
        self.updated_at = time.time()

    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > SESSION_TTL_SECONDS

    def to_status_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "repo_name": self.repo_name,
            "github_url": self.github_url,
            "status": self.status,
            "status_message": self.status_message,
            "file_count": self.file_count,
            "indexed_count": self.indexed_count,
            "dependencies": self.dependencies,
            "code_patterns": self.code_patterns,
            "kb_id": self.kb_id,
            "agent_id": self.agent_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class SessionStore:
    def __init__(self):
        self._store: dict[str, Session] = {}

    def create(self, github_url: str, repo_name: str) -> Session:
        session_id = str(uuid.uuid4())
        session = Session(
            session_id=session_id,
            github_url=github_url,
            repo_name=repo_name,
        )
        self._store[session_id] = session
        self._evict_expired()
        return session

    def get(self, session_id: str) -> Optional[Session]:
        session = self._store.get(session_id)
        if session is None:
            return None
        if session.is_expired():
            del self._store[session_id]
            return None
        return session

    def update(self, session: Session) -> Session:
        session.touch()
        self._store[session.session_id] = session
        return session

    def delete(self, session_id: str) -> bool:
        if session_id in self._store:
            del self._store[session_id]
            return True
        return False

    def list_all(self) -> list[dict]:
        self._evict_expired()
        return [s.to_status_dict() for s in self._store.values()]

    def _evict_expired(self):
        expired = [sid for sid, s in self._store.items() if s.is_expired()]
        for sid in expired:
            del self._store[sid]

    def __len__(self) -> int:
        self._evict_expired()
        return len(self._store)


# Module-level singleton
store = SessionStore()
