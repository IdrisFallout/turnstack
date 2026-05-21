from __future__ import annotations
from typing import Any, Dict, Optional, TYPE_CHECKING

from ..message import IncomingMessage
from ..reply import Reply, ReplyOption
from ..session import Session
from .base import NodeHandler

if TYPE_CHECKING:
    from ..tree import FlowTree

# WhatsApp interactive list max rows
MAX_MENU_ROWS = 10
PREV_PAGE = "__menu_prev__"
NEXT_PAGE = "__menu_next__"


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
        all_options = node.get("options", [])

        # ── pagination state ─────────────────────────────────────────
        pkey = f"menu_{session.current_node}_page"
        page = session.pagination.get(pkey, 0)
        total_pages = max(1, (len(all_options) + MAX_MENU_ROWS - 1) // MAX_MENU_ROWS)
        if page >= total_pages:
            page = total_pages - 1 if total_pages > 0 else 0
            session.pagination[pkey] = page

        # ── nothing yet — first render ────────────────────────────────
        if not raw_input:
            return self._render_menu_page(node, session, all_options, page, total_pages)

        # ── interactive pagination ────────────────────────────────────
        if message.interactive_id == PREV_PAGE:
            if page > 0:
                session.pagination[pkey] = page - 1
            return await self._enter_node(session, tree)
        if message.interactive_id == NEXT_PAGE:
            if page + 1 < total_pages:
                session.pagination[pkey] = page + 1
            return await self._enter_node(session, tree)

        # ── match option on current page ──────────────────────────────
        start = page * MAX_MENU_ROWS
        page_options = all_options[start: start + MAX_MENU_ROWS]
        matched_next = self._match_option(page_options, message, raw_input, node.get("allow_numeric", False))

        if not matched_next:
            rendered = self._render_menu_page(node, session, all_options, page, total_pages)
            return Reply(
                type="text",
                body="Invalid option. Please choose from the list.\n\n" + rendered.body,
                phone=session.user_id,
                options=rendered.options,
                node_type="menu",
                current_node=session.current_node,
                meta=rendered.meta,
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
        options: list,
        message: IncomingMessage,
        raw_input: str,
        allow_numeric: bool,
    ) -> Optional[str]:
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

    def _render_menu_page(
        self,
        node: Dict[str, Any],
        session: Session,
        all_options: list,
        page: int,
        total_pages: int,
    ) -> Reply:
        start = page * MAX_MENU_ROWS
        page_options = all_options[start: start + MAX_MENU_ROWS]
        text = node.get("text", "")
        button_label = node.get("button_label", "Options")

        # Build the reply options for the current page
        reply_options = []
        for opt in page_options:
            label = opt.get("label", "") if isinstance(opt, dict) else opt[0]
            value = opt.get("value", opt.get("next", "")) if isinstance(opt, dict) else opt[1]
            desc = opt.get("description", "") if isinstance(opt, dict) else ""
            reply_options.append(ReplyOption(label=label, value=value, description=desc))

        # Add pagination options if needed
        if total_pages > 1:
            if page > 0:
                reply_options.append(ReplyOption(
                    label="◀ Previous Page",
                    value=PREV_PAGE,
                    description=f"Page {page}/{total_pages}"
                ))
            if page < total_pages - 1:
                reply_options.append(ReplyOption(
                    label="Next Page ▶",
                    value=NEXT_PAGE,
                    description=f"Page {page+2}/{total_pages}"
                ))

        # Optionally show page info in body
        body = text
        if total_pages > 1:
            body = f"{text}\n\n(Page {page+1} of {total_pages})"

        return Reply(
            type="text",
            body=body,
            phone=session.user_id,
            options=reply_options,
            node_type="menu",
            current_node=session.current_node,
            session_state=session.lifecycle_state,
            meta={"button_label": button_label},
        )