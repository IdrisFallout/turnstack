from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List
from datetime import datetime
from abc import ABC, abstractmethod


@dataclass
class Session:
    """
    A single user's conversation state.

    The engine reads and writes this object on every message.
    Developers can read ``session.collected``,
    and ``session.context`` inside action and router functions.

    Fields
    ------
    phone:           Session key — the user's phone number.
    lifecycle_state: "new" | "active" | "expired"
    current_node:    Key of the node the user is currently on.
    nav_stack:       Back-navigation history.  The engine pushes before
                     every forward transition and pops on "go back".
                     Developers should not modify this directly.
    collected:       Data gathered via Input nodes.
                     Cleared when the session returns to the entry node.
    pagination:      Internal state for ListNode pagination.
    context:         Arbitrary dict for cross-node data.
                     Use this in router ``before`` functions to load a user
                     profile, store an invitation, etc.
    last_active:     UTC timestamp of the last message processed.
    """
    user_id: str
    lifecycle_state: str = "new"
    current_node: str = "entry"
    nav_stack: List[str] = field(default_factory=list)
    collected: Dict[str, Any] = field(default_factory=dict)
    pagination: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)
    last_active: Optional[datetime] = None

    def __post_init__(self):
        if self.last_active is None:
            self.last_active = datetime.utcnow()

    # ── lifecycle ──────────────────────────────────────────────────────────

    def touch(self) -> None:
        """Update last_active and activate a new/expired session."""
        self.last_active = datetime.utcnow()
        if self.lifecycle_state in ("new", "expired"):
            self.lifecycle_state = "active"

    def is_expired(self, timeout_seconds: int) -> bool:
        if self.lifecycle_state == "expired":
            return True
        if self.lifecycle_state == "active" and self.last_active:
            delta = datetime.utcnow() - self.last_active
            return delta.total_seconds() > timeout_seconds
        return False

    def expire(self) -> None:
        self.lifecycle_state = "expired"

    def reset(self, entry_node: str) -> None:
        """Reset all flow state, keep phone."""
        self.current_node = entry_node
        self.nav_stack = []
        self.collected = {}
        self.pagination = {}
        self.context = {}
        self.lifecycle_state = "active"
        self.touch()

    # ── navigation helpers (used by the engine, available to developers) ──

    def go_back(self) -> Optional[str]:
        """
        Pop and return the previous node from the nav stack.
        Returns None if already at the root.
        """
        if self.nav_stack:
            return self.nav_stack.pop()
        return None

    def go_home(self, entry_node: str) -> None:
        """Clear the nav stack and return to the entry node."""
        self.nav_stack.clear()
        self.current_node = entry_node
        self.collected = {}


class SessionStore(ABC):
    """
    Abstract base class for session persistence.

    Implement this for your storage backend (Redis, Postgres, SQLite, etc.).
    TurnStack ships with :class:`~turnstack.stores.memory.InMemorySessionStore`
    for development and testing.

    Example Redis implementation outline::

        class RedisSessionStore(SessionStore):
            def __init__(self, redis_client):
                self.r = redis_client

            async def get(self, phone):
                data = await self.r.get(f"session:{phone}")
                return pickle.loads(data) if data else None

            async def save(self, session):
                await self.r.setex(f"session:{session.phone}", 3600, pickle.dumps(session))

            async def delete(self, phone):
                await self.r.delete(f"session:{phone}")
    """

    @abstractmethod
    async def get(self, phone: str) -> Optional[Session]:
        """Load session by phone number. Return None if not found."""
        ...

    @abstractmethod
    async def save(self, session: Session) -> None:
        """Persist session."""
        ...

    @abstractmethod
    async def delete(self, phone: str) -> None:
        """Delete session."""
        ...