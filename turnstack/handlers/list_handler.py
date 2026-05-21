"""
handlers/list_handler.py
========================
ListHandler — dynamic list with optional server‑side pagination.
"""

from __future__ import annotations

import inspect
from typing import Any, Dict, List, TYPE_CHECKING

from .base import NodeHandler
from ..message import IncomingMessage
from ..reply import Reply, ReplyOption
from ..session import Session

if TYPE_CHECKING:
    from ..tree import FlowTree

NEXT_PAGE_KEYWORDS = {"n", "next", "more"}
PREV_PAGE_KEYWORDS = {"p", "prev", "previous", "back page"}

PREV_PAGE_VALUE = "__prev_page__"
NEXT_PAGE_VALUE = "__next_page__"

MAX_ROWS = 10   # WhatsApp interactive list row limit


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

        fetch = node.get("fetch")
        if not fetch:
            return self._error(session, "ListNode has no 'fetch' function.")

        page_size = node.get("page_size", 8)
        page_size = min(MAX_ROWS, max(1, page_size))

        sig = inspect.signature(fetch)
        paginated_mode = len(sig.parameters) >= 3

        pkey = f"list_{session.current_node}_page"
        page = session.pagination.get(pkey, 0)

        # ── fetch data for current page ───────────────────────────────
        if paginated_mode:
            try:
                if inspect.iscoroutinefunction(fetch):
                    result = await fetch(session, page, page_size)
                else:
                    result = fetch(session, page, page_size)
            except Exception as exc:
                return self._error(session, f"Paginated fetch raised: {exc}")

            if isinstance(result, tuple) and len(result) == 2:
                items_page, total = result
            elif isinstance(result, dict):
                items_page = result.get("items", [])
                total = result.get("total", 0)
            else:
                return self._error(session, "Paginated fetch must return (items, total) or dict with 'items' and 'total'")
            items_page = list(items_page or [])
            total_items = total
        else:
            try:
                if inspect.iscoroutinefunction(fetch):
                    all_items = await fetch(session)
                else:
                    all_items = fetch(session)
            except Exception as exc:
                return self._error(session, f"Fetch raised: {exc}")
            all_items = list(all_items or [])
            total_items = len(all_items)
            start = page * page_size
            items_page = all_items[start: start + page_size]

        total_pages = max(1, (total_items + page_size - 1) // page_size)
        if page >= total_pages:
            page = total_pages - 1 if total_pages > 0 else 0
            session.pagination[pkey] = page
            if paginated_mode and page != (session.pagination.get(pkey, 0)):
                try:
                    if inspect.iscoroutinefunction(fetch):
                        result = await fetch(session, page, page_size)
                    else:
                        result = fetch(session, page, page_size)
                except Exception as exc:
                    return self._error(session, f"Paginated fetch raised: {exc}")
                if isinstance(result, tuple):
                    items_page, total = result
                else:
                    items_page = result.get("items", [])
                items_page = list(items_page or [])

        # First render (no input)
        if not raw:
            session.pagination[pkey] = page
            return self._render_list_page(
                node, session, items_page, page, total_pages, interactive, page_size, paginated_mode
            )

        # ── handle interactive mode ─────────────────────────────────────
        if interactive:
            # Case 1: user clicked an interactive element (button or list row)
            if message.interactive_id:
                selected = message.interactive_id

                # Pagination controls
                if selected == PREV_PAGE_VALUE and page > 0:
                    session.pagination[pkey] = page - 1
                    return await self._enter_node(session, tree)
                if selected == NEXT_PAGE_VALUE and page + 1 < total_pages:
                    session.pagination[pkey] = page + 1
                    return await self._enter_node(session, tree)

                # Extra options (only shown on last page)
                extra_opts = node.get("extra_options", [])
                if page == total_pages - 1:
                    for opt in extra_opts:
                        opt_value = opt.get("value", opt.get("next", ""))
                        if selected == opt_value:
                            target = opt.get("next", tree.entry)
                            self._transition_to(session, target)
                            return await self._enter_node(session, tree)

                # Normal item selection
                if selected.startswith("list_idx_"):
                    try:
                        abs_idx = int(selected.split("_")[-1])
                        start_idx = page * page_size
                        local = abs_idx - start_idx
                        if 0 <= local < len(items_page):
                            selected_item = items_page[local]
                            session.context["selected_item"] = selected_item
                            session.context["selected_index"] = abs_idx
                            session.pagination.pop(pkey, None)
                            on_select = node.get("on_select", tree.entry)
                            self._transition_to(session, on_select)
                            return await self._enter_node(session, tree)
                    except (ValueError, IndexError):
                        pass

                # Invalid interactive selection – re‑render with error prefix
                rendered = self._render_list_page(
                    node, session, items_page, page, total_pages, interactive, page_size, paginated_mode
                )
                rendered.body = "Invalid selection.\n\n" + rendered.body
                return rendered

            # Case 2: interactive mode, but user typed plain text (not a command)
            else:
                # Plain text in interactive mode is invalid – re‑render with error prefix
                rendered = self._render_list_page(
                    node, session, items_page, page, total_pages, interactive, page_size, paginated_mode
                )
                rendered.body = "Invalid selection.\n\n" + rendered.body
                return rendered

        # ── plain text (non‑interactive) mode ──────────────────────────
        else:
            if raw in NEXT_PAGE_KEYWORDS and page + 1 < total_pages:
                session.pagination[pkey] = page + 1
                return await self._enter_node(session, tree)
            if raw in PREV_PAGE_KEYWORDS and page > 0:
                session.pagination[pkey] = page - 1
                return await self._enter_node(session, tree)
            if raw.isdigit():
                local_idx = int(raw) - 1
                if 0 <= local_idx < len(items_page):
                    selected_item = items_page[local_idx]
                    abs_idx = page * page_size + local_idx
                    session.context["selected_item"] = selected_item
                    session.context["selected_index"] = abs_idx
                    session.pagination.pop(pkey, None)
                    on_select = node.get("on_select", tree.entry)
                    self._transition_to(session, on_select)
                    return await self._enter_node(session, tree)

            # Invalid text input in plain text mode – re‑render with error prefix
            rendered = self._render_list_page(
                node, session, items_page, page, total_pages, interactive, page_size, paginated_mode
            )
            rendered.body = "Invalid selection.\n\n" + rendered.body
            return rendered

    def _render_list_page(
        self,
        node: Dict[str, Any],
        session: Session,
        items_page: List[Any],
        page: int,
        total_pages: int,
        interactive: bool,
        page_size: int,
        paginated_mode: bool,
    ) -> Reply:
        if not items_page and total_pages == 0:
            empty_text = node.get("empty_text", "No items available.")
            if interactive:
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

        title = node.get("title", "Select an option")
        item_label_fn = node.get("item_label", str)
        item_desc_fn = node.get("item_description")
        extra_opts = node.get("extra_options", [])

        if interactive:
            pagination_slots = 2 if (0 < page < total_pages - 1) else 1 if total_pages > 1 else 0
            show_extra_on_last = (page == total_pages - 1) and len(extra_opts) > 0
            extra_slots = len(extra_opts) if show_extra_on_last else 0
            max_items = MAX_ROWS - pagination_slots - extra_slots
            if max_items < 1:
                max_items = 1
            display_items = items_page[:max_items]

            sections = []

            if display_items:
                items_section = {
                    "title": node.get("items_section_title", "Options"),
                    "rows": []
                }
                start_idx = page * page_size
                for local_i, item in enumerate(display_items):
                    abs_i = start_idx + local_i
                    label = item_label_fn(item)
                    desc = item_desc_fn(item) if item_desc_fn else ""
                    items_section["rows"].append({
                        "id": f"list_idx_{abs_i}",
                        "title": label[:24],
                        "description": desc[:72],
                    })
                sections.append(items_section)

            actions_rows = []
            if total_pages > 1:
                if page > 0:
                    actions_rows.append({
                        "id": PREV_PAGE_VALUE,
                        "title": "⬅️ Previous Page",
                        "description": f"Page {page}/{total_pages}" if page > 1 else "Previous page",
                    })
                if page < total_pages - 1:
                    actions_rows.append({
                        "id": NEXT_PAGE_VALUE,
                        "title": "➡️ Next Page",
                        "description": f"Page {page+2}/{total_pages}" if page + 2 < total_pages else "Next page",
                    })
            if show_extra_on_last:
                for opt in extra_opts:
                    actions_rows.append({
                        "id": opt.get("value", opt.get("next", "")),
                        "title": opt.get("label", "")[:24],
                        "description": opt.get("description", "")[:72],
                    })
            if actions_rows:
                sections.append({
                    "title": node.get("actions_section_title", "Actions"),
                    "rows": actions_rows
                })

            button_label = node.get("button_label", "Options")
            return Reply(
                type="text",
                body=title,
                phone=session.user_id,
                node_type="menu",
                options=[],
                current_node=session.current_node,
                meta={
                    "button_label": button_label,
                    "sections": sections,
                },
            )

        else:
            # Plain text rendering
            lines = [title]
            if total_pages > 1:
                lines.append(f"(Page {page+1}/{total_pages})")
            lines.append("")
            reply_options = []
            start_idx = page * page_size
            for local_i, item in enumerate(items_page):
                abs_i = start_idx + local_i
                label = item_label_fn(item)
                desc = item_desc_fn(item) if item_desc_fn else ""
                lines.append(f"{local_i+1}. {label}")
                if desc:
                    lines.append(f"   {desc}")
                reply_options.append(ReplyOption(
                    label=label,
                    value=f"list_idx_{abs_i}",
                    description=desc,
                ))
            lines.append("")
            nav = []
            if page > 0:
                nav.append("p=prev")
            if page < total_pages - 1:
                nav.append("n=next")
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