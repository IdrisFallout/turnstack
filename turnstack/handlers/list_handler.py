"""
handlers/list_handler.py
========================
ListHandler — dynamic paginated list from DB/API.

Now supports interactive WhatsApp list messages.
"""

from __future__ import annotations
import inspect
from typing import Any, Dict, List, TYPE_CHECKING

from ..message import IncomingMessage
from ..reply import Reply, ReplyOption
from ..session import Session
from .base import NodeHandler, BACK_KEYWORDS, HOME_KEYWORDS

if TYPE_CHECKING:
    from ..tree import FlowTree

NEXT_PAGE_KEYWORDS = {"n", "next", "more"}
PREV_PAGE_KEYWORDS = {"p", "prev", "previous", "back page"}

# Special values for interactive pagination
PREV_PAGE_VALUE = "__prev_page__"
NEXT_PAGE_VALUE = "__next_page__"


class ListHandler(NodeHandler):

    async def handle(
        self,
        node: Dict[str, Any],
        session: Session,
        message: IncomingMessage,
        tree: "FlowTree",
    ) -> Reply:
        raw = (message.interactive_id or message.text or "").strip().lower()
        interactive = node.get("interactive", False)

        # ── back / home (only for plain text mode) ───────────────────────
        if not interactive and raw in BACK_KEYWORDS:
            self._go_back(session, tree.entry)
            return await self._enter_node(session, tree)
        if not interactive and raw in HOME_KEYWORDS:
            self._go_home(session, tree.entry)
            return await self._enter_node(session, tree)

        # ── fetch items ─────────────────────────────────────────────────
        fetch = node.get("fetch")
        if not fetch:
            return self._error(session, "ListNode has no 'fetch' function.")

        try:
            items = await fetch(session) if inspect.iscoroutinefunction(fetch) else fetch(session)
        except Exception as exc:
            return self._error(session, f"ListNode fetch raised: {exc}")

        items = list(items or [])
        page_size = node.get("page_size", 5)
        total_pages = max(1, (len(items) + page_size - 1) // page_size)

        # ── pagination state ───────────────────────────────────────────
        pkey = f"list_{session.current_node}_page"
        page = session.pagination.get(pkey, 0)

        # ── first render ───────────────────────────────────────────────
        if not raw:
            session.pagination[pkey] = 0
            return self._render_list_page(node, session, items, 0, interactive)

        # ── handle interactive selection (value based) ─────────────────
        if interactive and message.interactive_id:
            selected_value = message.interactive_id
            # Pagination special values
            if selected_value == PREV_PAGE_VALUE:
                new_page = max(page - 1, 0)
                session.pagination[pkey] = new_page
                return self._render_list_page(node, session, items, new_page, interactive)
            if selected_value == NEXT_PAGE_VALUE:
                new_page = min(page + 1, total_pages - 1)
                session.pagination[pkey] = new_page
                return self._render_list_page(node, session, items, new_page, interactive)

            # Extra options: they have their own target
            extra_opts = node.get("extra_options", [])
            for opt in extra_opts:
                opt_value = opt.get("value", opt.get("next", ""))
                if selected_value == opt_value:
                    target = opt.get("next", tree.entry)
                    self._transition_to(session, target)
                    return await self._enter_node(session, tree)

            # Normal item selection: value is "list_idx_{abs_index}"
            try:
                abs_idx = int(selected_value.split("_")[-1])
                if 0 <= abs_idx < len(items):
                    selected = items[abs_idx]
                    session.context["selected_item"] = selected
                    session.context["selected_index"] = abs_idx
                    session.pagination.pop(pkey, None)
                    on_select = node.get("on_select", tree.entry)
                    self._transition_to(session, on_select)
                    return await self._enter_node(session, tree)
            except (ValueError, IndexError):
                pass

            # Invalid selection – re‑render
            return self._render_list_page(node, session, items, page, interactive)

        # ── plain text pagination commands ─────────────────────────────
        if not interactive:
            if raw in NEXT_PAGE_KEYWORDS:
                page = min(page + 1, total_pages - 1)
                session.pagination[pkey] = page
                return self._render_list_page(node, session, items, page, interactive)
            if raw in PREV_PAGE_KEYWORDS:
                page = max(page - 1, 0)
                session.pagination[pkey] = page
                return self._render_list_page(node, session, items, page, interactive)

            # Plain text item selection by digit
            if raw.isdigit():
                start = page * page_size
                page_items = items[start: start + page_size]
                local_idx = int(raw) - 1
                if 0 <= local_idx < len(page_items):
                    selected = page_items[local_idx]
                    abs_index = items.index(selected)
                    session.context["selected_item"] = selected
                    session.context["selected_index"] = abs_index
                    session.pagination.pop(pkey, None)
                    on_select = node.get("on_select", tree.entry)
                    self._transition_to(session, on_select)
                    return await self._enter_node(session, tree)

            # Invalid input – re‑render with error
            rendered = self._render_list_page(node, session, items, page, interactive)
            return Reply(
                type="text",
                body="Invalid selection.\n\n" + rendered.body,
                phone=session.user_id,
                options=rendered.options,
                node_type="list",
                current_node=session.current_node,
            )

        # Fallback (should not reach)
        return self._error(session, "Unknown list state.")

    # ── rendering ───────────────────────────────────────────────────────

    def _render_list_page(
        self,
        node: Dict[str, Any],
        session: Session,
        items: List[Any],
        page: int,
        interactive: bool,
    ) -> Reply:
        if not items:
            empty_text = node.get("empty_text", "No items available.")
            if interactive:
                # Interactive empty state: just a text message
                return Reply(
                    type="text",
                    body=f"{node.get('title', 'List')}\n\n{empty_text}",
                    phone=session.user_id,
                    node_type="list",
                    current_node=session.current_node,
                )
            else:
                return Reply(
                    type="text",
                    body=f"{node.get('title', 'List')}\n\n{empty_text}\n\nReply 0 to go back.",
                    phone=session.user_id,
                    node_type="list",
                    current_node=session.current_node,
                )

        page_size = node.get("page_size", 5)
        total_pages = max(1, (len(items) + page_size - 1) // page_size)
        page = max(0, min(page, total_pages - 1))
        start = page * page_size
        page_items = items[start: start + page_size]

        title = node.get("title", "Select an option")
        item_label_fn = node.get("item_label", str)
        item_desc_fn = node.get("item_description")

        if interactive:
            # Build interactive list options
            rows = []
            for local_i, item in enumerate(page_items, 1):
                abs_i = start + local_i - 1
                label = item_label_fn(item)
                desc = item_desc_fn(item) if item_desc_fn else ""
                rows.append({
                    "id": f"list_idx_{abs_i}",
                    "title": label[:24],
                    "description": desc[:72],
                })

            # Pagination controls as interactive options
            if page > 0:
                rows.append({
                    "id": PREV_PAGE_VALUE,
                    "title": "⬅️ Previous Page",
                    "description": f"Page {page}/{total_pages}",
                })
            if page < total_pages - 1:
                rows.append({
                    "id": NEXT_PAGE_VALUE,
                    "title": "➡️ Next Page",
                    "description": f"Page {page + 2}/{total_pages}",
                })

            # Extra static options
            extra_opts = node.get("extra_options", [])
            for opt in extra_opts:
                rows.append({
                    "id": opt.get("value", opt.get("next", "")),
                    "title": opt.get("label", "")[:24],
                    "description": opt.get("description", "")[:72],
                })

            # Determine custom button label from node or default "Options"
            button_label = node.get("button_label", "Options")
            # Build the reply for interactive list with meta
            reply = Reply(
                type="text",  # Will be converted to interactive by send_whatsapp
                body=title,
                phone=session.user_id,
                node_type="menu",  # Trigger interactive list rendering
                options=[ReplyOption(
                    label=r["title"],
                    value=r["id"],
                    description=r.get("description", ""),
                ) for r in rows],
                current_node=session.current_node,
                meta={"button_label": button_label},
            )
            # Add page info to body
            if total_pages > 1:
                reply.body = f"{title} (Page {page + 1}/{total_pages})"
            return reply

        else:
            # Plain text rendering (original behaviour)
            lines = [title, f"(Page {page + 1}/{total_pages})", ""]
            reply_options = []
            for local_i, item in enumerate(page_items, 1):
                abs_i = start + local_i - 1
                label = item_label_fn(item)
                desc = item_desc_fn(item) if item_desc_fn else ""
                lines.append(f"{local_i}. {label}")
                if desc:
                    lines.append(f"   {desc}")
                reply_options.append(ReplyOption(
                    label=label,
                    value=f"list_idx_{abs_i}",
                    description=desc,
                ))
            lines.append("")
            nav = []
            if page > 0:              nav.append("p=prev")
            if page < total_pages - 1: nav.append("n=next")
            nav.append("0=back")
            lines.append(" · ".join(nav))

            return Reply(
                type="text",
                body="\n".join(lines),
                phone=session.user_id,
                options=reply_options,
                node_type="list",
                suggested_replies=[o.value for o in reply_options],
                current_node=session.current_node,
            )