from __future__ import annotations
from typing import Any, Dict, TYPE_CHECKING

from ..message import IncomingMessage
from ..reply import Reply
from ..session import Session
from .base import NodeHandler, BACK_KEYWORDS, HOME_KEYWORDS

if TYPE_CHECKING:
    from ..tree import FlowTree


class InputHandler(NodeHandler):
    """Handles ``input`` nodes — single free-text field collection."""

    async def handle(
        self,
        node: Dict[str, Any],
        session: Session,
        message: IncomingMessage,
        tree: "FlowTree",
    ) -> Reply:
        raw = (message.text or "").strip()

        # ── first render (no input yet) ───────────────────────────────
        if not raw:
            return self._render_input(node, session)

        lower = raw.lower()

        # ── back / home ───────────────────────────────────────────────
        if lower in BACK_KEYWORDS:
            self._go_back(session, tree.entry)
            return await self._enter_node(session, tree)
        if lower in HOME_KEYWORDS:
            self._go_home(session, tree.entry)
            return await self._enter_node(session, tree)

        # ── validate ──────────────────────────────────────────────────
        validate = node.get("validate")
        if validate:
            error = validate(raw)
            if error:
                return Reply(
                    type="text",
                    body=f"⚠️ {error}\n\n{node.get('prompt', '')}",
                    phone=session.user_id,
                    node_type="input",
                    current_node=session.current_node,
                )

        # ── transform and store ───────────────────────────────────────
        transform = node.get("transform", lambda v: v)
        session.collected[node["field"]] = transform(raw)

        # ── advance ───────────────────────────────────────────────────
        self._transition_to(session, node["next"])
        return await self._enter_node(session, tree)