from __future__ import annotations
from typing import Any, Dict, Optional, TYPE_CHECKING

from ..message import IncomingMessage
from ..reply import Reply
from ..session import Session
from .base import NodeHandler, BACK_KEYWORDS, HOME_KEYWORDS

if TYPE_CHECKING:
    from ..tree import FlowTree


class ConfirmHandler(NodeHandler):
    """Handles ``confirm`` nodes — review screen before a write action."""

    async def handle(
        self,
        node: Dict[str, Any],
        session: Session,
        message: IncomingMessage,
        tree: "FlowTree",
    ) -> Reply:
        raw = (message.interactive_id or message.text or "").strip()

        if not raw:
            return self._render_confirm(node, session)

        lower = raw.lower()

        if lower in BACK_KEYWORDS:
            self._go_back(session, tree.entry)
            return await self._enter_node(session, tree)
        if lower in HOME_KEYWORDS:
            self._go_home(session, tree.entry)
            return await self._enter_node(session, tree)

        matched_next = self._match_option(node, message, raw)

        if not matched_next:
            rendered = self._render_confirm(node, session)
            return Reply(
                type="text",
                body="Please choose one of the options.\n\n" + rendered.body,
                phone=session.user_id,
                options=rendered.options,
                node_type="confirm",
                current_node=session.current_node,
            )

        if matched_next == tree.entry:
            session.collected = {}

        self._transition_to(session, matched_next)
        return await self._enter_node(session, tree)

    def _match_option(
        self,
        node: Dict[str, Any],
        message: IncomingMessage,
        raw: str,
    ) -> Optional[str]:
        options = node.get("options", [])
        allow_numeric = node.get("allow_numeric", False)

        for i, opt in enumerate(options, 1):
            label    = opt.get("label", "") if isinstance(opt, dict) else opt[0]
            value    = opt.get("value", opt.get("next", "")) if isinstance(opt, dict) else opt[1]
            next_key = opt.get("next", "") if isinstance(opt, dict) else opt[1]

            if message.interactive_id and (
                message.interactive_id == value or message.interactive_id == next_key
            ):
                return next_key
            if allow_numeric and raw == str(i):
                return next_key
            if raw.lower() == label.lower():
                return next_key

        return None