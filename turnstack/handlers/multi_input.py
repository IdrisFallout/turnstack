from __future__ import annotations
from typing import Any, Dict, TYPE_CHECKING

from ..message import IncomingMessage
from ..reply import Reply
from ..session import Session
from .base import NodeHandler

if TYPE_CHECKING:
    from ..tree import FlowTree

_IDX_KEY = "mi_{node}_idx"


class MultiInputHandler(NodeHandler):
    """
    Handles ``multi_input`` nodes.

    Walks through the ``fields`` list one at a time, validating and storing
    each value, then advances to ``next`` when all fields are collected.
    """

    async def handle(
        self,
        node: Dict[str, Any],
        session: Session,
        message: IncomingMessage,
        tree: "FlowTree",
    ) -> Reply:
        fields  = node.get("fields", [])
        idx_key = f"mi_{session.current_node}_idx"
        idx     = session.pagination.get(idx_key, 0)
        raw     = (message.text or "").strip()

        # ── first entry — no input yet ────────────────────────────────
        if not raw:
            if idx == 0:
                session.pagination[idx_key] = 0
            return self._render_field(node, session, fields, idx)

        # ── validate current field ────────────────────────────────────
        current_field = fields[idx]
        validate = current_field.get("validate")
        if validate:
            error = validate(raw)
            if error:
                return Reply(
                    type="text",
                    body=f"⚠️ {error}\n\n{current_field.get('prompt', '')}",
                    phone=session.user_id,
                    node_type="multi_input",
                    current_node=session.current_node,
                )

        # ── store ─────────────────────────────────────────────────────
        transform = current_field.get("transform", lambda v: v)
        session.collected[current_field["name"]] = transform(raw)
        idx += 1
        session.pagination[idx_key] = idx

        # ── advance to next field or finish ───────────────────────────
        if idx >= len(fields):
            session.pagination.pop(idx_key, None)
            self._transition_to(session, node["next"])
            return await self._enter_node(session, tree)

        return self._render_field(node, session, fields, idx)

    def _render_field(self, node, session, fields, idx) -> Reply:
        f = fields[idx]
        prompt = f.get("prompt", "")
        if idx == 0:
            intro = node.get("intro", "")
            if intro:
                prompt = intro + "\n\n" + prompt
        total = len(fields)
        progress = f"({idx + 1}/{total}) "
        return Reply(
            type="text",
            body=progress + prompt,
            phone=session.user_id,
            node_type="multi_input",
            current_node=session.current_node,
        )