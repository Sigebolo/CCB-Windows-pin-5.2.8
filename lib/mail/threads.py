"""
Thread-ID to Session-ID mapping for CCB Mail.

Maps email thread IDs to CCB session IDs for conversation continuity.
"""

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Dict
from threading import Lock

from .config import get_config_dir, ensure_config_dir

THREADS_FILE = "threads.json"
# Clean up threads older than 7 days
THREAD_TTL_SECONDS = 7 * 24 * 60 * 60


@dataclass
class ThreadMapping:
    """Mapping from email thread to CCB session."""
    session_id: str
    provider: str
    last_updated: float
    message_count: int = 1

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "ThreadMapping":
        return cls(
            session_id=data.get("session_id", ""),
            provider=data.get("provider", ""),
            last_updated=data.get("last_updated", 0),
            message_count=data.get("message_count", 1),
        )


class ThreadStore:
    """Thread-to-session mapping store."""

    def __init__(self, threads_file: Optional[Path] = None):
        if threads_file is None:
            self.threads_file = get_config_dir() / THREADS_FILE
        elif isinstance(threads_file, str):
            self.threads_file = Path(threads_file)
        else:
            self.threads_file = threads_file
        self._lock = Lock()
        self._cache: Dict[str, ThreadMapping] = {}
        self._loaded = False

    def _load(self) -> None:
        """Load threads from file."""
        if self._loaded:
            return

        with self._lock:
            if self._loaded:
                return

            if self.threads_file.exists():
                try:
                    with open(self.threads_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    for thread_id, mapping_data in data.items():
                        self._cache[thread_id] = ThreadMapping.from_dict(mapping_data)
                except (json.JSONDecodeError, IOError) as e:
                    print(f"Warning: Failed to load threads: {e}")

            self._loaded = True

    def _save(self) -> None:
        """Save threads to file."""
        ensure_config_dir()
        data = {tid: m.to_dict() for tid, m in self._cache.items()}
        with open(self.threads_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        self.threads_file.chmod(0o600)

    def get(self, thread_id: str) -> Optional[ThreadMapping]:
        """Get session mapping for a thread ID."""
        self._load()
        with self._lock:
            return self._cache.get(thread_id)

    def set(self, thread_id: str, session_id: str, provider: str) -> ThreadMapping:
        """Set or update session mapping for a thread ID."""
        self._load()
        with self._lock:
            existing = self._cache.get(thread_id)
            if existing:
                existing.last_updated = time.time()
                existing.message_count += 1
                mapping = existing
            else:
                mapping = ThreadMapping(
                    session_id=session_id,
                    provider=provider,
                    last_updated=time.time(),
                    message_count=1,
                )
                self._cache[thread_id] = mapping

            self._save()
            return mapping

    def delete(self, thread_id: str) -> bool:
        """Delete a thread mapping."""
        self._load()
        with self._lock:
            if thread_id in self._cache:
                del self._cache[thread_id]
                self._save()
                return True
            return False

    def cleanup_old(self, ttl_seconds: float = THREAD_TTL_SECONDS) -> int:
        """Remove threads older than TTL."""
        self._load()
        now = time.time()
        removed = 0

        with self._lock:
            to_remove = []
            for thread_id, mapping in self._cache.items():
                if now - mapping.last_updated > ttl_seconds:
                    to_remove.append(thread_id)

            for thread_id in to_remove:
                del self._cache[thread_id]
                removed += 1

            if removed > 0:
                self._save()

        return removed

    def get_all(self) -> Dict[str, ThreadMapping]:
        """Get all thread mappings."""
        self._load()
        with self._lock:
            return dict(self._cache)

    def generate_session_id(self, provider: str, context: str = "") -> str:
        """Generate a new session ID for a provider."""
        timestamp = int(time.time())
        return f"{provider}:{context}:{timestamp}" if context else f"{provider}:{timestamp}"


# Global thread store instance
_thread_store: Optional[ThreadStore] = None


def get_thread_store() -> ThreadStore:
    """Get the global thread store instance."""
    global _thread_store
    if _thread_store is None:
        _thread_store = ThreadStore()
    return _thread_store
