"""
turnstack.nodes
===============
Typed node classes — the developer-facing API for building flow trees.

Every node serialises to a plain dict internally so the engine never changes,
but developers get full IDE autocomplete, type checking, and clear docstrings.

Usage::

    from turnstack.nodes import Menu, Input, Action, Confirm, Router, List, Option, Field, Route

    tree.add("welcome", Menu(
        text="Welcome! What would you like to do?",
        options=[
            Option("Register as Landlord", next="register_start"),
            Option("Learn More",           next="learn_more"),
        ]
    ))

    tree.add("register_start", Input(
        prompt="What is your first name?",
        field="first_name",
        next="register_last_name",
        validate=validators.name,
    ))

    tree.add("entry", Router(
        routes=[
            Route(when=lambda s: s.context.get("user") is not None, next="home"),
            Route(when=lambda s: s.context.get("invitation"),  next="onboarding"),
        ],
        default="public_welcome",
    ))
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union


# ── helpers ───────────────────────────────────────────────────────────────────

NodeDict = Dict[str, Any]


def _to_dict(node: "BaseNode") -> NodeDict:
    """Recursively serialise a node object to a plain dict the engine uses."""
    if isinstance(node, BaseNode):
        return node.to_dict()
    return node  # already a dict (legacy support)


# ── option / field / route helpers ───────────────────────────────────────────

@dataclass
class Option:
    """
    A single choice in a Menu or Confirm node.

    Args:
        label:       Text shown to the user (truncated to 24 chars on WhatsApp buttons).
        next:        Node key to navigate to when this option is selected.
        description: Optional subtitle shown in list-style interactive menus (max 72 chars).
        value:       Optional machine-readable value stored in session.context["last_option"].
                     Defaults to the `next` key if not set.
    """
    label: str
    next: str
    description: str = ""
    value: Optional[str] = None

    def to_dict(self) -> Dict[str, str]:
        d: Dict[str, Any] = {"label": self.label, "next": self.next}
        if self.description:
            d["description"] = self.description
        d["value"] = self.value if self.value is not None else self.next
        return d


@dataclass
class Field:
    """
    A single input field inside a MultiInput node.

    Args:
        name:      Key under which the collected value is stored in session.collected.
        prompt:    Question shown to the user for this field.
        validate:  Optional callable ``(value: str) -> Optional[str]``.
                   Return an error string to reject, or None to accept.
        transform: Optional callable ``(value: str) -> Any`` applied before storing.
    """
    name: str
    prompt: str
    validate: Optional[Callable[[str], Optional[str]]] = None
    transform: Optional[Callable[[str], Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"name": self.name, "prompt": self.prompt}
        if self.validate:
            d["validate"] = self.validate
        if self.transform:
            d["transform"] = self.transform
        return d


@dataclass
class Route:
    """
    A single conditional branch inside a Router node.

    Args:
        when: Callable ``(session: Session) -> bool``.
              The router evaluates conditions in order and takes the first True branch.
        next: Node key to navigate to when the condition is True.
    """
    when: Callable[["Session"], bool]  # type: ignore[name-defined]
    next: str

    def to_dict(self) -> Dict[str, Any]:
        return {"when": self.when, "next": self.next}


# ── base ──────────────────────────────────────────────────────────────────────

class BaseNode:
    """All node classes inherit from this. Provides .to_dict() serialisation."""

    def to_dict(self) -> NodeDict:  # pragma: no cover
        raise NotImplementedError


# ── node classes ──────────────────────────────────────────────────────────────

@dataclass
class Menu(BaseNode):
    """
    Show the user a numbered list of options.

    The engine renders this as a WhatsApp interactive list message.
    Developers should prefer interactive messages over numbered text where possible.

    Args:
        text:          Message body shown above the options.
        options:       List of :class:`Option` objects.
        allow_numeric: If True, also accept plain digit input ("1", "2" …).
                       Useful for fallback SMS / terminal testing.
                       Default: False (interactive-only).
        header:        Optional header line shown in the interactive list.
        footer:        Optional footer line shown in the interactive list.
        button_label:  Custom label for the interactive list button
                       (e.g., "Choose Action"). Default: "Options".
    """
    text: str
    options: List[Option]
    allow_numeric: bool = False
    header: str = ""
    footer: str = ""
    button_label: str = "Options"

    def to_dict(self) -> NodeDict:
        return {
            "type": "menu",
            "text": self.text,
            "options": [o.to_dict() for o in self.options],
            "allow_numeric": self.allow_numeric,
            "header": self.header,
            "footer": self.footer,
            "button_label": self.button_label,
        }


@dataclass
class Input(BaseNode):
    """
    Prompt the user for a single piece of free-text input.

    Args:
        prompt:    Question shown to the user.
        field:     Key under which the value is stored in ``session.collected``.
        next:      Node key to navigate to after valid input is received.
        validate:  Optional callable ``(value: str) -> Optional[str]``.
                   Return an error string to reject, or None to accept.
        transform: Optional callable ``(value: str) -> Any`` applied before storing.
                   Example: ``str.strip``, ``str.lower``, ``lambda v: v.title()``.
        placeholder: Hint shown alongside the prompt (not sent to WhatsApp, used in UI sims).
    """
    prompt: str
    field: str
    next: str
    validate: Optional[Callable[[str], Optional[str]]] = None
    transform: Optional[Callable[[str], Any]] = None
    placeholder: str = ""

    def to_dict(self) -> NodeDict:
        d: NodeDict = {
            "type": "input",
            "prompt": self.prompt,
            "field": self.field,
            "next": self.next,
            "placeholder": self.placeholder,
        }
        if self.validate:
            d["validate"] = self.validate
        if self.transform:
            d["transform"] = self.transform
        return d


@dataclass
class MultiInput(BaseNode):
    """
    Collect several fields in sequence, owned by a single logical form node.

    The engine walks through ``fields`` one at a time, storing each value in
    ``session.collected``, then advances to ``next`` when all fields are filled.

    This is cleaner than chaining many :class:`Input` nodes for long forms.

    Args:
        fields: Ordered list of :class:`Field` objects.
        next:   Node key to navigate to after all fields are collected.
        intro:  Optional message shown once when the user first enters this node.
    """
    fields: List[Field]
    next: str
    intro: str = ""

    def to_dict(self) -> NodeDict:
        return {
            "type": "multi_input",
            "fields": [f.to_dict() for f in self.fields],
            "next": self.next,
            "intro": self.intro,
        }


@dataclass
class Confirm(BaseNode):
    """
    Show a summary and ask the user to confirm before committing a write action.

    Args:
        text:    Summary text. Can be a plain string or a callable
                 ``(collected: dict) -> str`` that receives the current
                 ``session.collected`` so you can interpolate collected values.
        options: List of :class:`Option` objects (typically Yes / Edit / Cancel).
                 Rendered as WhatsApp reply buttons (**max 3**).
        allow_numeric: Also accept digit input. Default: False.
    """
    text: Union[str, Callable[[Dict[str, Any]], str]]
    options: List[Option]
    allow_numeric: bool = False

    def __post_init__(self):
        if len(self.options) > 3:
            raise ValueError(
                f"Confirm node can have at most 3 options (WhatsApp button limit), got {len(self.options)}."
            )

    def to_dict(self) -> NodeDict:
        return {
            "type": "confirm",
            "text": self.text,
            "options": [o.to_dict() for o in self.options],
            "allow_numeric": self.allow_numeric,
        }


@dataclass
class Action(BaseNode):
    """
    Execute a side-effect function (save to DB, send notification, etc.)
    and advance to the next node.

    The function receives ``(session, collected)`` and must return a string
    that is sent to the user, OR a :class:`~turnstack.reply.Reply` object
    for full control (e.g. to send a media file).

    Args:
        fn:   Callable ``(session: Session, collected: dict) -> str | Reply``.
        next: Node key to navigate to after the action completes.
              Use ``"__end__"`` to terminate the session cleanly.
    """
    fn: Callable[..., Any]
    next: str = "welcome"

    def to_dict(self) -> NodeDict:
        return {"type": "action", "fn": self.fn, "next": self.next}


@dataclass
class Router(BaseNode):
    """
    Silently branch to different nodes based on session state — no user input required.

    The engine evaluates ``routes`` in order and follows the first truthy condition.
    Falls back to ``default`` if no condition matches.

    This is the entry-point node type: one phone number arriving → check DB →
    route to landlord home, tenant home, invited-user onboarding, or public welcome.

    Args:
        routes:  Ordered list of :class:`Route` objects.
        default: Node key used when no route condition matches.
        before:  Optional callable ``(session) -> None`` run before evaluation
                 (useful for loading user data into ``session.context``, e.g. ``session.context["user"]``).
    """
    routes: List[Route]
    default: str
    before: Optional[Callable[..., Any]] = None

    def to_dict(self) -> NodeDict:
        return {
            "type": "router",
            "routes": [r.to_dict() for r in self.routes],
            "default": self.default,
            "before": self.before,
        }


@dataclass
class ListNode(BaseNode):
    """
    Render a dynamic list of items fetched at runtime (from DB, API, etc.)
    with automatic pagination.

    Two modes:
    - Simple: `fetch(session)` returns a full list of all items.
    - Paginated: `fetch(session, page, page_size)` returns either:
        * a tuple (items_for_page, total_count)
        * a dict {"items": [...], "total": N}
      In this mode, the engine will not fetch all items, only the current page.

    When ``interactive=True``, the list is sent as a WhatsApp interactive
    list message. Pagination buttons appear automatically and consume only
    as many rows as needed (1 for first/last page, 2 for middle pages).
    Extra options are shown **only on the last page** if space permits.

    Args:
        fetch:           Callable. In simple mode: ``(session) -> list``.
                         In paginated mode: ``(session, page, page_size) -> (list, int) | dict``.
        item_label:      Callable ``(item: Any) -> str`` for display label.
        on_select:       Node to go to when an item is selected.
        title:           Heading shown above the list (no automatic page number).
        empty_text:      Text shown when the list is empty.
        item_description: Optional subtitle callable.
        extra_options:   Static :class:`Option` list (max 3). Shown only on the last page.
        interactive:     If True, render as interactive list; else plain text.
        button_label:    Custom label for the interactive list button.
        page_size:       Number of items per page (default: 8, but may be adjusted
                         to fit WhatsApp limit). If paginated fetch is used, this is
                         passed to the fetch function.
    """
    fetch: Callable[..., Any]  # signature depends on mode
    item_label: Callable[[Any], str]
    on_select: str
    title: str = "Select an option"
    empty_text: str = "No items available."
    item_description: Optional[Callable[[Any], str]] = None
    extra_options: List[Option] = field(default_factory=list)
    interactive: bool = False
    button_label: str = "Options"
    page_size: int = 8  # default, but will be clamped

    def __post_init__(self):
        if self.interactive and len(self.extra_options) > 3:
            raise ValueError(
                f"ListNode interactive mode supports at most 3 extra_options, got {len(self.extra_options)}."
            )
        # Page size cannot exceed 10 (WhatsApp limit) and cannot be < 1
        if self.page_size < 1:
            self.page_size = 1
        if self.page_size > 10:
            self.page_size = 10

    def to_dict(self) -> NodeDict:
        return {
            "type": "list",
            "fetch": self.fetch,
            "item_label": self.item_label,
            "on_select": self.on_select,
            "title": self.title,
            "empty_text": self.empty_text,
            "item_description": self.item_description,
            "extra_options": [opt.to_dict() for opt in self.extra_options],
            "interactive": self.interactive,
            "button_label": self.button_label,
            "page_size": self.page_size,
        }


@dataclass
class MediaReply(BaseNode):
    """
    Send a file (PDF, CSV, image, etc.) to the user, then continue the flow.

    The engine calls ``generate(session, collected)`` to get the file bytes,
    sets ``Reply.type = "media"``, and then advances to ``next``.

    The developer's WhatsApp adapter is responsible for actually sending the file
    using their provider's media API.

    Args:
        generate:  Callable ``(session, collected) -> bytes``.
                   Must return the raw file bytes.
        filename:  Default filename sent with the file (e.g. ``"report_june.pdf"``).
                   Can be a callable ``(session, collected) -> str`` for dynamic names.
        mime_type: MIME type string, e.g. ``"application/pdf"``, ``"text/csv"``,
                   ``"image/png"``.
        caption:   Optional text message sent alongside the file.
                   Can be a callable ``(session, collected) -> str``.
        next:      Node key to navigate to after sending the file.
    """
    generate: Callable[..., bytes]
    filename: Union[str, Callable[..., str]]
    mime_type: str
    caption: Union[str, Callable[..., str]] = ""
    next: str = "welcome"

    def to_dict(self) -> NodeDict:
        return {
            "type": "media",
            "generate": self.generate,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "caption": self.caption,
            "next": self.next,
        }


# ── convenience re-exports so developers only need one import ─────────────────

__all__ = [
    "Option",
    "Field",
    "Route",
    "Menu",
    "Input",
    "MultiInput",
    "Confirm",
    "Action",
    "Router",
    "ListNode",
    "MediaReply",
    "NodeDict",
]