import time
from typing import Dict, Optional
from ..session import Session, SessionStore


class InMemorySessionStore(SessionStore):
    """
    In-memory session store with automatic eviction of expired sessions.

    - Sessions are removed when they exceed `session_timeout` seconds since last activity.
    - Optional `max_sessions` prevents unbounded memory growth.
    - Cleanup runs automatically on every `get()` and `save()` (with a periodic full sweep).

    For production with high concurrency, consider a persistent store (Redis, SQLite).
    """

    def __init__(self, session_timeout: int = 1800, max_sessions: int = 10000):
        self._sessions: Dict[str, Session] = {}
        self.session_timeout = session_timeout
        self.max_sessions = max_sessions
        self._last_cleanup = time.time()
        self._cleanup_interval = 60  # seconds

    async def get(self, user_id: str) -> Optional[Session]:
        """Return session if exists and not expired, otherwise None."""
        self._maybe_cleanup()
        session = self._sessions.get(user_id)
        if session and session.is_expired(self.session_timeout):
            await self.delete(user_id)
            return None
        return session

    async def save(self, session: Session) -> None:
        """Store session, evicting oldest if max_sessions exceeded."""
        self._maybe_cleanup()
        # Enforce session limit (only when adding a new session)
        if len(self._sessions) >= self.max_sessions and session.user_id not in self._sessions:
            # Remove the session with the oldest last_active
            oldest_uid = min(self._sessions.items(), key=lambda x: x[1].last_active or 0)[0]
            await self.delete(oldest_uid)
        self._sessions[session.user_id] = session

    async def delete(self, user_id: str) -> None:
        self._sessions.pop(user_id, None)

    def all(self) -> Dict[str, Session]:
        """Return copy of all sessions (debug only)."""
        return dict(self._sessions)

    def _maybe_cleanup(self):
        """Periodically remove all expired sessions."""
        now = time.time()
        if now - self._last_cleanup >= self._cleanup_interval:
            expired = [
                uid for uid, sess in self._sessions.items()
                if sess.is_expired(self.session_timeout)
            ]
            for uid in expired:
                del self._sessions[uid]
            self._last_cleanup = now