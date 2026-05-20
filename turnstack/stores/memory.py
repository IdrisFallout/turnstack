from typing import Dict, Optional
from ..session import Session, SessionStore


class InMemorySessionStore(SessionStore):
    """
    In-memory session store — for development and testing only.

    Sessions are lost when the process restarts.
    For production, implement :class:`~turnstack.session.SessionStore`
    with Redis, Postgres, or SQLite.
    """

    def __init__(self):
        self._sessions: Dict[str, Session] = {}

    async def get(self, phone: str) -> Optional[Session]:
        return self._sessions.get(phone)

    async def save(self, session: Session) -> None:
        self._sessions[session.user_id] = session

    async def delete(self, phone: str) -> None:
        self._sessions.pop(phone, None)

    def all(self) -> Dict[str, Session]:
        """Return all sessions — useful for debug endpoints."""
        return dict(self._sessions)