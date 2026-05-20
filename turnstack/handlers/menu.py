from __future__ import annotations
from typing import Any, Dict, Optional, TYPE_CHECKING

from ..message import IncomingMessage
from ..reply import Reply
from ..session import Session
from .base import NodeHandler, BACK_KEYWORDS, HOME_KEYWORDS

if TYPE_CHECKING:
    from ..tree import FlowTree


class MenuHandler(NodeHandler):
    """
    Handles ``menu`` nodes.

    Accepts input via:
    1. Interactive ID (``message.interactive_id``) — always checked first.
       The value must match an Option's ``value`` field.
    2. Numeric digit ("1", "2" …) — only if ``allow_numeric=True`` on the node.
    3. Label text (case-insensitive) — fallback for text-only channels.
    4. Back / Home keywords — "0" goes back, "00" goes home.
    """

    async def handle(
        self,
        node: Dict[str, Any],
        session: Session,
        message: IncomingMessage,
        tree: "FlowTree",
    ) -> Reply:
        raw_input = (message.interactive_id or message.text or "").strip()

        # ── nothing yet — first render ────────────────────────────────
        if not raw_input:
            return self._render_menu(node, session)

        lower = raw_input.lower()

        # ── back / home navigation ────────────────────────────────────
        if lower in BACK_KEYWORDS:
            self._go_back(session, tree.entry)
            return await self._enter_node(session, tree)
        if lower in HOME_KEYWORDS:
            self._go_home(session, tree.entry)
            return await self._enter_node(session, tree)

        # ── match option ──────────────────────────────────────────────
        matched_next = self._match_option(node, message, raw_input)

        if not matched_next:
            return Reply(
                type="text",
                body="Invalid option. Please choose from the list.\n\n"
                     + self._render_menu(node, session).body,
                phone=session.user_id,
                options=self._render_menu(node, session).options,
                node_type="menu",
                current_node=session.current_node,
            )

        # store selected value in context for downstream access
        session.context["last_option"] = matched_next

        # clear collected when going home
        if matched_next == tree.entry:
            session.collected = {}

        self._transition_to(session, matched_next)
        return await self._enter_node(session, tree)

    def _match_option(
        self,
        node: Dict[str, Any],
        message: IncomingMessage,
        raw_input: str,
    ) -> Optional[str]:
        options = node.get("options", [])
        allow_numeric = node.get("allow_numeric", False)

        for i, opt in enumerate(options, 1):
            label = opt.get("label", "") if isinstance(opt, dict) else opt[0]
            value = opt.get("value", opt.get("next", "")) if isinstance(opt, dict) else opt[1]
            next_key = opt.get("next", "") if isinstance(opt, dict) else opt[1]

            # 1. Interactive ID match
            if message.interactive_id and (
                message.interactive_id == value or message.interactive_id == next_key
            ):
                return next_key

            # 2. Numeric
            if allow_numeric and raw_input == str(i):
                return next_key

            # 3. Label (case-insensitive)
            if raw_input.lower() == label.lower():
                return next_key

        return None