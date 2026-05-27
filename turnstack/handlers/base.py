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
- ``_render()``        — builds a Reply for any renderable node type
"""

from __future__ import annotations
from abc import ABC, abstractmethod
import inspect
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..message import IncomingMessage
from ..reply import Reply, ReplyOption
from ..session import Session

if TYPE_CHECKING:
    from ..tree import FlowTree


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
        Move session to ``next_node``, pushing the current node onto nav_stack.

        Does NOT push if going to "__end__" or if the destination is the same node.
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
        - router → evaluates silently, enters the target node
        - action → runs fn, then enters next node
        - media  → generates and returns media reply
        - anything else → renders and returns

        ``_depth`` guards against infinite loops (max 10 silent hops).
        """
        if _depth > 10:
            return self._error(session, "Infinite routing loop detected.")

        node = tree.get(session.current_node)
        if not node:
            return self._error(session, f"Node '{session.current_node}' not found.")

        t = node.get("type")

        if t == "router":
            from .router import RouterHandler
            return await RouterHandler()._run_router(node, session, tree, _depth)

        if t == "action":
            from .action import ActionHandler
            return await ActionHandler()._run_action(node, session, tree, _depth)

        if t == "media":
            from .media_handler import MediaHandler
            dummy = IncomingMessage(user_id=session.user_id, type="text", text="")
            return await MediaHandler().handle(node, session, dummy, tree)

        # Everything else (menu, confirm, input, list): render
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

        if t == "list":
            return self._render_list(node, session)

        # Media is a transparent/auto-executing node (like router/action).
        # It must be dispatched by the engine, never rendered statically.
        # If we reach here it means _render_current had a gap — surface a
        # clear error instead of going silent.
        if t == "media":
            return self._error(
                session,
                "MediaReply node reached _render() — this is a bug. "
                "Ensure engine._render_current dispatches 'media' directly.",
            )

        return self._error(session, f"Cannot render node type '{t}'.")

    def _render_menu(self, node: Dict[str, Any], session: Session) -> Reply:
        # Delegate to MenuHandler so pagination is applied consistently
        # whether we arrive via a cold entry (router/action chain) or
        # directly through MenuHandler.handle().
        from .menu import MenuHandler
        all_options = node.get("options", [])
        pkey        = f"menu_{session.current_node}_page"
        page        = session.pagination.get(pkey, 0)

        from .menu import MAX_MENU_ROWS
        items_per_page = MAX_MENU_ROWS - 2 if len(all_options) > MAX_MENU_ROWS else MAX_MENU_ROWS
        total_pages    = max(1, (len(all_options) + items_per_page - 1) // items_per_page)
        if page >= total_pages:
            page = max(0, total_pages - 1)
            session.pagination[pkey] = page

        return MenuHandler()._render_menu_page(node, session, all_options, page, total_pages)

    def _render_confirm(self, node: Dict[str, Any], session: Session) -> Reply:
        text = node.get("text", "")
        body = text(session.collected) if callable(text) else text

        options      = node.get("options", [])
        reply_options: List[ReplyOption] = []

        for opt in options:
            label = opt.get("label", "") if isinstance(opt, dict) else opt[0]
            value = opt.get("value", opt.get("next", "")) if isinstance(opt, dict) else opt[1]
            reply_options.append(ReplyOption(label=label, value=value))

        return Reply(
            type="text",
            body=body,
            phone=session.user_id,
            options=reply_options,
            node_type="confirm",
            suggested_replies=[o.value for o in reply_options],
            current_node=session.current_node,
            session_state=session.lifecycle_state,
        )

    def _render_input(self, node: Dict[str, Any], session: Session) -> Reply:
        """
        Render the current field inside an input node.

        Two cases:
        - Cold entry (no idx in pagination): reset the node and render field 0.
        - Back-nav within the node (idx already set by engine): skip reset,
          render whichever field the engine stepped back to.
        """
        from .input import InputHandler, _IDX_KEY_TMPL, _flatten_fields
        fields = node.get("fields", [])

        if not fields:
            return self._error(session, "Input: no fields defined.")

        handler = InputHandler()
        idx_key = _IDX_KEY_TMPL.format(node=session.current_node)

        if idx_key in session.pagination:
            # Back-nav has already positioned the index -- just re-render that field.
            # Flatten so BranchFields that are now active are included and the
            # step counter reflects the real remaining work.
            idx    = session.pagination[idx_key]
            fields = _flatten_fields(fields, session)
        else:
            # Genuine cold entry -- reset first (wipes collected + pagination),
            # then flatten against the now-clean session so branch conditions
            # that depended on stale answers from a previous run evaluate to
            # False. Without the re-flatten the BranchField object itself is
            # counted as a field, giving "Step 1 of 8" instead of "Step 1 of 7".
            handler._reset_input(session, node, fields)
            fields = _flatten_fields(fields, session)
            idx = 0

        return handler._render_field(node, session, fields, idx)

    def _render_list(self, node: Dict[str, Any], session: Session) -> Reply:
        from .list_handler import ListHandler, MAX_ROWS

        fetch = node.get("fetch")
        if not fetch:
            return self._error(session, "ListNode has no 'fetch' function.")

        page_size  = min(MAX_ROWS, max(1, node.get("page_size", 8)))
        interactive = node.get("interactive", False)

        sig            = inspect.signature(fetch)
        paginated_mode = len(sig.parameters) >= 3

        pkey = f"list_{session.current_node}_page"
        page = session.pagination.get(pkey, 0)

        if paginated_mode:
            try:
                result = fetch(session, page, page_size)
            except Exception as exc:
                return self._error(session, f"Paginated fetch raised: {exc}")
            if isinstance(result, tuple) and len(result) == 2:
                items_page, total_items = result
            elif isinstance(result, dict):
                items_page  = result.get("items", [])
                total_items = result.get("total", 0)
            else:
                return self._error(session, "Paginated fetch must return (items, total) or dict.")
            items_page = list(items_page or [])
        else:
            try:
                items = fetch(session)
            except Exception as exc:
                return self._error(session, f"Fetch raised: {exc}")
            items       = list(items or [])
            total_items = len(items)
            start       = page * page_size
            items_page  = items[start: start + page_size]

        total_pages = max(1, (total_items + page_size - 1) // page_size)
        if page >= total_pages:
            page = max(0, total_pages - 1)
            session.pagination[pkey] = page

        return ListHandler()._render_list_page(
            node, session, items_page, page, total_pages, interactive, page_size, paginated_mode
        )

    def _error(self, session: Session, msg: str) -> Reply:
        return Reply(
            type="error",
            body=msg,
            phone=session.user_id,
            current_node=session.current_node,
            session_state=session.lifecycle_state,
        )