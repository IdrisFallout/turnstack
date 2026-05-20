"""
turnstack.handlers.base
=======================
Abstract base for all node handlers.

Key responsibilities:
- ``handle()``         — implemented by each subclass
- ``_enter_node()``    — shared logic for entering any node after a transition
                         (handles chained actions, routers, etc.)
- ``_transition_to()`` — moves session forward, pushes nav stack
- ``_go_back()``       — pops nav stack, moves session backward
- ``_go_home()``       — clears nav stack, returns to entry node
- ``_make_reply()``    — builds a Reply with options hints attached
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..message import IncomingMessage
from ..reply import Reply, ReplyOption
from ..session import Session

if TYPE_CHECKING:
    from ..tree import FlowTree


# ── navigation keywords recognised in any input field ────────────────────────
BACK_KEYWORDS = {"0", "back", "go back"}
HOME_KEYWORDS = {"00", "home", "main menu", "start over"}


class NodeHandler(ABC):
    """Abstract handler for a single node type."""

    @abstractmethod
    async def handle(
        self,
        node: Dict[str, Any],
        session: Session,
        message: IncomingMessage,
        tree: "FlowTree",
    ) -> Reply:
        """Process the incoming message for this node and return a Reply."""
        ...

    # ── transition helpers ────────────────────────────────────────────────

    def _transition_to(self, session: Session, next_node: str) -> None:
        """
        Move session to ``next_node``, pushing the current node onto nav_stack
        so the user can navigate back.

        Does NOT push if going to "__end__" or if the destination is the same
        node (prevents nav_stack pollution on re-renders).
        """
        if not next_node or next_node == "__end__":
            return
        if next_node != session.current_node:
            session.nav_stack.append(session.current_node)
        session.current_node = next_node

    def _go_back(self, session: Session, entry_node: str) -> None:
        """Pop the nav stack and go to the previous node (or entry if at root)."""
        prev = session.go_back()
        session.current_node = prev if prev else entry_node

    def _go_home(self, session: Session, entry_node: str) -> None:
        """Jump to entry node, clearing the nav stack and collected data."""
        session.go_home(entry_node)

    # ── shared enter-node logic ───────────────────────────────────────────

    async def _enter_node(
        self,
        session: Session,
        tree: "FlowTree",
        _depth: int = 0,
    ) -> Reply:
        """
        Render the node ``session.current_node`` is pointing at.

        Handles transparent chaining:
        - router  → evaluates silently, enters the target node
        - action  → runs fn, then enters next node
        - anything else → renders and returns

        ``_depth`` guards against infinite loops (max 10 silent hops).
        """
        if _depth > 10:
            return self._error(session, "Infinite routing loop detected.")

        node = tree.get(session.current_node)
        if not node:
            return self._error(session, f"Node '{session.current_node}' not found.")

        t = node.get("type")

        # ── router: evaluate silently, then recurse ───────────────────
        if t == "router":
            from .router import RouterHandler
            return await RouterHandler()._run_router(node, session, tree, _depth)

        # ── action: run fn, advance, then recurse ─────────────────────
        if t == "action":
            from .action import ActionHandler
            return await ActionHandler()._run_action(node, session, tree, _depth)

        if t == "media":
            from .media_handler import MediaHandler
            # Dummy message because we're entering the node, not processing user input
            dummy = IncomingMessage(user_id=session.user_id, type="text", text="")
            return await MediaHandler().handle(node, session, dummy, tree)

        # ── everything else: render ───────────────────────────────────
        return self._render(node, session)

    # ── reply builders ────────────────────────────────────────────────────

    def _render(self, node: Dict[str, Any], session: Session) -> Reply:
        """Build a Reply for any renderable node type."""
        t = node.get("type")

        if t == "menu":
            return self._render_menu(node, session)
        if t == "confirm":
            return self._render_confirm(node, session)
        if t == "input":
            return self._render_input(node, session)
        if t == "multi_input":
            return self._render_multi_input(node, session)
        if t == "list":
            # list rendering is delegated to ListHandler
            from .list_handler import ListHandler
            return ListHandler()._render_list_page(node, session)

        return self._error(session, f"Cannot render node type '{t}'.")

    def _render_menu(self, node: Dict[str, Any], session: Session) -> Reply:
        text = node.get("text", "")
        options = node.get("options", [])
        button_label = node.get("button_label", "Options")
        print(f"DEBUG: button_label = {button_label}")

        # Build the reply options (used by WhatsApp adapter)
        reply_options = []
        for opt in options:
            label = opt.get("label", "") if isinstance(opt, dict) else opt[0]
            value = opt.get("value", opt.get("next", "")) if isinstance(opt, dict) else opt[1]
            desc = opt.get("description", "") if isinstance(opt, dict) else ""
            reply_options.append(ReplyOption(label=label, value=value, description=desc))

        # Create the Reply with meta
        return Reply(
            type="text",
            body=text,
            phone=session.user_id,
            options=reply_options,
            node_type="menu",
            current_node=session.current_node,
            meta={"button_label": button_label},
        )

    def _render_confirm(self, node: Dict[str, Any], session: Session) -> Reply:
        text = node.get("text", "")
        body = text(session.collected) if callable(text) else text

        options = node.get("options", [])
        reply_options: List[ReplyOption] = []

        for opt in options:
            label = opt.get("label", "") if isinstance(opt, dict) else opt[0]
            value = opt.get("value", opt.get("next", "")) if isinstance(opt, dict) else opt[1]
            reply_options.append(ReplyOption(label=label, value=value))

        return Reply(
            type="text",
            body=body,  # only the prompt, no numbered list
            phone=session.user_id,  # use user_id consistently
            options=reply_options,
            node_type="confirm",
            suggested_replies=[o.value for o in reply_options],
            current_node=session.current_node,
        )

    def _render_input(self, node: Dict[str, Any], session: Session) -> Reply:
        return Reply(
            type="text",
            body=node.get("prompt", ""),
            phone=session.user_id,
            node_type="input",
            current_node=session.current_node,
        )

    def _render_multi_input(self, node: Dict[str, Any], session: Session) -> Reply:
        """Render the current field inside a multi_input node."""
        fields = node.get("fields", [])
        idx = session.pagination.get(f"mi_{session.current_node}_idx", 0)
        if idx >= len(fields):
            # all fields collected — should not normally reach here
            return self._error(session, "MultiInput: field index out of range.")
        f = fields[idx]
        prompt = f.get("prompt", "")
        if idx == 0:
            intro = node.get("intro", "")
            if intro:
                prompt = intro + "\n\n" + prompt
        return Reply(
            type="text",
            body=prompt,
            phone=session.user_id,
            node_type="multi_input",
            current_node=session.current_node,
        )

    def _error(self, session: Session, msg: str) -> Reply:
        return Reply(type="error", body=msg, phone=session.user_id,
                     current_node=session.current_node)