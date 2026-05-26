"""
turnstack.handlers.input
========================
InputHandler — processes ``input`` nodes containing typed fields.

Supported field types
---------------------
- ``text``     (:class:`~turnstack.nodes.Field`)
  Plain text prompt → accepts any text reply.

- ``menu``     (:class:`~turnstack.nodes.MenuField`)
  Interactive list prompt → accepts ``interactive_id`` (list_reply)
  or numeric digit fallback when ``allow_numeric=True``.

- ``buttons``  (:class:`~turnstack.nodes.ButtonsField`)
  Interactive button prompt → accepts ``interactive_id`` (button_reply)
  or numeric digit fallback when ``allow_numeric=True``.
  The stored value is the selected option's ``value``.

- ``image``    (:class:`~turnstack.nodes.ImageField`)
  Text prompt → accepts ``message.type == "image"``.
  Rejects everything else with the field's ``rejection_text``.
  Stored value: ``{"media_id": str, "mime_type": str}``.

- ``document`` (:class:`~turnstack.nodes.DocumentField`)
  Text prompt → accepts ``message.type == "document"``.
  Optional MIME filtering via ``accept`` list.
  Rejects everything else with the field's ``rejection_text``.
  Stored value: ``{"media_id": str, "mime_type": str, "filename": str}``.

- ``location`` (:class:`~turnstack.nodes.LocationField`)
  Location-request prompt → accepts ``message.type == "location"``.
  Rejects everything else with the field's ``rejection_text``.
  Stored value: ``{"latitude": float, "longitude": float,
                   "name": str|None, "address": str|None}``.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from ..message import IncomingMessage
from ..reply import Reply, ReplyOption
from ..session import Session
from .base import NodeHandler

if TYPE_CHECKING:
    from ..tree import FlowTree

# session.pagination key for the current field index inside an Input node
_IDX_KEY_TMPL  = "mi_{node}_idx"
_MENU_PAGE_TMPL = "mi_{node}_f{field}_pg"   # per-field menu page state
_MAX_MENU_ROWS  = 10
_MENU_PREV      = "__mf_prev__"
_MENU_NEXT      = "__mf_next__"


class InputHandler(NodeHandler):
    """
    Handles ``input`` nodes.

    Walks through ``fields`` one at a time.  For each field it:
    1. Renders the appropriate prompt (text / interactive list / buttons /
       location request).
    2. On the next message, checks the *incoming type* matches the field type.
    3. Validates the value (optional ``validate`` callable on the field).
    4. Transforms and stores the value in ``session.collected``.
    5. Advances to the next field or, when all are done, transitions to
       ``node["next"]``.
    """

    async def handle(
        self,
        node: Dict[str, Any],
        session: Session,
        message: IncomingMessage,
        tree: "FlowTree",
    ) -> Reply:
        fields  = node.get("fields", [])
        idx_key = _IDX_KEY_TMPL.format(node=session.current_node)
        idx     = session.pagination.get(idx_key, 0)

        # Guard: all fields somehow already collected
        if idx >= len(fields):
            session.pagination.pop(idx_key, None)
            self._transition_to(session, node["next"])
            return await self._enter_node(session, tree)

        current_field = fields[idx]
        field_type    = current_field.get("field_type", "text")

        # ── first entry (no meaningful input yet) ───────────────────
        # Reset only on a genuine cold entry (no idx tracked yet).
        # When the engine's back-nav has already decremented the idx,
        # we must NOT reset — we want to re-render that specific field.
        if _is_blank(message):
            if idx_key not in session.pagination:
                self._reset_input(session, node, fields)
                idx = 0
                # Advance past any leading skip_if fields on fresh entry
                while idx < len(fields):
                    skip_if = fields[idx].get("skip_if")
                    if skip_if and skip_if(session):
                        session.collected[fields[idx]["name"]] = None
                        idx += 1
                        session.pagination[idx_key] = idx
                    else:
                        break
            else:
                # Back-nav: engine already decremented idx by 1.
                # Walk backwards further past any skip_if fields that were
                # never actually shown (auto-skipped).
                idx = _skip_backwards(fields, idx, session)
                session.pagination[idx_key] = idx
            return self._render_field(node, session, fields, idx)

        # ── guard: never store navigation keywords as field values ───
        # This catches cases where the engine's global-command interception
        # is bypassed (e.g. duplicate webhook delivery, direct handler call).
        # Only applies to plain-text messages; interactive replies are safe.
        if message.type == "text" and message.text:
            cmd = message.text.strip().lower()
            _BACK  = {"0", "back", "go back"}
            _HOME  = {"00", "home", "menu", "start over"}
            _EXIT  = {"000", "exit", "quit", "reset", "goodbye", "bye"}
            if cmd in _BACK or cmd in _HOME or cmd in _EXIT:
                # Engine already decremented idx before reaching here.
                # Walk backwards past any skip_if fields never shown.
                idx = _skip_backwards(fields, idx, session)
                session.pagination[idx_key] = idx
                return self._render_field(node, session, fields, idx)

        # ── try to accept the incoming message for this field ─────────
        value, error = self._accept(current_field, field_type, message, session, idx)

        if error:
            # Pagination sentinels from MenuField — just re-render the field
            if error in (_MENU_PREV, _MENU_NEXT):
                return self._render_field(node, session, fields, idx)
            # Wrong type or failed validation → re-prompt with error prefix
            prompt_reply = self._render_field(node, session, fields, idx)
            prompt_reply.body = f"{error}\n\n{prompt_reply.body}"
            return prompt_reply

        # ── apply transform ───────────────────────────────────────────
        transform = current_field.get("transform")
        if transform:
            value = transform(value)

        # ── store ─────────────────────────────────────────────────────
        session.collected[current_field["name"]] = value
        idx += 1
        session.pagination[idx_key] = idx

        # ── skip fields whose condition is met ────────────────────────
        # Evaluate skip_if on every subsequent field in order; for each
        # skipped field store None so downstream code can still key on it.
        while idx < len(fields):
            skip_if = fields[idx].get("skip_if")
            if skip_if and skip_if(session):
                session.collected[fields[idx]["name"]] = None
                idx += 1
                session.pagination[idx_key] = idx
            else:
                break

        # ── advance to next field or finish ───────────────────────────
        if idx >= len(fields):
            session.pagination.pop(idx_key, None)
            self._transition_to(session, node["next"])
            return await self._enter_node(session, tree)

        return self._render_field(node, session, fields, idx)

    # ── reset helper ─────────────────────────────────────────────────

    def _reset_input(
        self,
        session: Session,
        node: Dict[str, Any],
        fields: List[Dict[str, Any]],
    ) -> None:
        """
        Wipe all state for this Input node so it starts clean.

        Clears:
        - The field index (``mi_{node}_idx``)
        - Any per-field MenuField page state (``mi_{node}_f{i}_pg``)
        - The collected values for every field defined on this node
          (leaves unrelated keys in session.collected untouched)
        """
        node_key = session.current_node
        # Field index
        session.pagination.pop(_IDX_KEY_TMPL.format(node=node_key), None)
        # Per-field menu page state
        for i in range(len(fields)):
            session.pagination.pop(_MENU_PAGE_TMPL.format(node=node_key, field=i), None)
        # Collected values for this node's fields only
        for f in fields:
            session.collected.pop(f.get("name", ""), None)

    # ── accept helpers (type-dispatch) ───────────────────────────────

    def _accept(
        self,
        f: Dict[str, Any],
        field_type: str,
        message: IncomingMessage,
        session: "Session" = None,
        field_idx: int = 0,
    ) -> Tuple[Any, Optional[str]]:
        """
        Try to extract a value from *message* for field *f*.

        Returns ``(value, None)`` on success or ``(None, error_str)`` on failure.
        For menu fields with >10 options the error string may be the sentinel
        ``_MENU_NEXT`` / ``_MENU_PREV`` — the caller re-renders instead of showing an error.
        Validation (the field's ``validate`` callable) is run here too.
        """
        if field_type == "text":
            return self._accept_text(f, message)

        if field_type == "menu":
            return self._accept_menu(f, message, session, field_idx)

        if field_type == "buttons":
            return self._accept_buttons(f, message)

        if field_type == "image":
            return self._accept_image(f, message)

        if field_type == "document":
            return self._accept_document(f, message)

        if field_type == "location":
            return self._accept_location(f, message)

        # Unknown field type — fall back to raw text
        return self._accept_text(f, message)

    def _accept_text(
        self, f: Dict[str, Any], message: IncomingMessage
    ) -> Tuple[Any, Optional[str]]:
        raw = (message.text or "").strip()
        if not raw:
            return None, f.get("rejection_text", "⚠️ Please send a text reply.")
        validate = f.get("validate")
        if validate:
            err = validate(raw)
            if err:
                return None, f"⚠️ {err}"
        return raw, None

    def _accept_menu(
        self, f: Dict[str, Any], message: IncomingMessage, session: Session, field_idx: int
    ) -> Tuple[Any, Optional[str]]:
        options       = _resolve_options(f, session)
        allow_numeric = f.get("allow_numeric", False)
        pkey          = _MENU_PAGE_TMPL.format(node=session.current_node, field=field_idx)
        items_per_page = _MAX_MENU_ROWS - 2 if len(options) > _MAX_MENU_ROWS else _MAX_MENU_ROWS
        page          = session.pagination.get(pkey, 0)
        total_pages   = max(1, (len(options) + items_per_page - 1) // items_per_page)

        # Pagination controls – handle before attempting a value match
        if message.interactive_id == _MENU_NEXT:
            if page + 1 < total_pages:
                session.pagination[pkey] = page + 1
            return None, _MENU_NEXT   # sentinel: re-render same field
        if message.interactive_id == _MENU_PREV:
            if page > 0:
                session.pagination[pkey] = page - 1
            return None, _MENU_PREV   # sentinel: re-render same field

        start   = page * items_per_page
        page_options = options[start: start + items_per_page]
        raw     = (message.interactive_id or message.text or "").strip()

        for i, opt in enumerate(page_options, start + 1):
            label = opt.get("label", "")
            value = opt.get("value", opt.get("next", label))

            if message.interactive_id and message.interactive_id == value:
                session.pagination.pop(pkey, None)
                return value, None

            if allow_numeric and (message.text or "").strip() == str(i):
                session.pagination.pop(pkey, None)
                return value, None

            if raw.lower() == label.lower():
                session.pagination.pop(pkey, None)
                return value, None

        return None, "⚠️ Please choose one of the options from the list."

    def _accept_buttons(
        self, f: Dict[str, Any], message: IncomingMessage
    ) -> Tuple[Any, Optional[str]]:
        options       = _resolve_options(f, None)
        allow_numeric = f.get("allow_numeric", False)
        raw           = (message.interactive_id or message.text or "").strip()

        for i, opt in enumerate(options, 1):
            label = opt.get("label", "")
            value = opt.get("value", opt.get("next", label))

            if message.interactive_id and message.interactive_id == value:
                return value, None

            if allow_numeric and (message.text or "").strip() == str(i):
                return value, None

            if raw.lower() == label.lower():
                return value, None

        return None, "⚠️ Please tap one of the buttons to continue."

    def _accept_image(
        self, f: Dict[str, Any], message: IncomingMessage
    ) -> Tuple[Any, Optional[str]]:
        if message.type != "image" or not message.media_id:
            return None, f.get("rejection_text", "⚠️ Please send an image.")
        value = {
            "media_id":  message.media_id,
            "mime_type": message.media_mime or "image/jpeg",
        }
        validate = f.get("validate")
        if validate:
            err = validate(value)
            if err:
                return None, f"⚠️ {err}"
        return value, None

    def _accept_document(
        self, f: Dict[str, Any], message: IncomingMessage
    ) -> Tuple[Any, Optional[str]]:
        if message.type != "document" or not message.media_id:
            return None, f.get("rejection_text", "⚠️ Please send a document file.")

        accept = f.get("accept", [])
        if accept and message.media_mime and message.media_mime not in accept:
            accepted_str = ", ".join(accept)
            return None, f"⚠️ Unsupported file type. Accepted: {accepted_str}"

        value = {
            "media_id":  message.media_id,
            "mime_type": message.media_mime or "application/octet-stream",
            "filename":  getattr(message, "media_name", "") or "",
        }
        validate = f.get("validate")
        if validate:
            err = validate(value)
            if err:
                return None, f"⚠️ {err}"
        return value, None

    def _accept_location(
        self, f: Dict[str, Any], message: IncomingMessage
    ) -> Tuple[Any, Optional[str]]:
        if message.type != "location" or not message.location:
            return None, f.get("rejection_text", "⚠️ Please share your location using the 📍 button.")

        loc = message.location
        value = {
            "latitude":  loc.get("latitude"),
            "longitude": loc.get("longitude"),
            "name":      loc.get("name"),
            "address":   loc.get("address"),
        }
        validate = f.get("validate")
        if validate:
            err = validate(value)
            if err:
                return None, f"⚠️ {err}"
        return value, None

    # ── render helpers (type-dispatch) ───────────────────────────────

    def _render_field(
        self,
        node: Dict[str, Any],
        session: Session,
        fields: List[Dict[str, Any]],
        idx: int,
    ) -> Reply:
        """Build the correct Reply for ``fields[idx]`` based on its type."""
        f          = fields[idx]
        field_type = f.get("field_type", "text")
        # Only count fields that are actually visible (no skip_if, or skip_if=False)
        visible_total = sum(
            1 for fi in fields
            if not (fi.get("skip_if") and fi["skip_if"](session))  # type: ignore[operator]
            if True  # keep the generator tidy
        )
        # Visible position of current field (1-based)
        visible_idx = sum(
            1 for fi in fields[:idx]
            if not (fi.get("skip_if") and fi["skip_if"](session))
        ) + 1
        total      = visible_total

        # Header line — "Title - Step N of M" when title is set, else plain "(N/M)"
        def _prefixed(prompt: str) -> str:
            title = node.get("title", "") or node.get("intro", "")
            if total > 1:
                step_label = f"Step {visible_idx} of {total}"
                header = f"*{title} - {step_label}*" if title else f"({visible_idx}/{total})"
            else:
                header = f"*{title}*" if title else ""
            return f"{header}\n\n{prompt}" if header else prompt

        if field_type == "text":
            return Reply(
                type="text",
                body=_prefixed(f.get("prompt", "")),
                phone=session.user_id,
                node_type="input",
                current_node=session.current_node,
            )

        if field_type == "menu":
            return self._render_menu_field(f, session, _prefixed(f.get("prompt", "")), field_idx=idx)

        if field_type == "buttons":
            return self._render_buttons_field(f, session, _prefixed(f.get("prompt", "")))

        if field_type in ("image", "document"):
            return Reply(
                type="text",
                body=_prefixed(f.get("prompt", "")),
                phone=session.user_id,
                node_type=f"input_{field_type}",   # lets adapter know what to expect
                current_node=session.current_node,
            )

        if field_type == "location":
            return Reply(
                type="text",
                body=_prefixed(f.get("prompt", "")),
                phone=session.user_id,
                node_type="input_location",   # adapter maps this to location_request_message
                current_node=session.current_node,
            )

        # Unknown type — plain text fallback
        return Reply(
            type="text",
            body=_prefixed(f.get("prompt", "")),
            phone=session.user_id,
            node_type="input",
            current_node=session.current_node,
        )

    def _render_menu_field(
        self,
        f: Dict[str, Any],
        session: Session,
        body: str,
        field_idx: int = 0,
    ) -> Reply:
        options        = _resolve_options(f, session)
        button_label   = f.get("button_label", "Options")
        items_per_page = _MAX_MENU_ROWS - 2 if len(options) > _MAX_MENU_ROWS else _MAX_MENU_ROWS
        pkey           = _MENU_PAGE_TMPL.format(node=session.current_node, field=field_idx)
        page           = session.pagination.get(pkey, 0)
        total_pages    = max(1, (len(options) + items_per_page - 1) // items_per_page)

        # Clamp page in case options shrank
        if page >= total_pages:
            page = max(0, total_pages - 1)
            session.pagination[pkey] = page

        start        = page * items_per_page
        page_options = options[start: start + items_per_page]

        reply_options = [
            ReplyOption(
                label=opt.get("label", ""),
                value=opt.get("value", opt.get("next", opt.get("label", ""))),
                description=opt.get("description", ""),
            )
            for opt in page_options
        ]

        # Pagination rows
        if total_pages > 1:
            if page > 0:
                reply_options.append(ReplyOption(
                    label="◀ Previous",
                    value=_MENU_PREV,
                    description=f"Page {page}/{total_pages}",
                ))
            if page < total_pages - 1:
                reply_options.append(ReplyOption(
                    label="Next ▶",
                    value=_MENU_NEXT,
                    description=f"Page {page + 2}/{total_pages}",
                ))

        return Reply(
            type="text",
            body=body,
            phone=session.user_id,
            options=reply_options,
            node_type="input_menu",
            current_node=session.current_node,
            meta={"button_label": button_label},
        )

    def _render_buttons_field(
        self,
        f: Dict[str, Any],
        session: Session,
        body: str,
    ) -> Reply:
        options = _resolve_options(f, session)

        reply_options = [
            ReplyOption(
                label=opt.get("label", ""),
                value=opt.get("value", opt.get("next", opt.get("label", ""))),
            )
            for opt in options
        ]
        return Reply(
            type="text",
            body=body,
            phone=session.user_id,
            options=reply_options,
            node_type="input_buttons",   # adapter renders as interactive reply buttons
            current_node=session.current_node,
        )


# ── module-level helper ───────────────────────────────────────────────────────

def _is_blank(message: IncomingMessage) -> bool:
    """True when the message carries no usable content at all."""
    return (
        not message.text
        and not message.interactive_id
        and not message.media_id
        and not message.location
    )

def _resolve_options(f: dict, session) -> list:
    """
    Return a plain list of option dicts for a menu/buttons field.

    ``options`` may be stored as:
    - a list of dicts (already serialised)          → returned as-is
    - a callable ``(session) -> list[Option|dict]`` → called now, then serialised
    """
    try:
        from ..nodes import Option  # noqa: F401 – used in isinstance check below
        _Option = Option
    except ImportError:
        _Option = None

    raw = f.get("options", [])
    if callable(raw):
        raw = raw(session)
    result = []
    for o in raw:
        if _Option and isinstance(o, _Option):
            result.append(o.to_dict())
        else:
            result.append(o)  # already a dict
    return result

def _skip_backwards(fields: list, idx: int, session) -> int:
    """
    After back-nav has decremented idx by 1, keep stepping back while the
    field at the current idx was auto-skipped (skip_if returns True).
    This ensures the user never lands on a conditional field they never saw.
    """
    while idx > 0:
        skip_if = fields[idx].get("skip_if")
        if skip_if and skip_if(session):
            idx -= 1
        else:
            break
    return idx