"""
turnstack.nodes
===============
Typed node classes — the developer-facing API for building flow trees.

Every node serialises to a plain dict internally so the engine never changes,
but developers get full IDE autocomplete, type checking, and clear docstrings.

Usage::

    from turnstack.nodes import (
        Menu, Input, Action, Confirm, Router, ListNode, MediaReply,
        Option, Field, MenuField, ButtonsField, ImageField, DocumentField, LocationField,
        Route,
    )

    tree.add("register", Input(
        title="Registration",
        fields=[
            Field("name", "What is your name?"),
            MenuField("category", "Pick a category:", options=[
                Option("Housing", next="housing"),
                Option("Transport", next="transport"),
            ]),
            ButtonsField("role", "Are you a landlord or tenant?", options=[
                Option("Landlord", next="landlord"),
                Option("Tenant", next="tenant"),
            ]),
            ImageField("photo", "Please send your profile photo 📸"),
            DocumentField("id_doc", "Upload your ID document 📄"),
            LocationField("home_loc", "Share your home location 📍"),
        ],
        next="confirm_node",
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


# ── option / route helpers ────────────────────────────────────────────────────

@dataclass
class Option:
    """
    A single choice in a Menu, Confirm, MenuField, or ButtonsField.

    Args:
        label:       Text shown to the user (truncated to 24 chars on WhatsApp buttons).
        next:        Node key to navigate to when this option is selected.
                     Inside a MenuField / ButtonsField this is used only when the
                     field is the *last* field — normally the Input node advances to
                     its own ``next`` after all fields are collected.
        description: Optional subtitle shown in list-style interactive menus (max 72 chars).
        value:       Machine-readable value stored in session.collected for this field.
                     Defaults to the ``next`` key if not set.
    """
    label: str
    next: str = ""
    description: str = ""
    value: Optional[str] = None

    def to_dict(self) -> Dict[str, str]:
        d: Dict[str, Any] = {"label": self.label, "next": self.next}
        if self.description:
            d["description"] = self.description
        d["value"] = self.value if self.value is not None else (self.next or self.label)
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


# ── field base ────────────────────────────────────────────────────────────────

@dataclass
class BaseField:
    """
    Base class for all field types used inside an Input node.

    All fields share ``name``, ``validate``, and ``transform``.
    Subclasses add their type-specific rendering config.

    The ``field_type`` class variable is the string tag the InputHandler
    switches on — it must match one of the cases in ``InputHandler._render_field``.

    Note: subclasses that add positional args (e.g. ``prompt``) declare them
    directly so the dataclass field order stays intuitive for callers.

    ``skip_if`` — optional callable ``(session: Session) -> bool``.
    When provided, the InputHandler evaluates it at runtime just before
    presenting the field.  If it returns ``True`` the field is silently
    skipped and ``None`` is stored under its name in ``session.collected``.
    This lets you conditionally show follow-up fields based on earlier answers.
    """
    name: str
    skip_if: Optional[Callable[..., bool]] = field(default=None, init=True, repr=False, kw_only=True)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "field_type": getattr(self, "field_type", "text"),
            "name": self.name,
        }
        validate  = getattr(self, "validate", None)
        transform = getattr(self, "transform", None)
        skip_if   = getattr(self, "skip_if", None)
        if validate:
            d["validate"] = validate
        if transform:
            d["transform"] = transform
        if skip_if is not None:
            if not callable(skip_if):
                raise TypeError(
                    f"Field '{self.name}': skip_if must be a callable "
                    f"(e.g. lambda session: ...), got {type(skip_if).__name__!r}."
                )
            d["skip_if"] = skip_if
        return d


# ── concrete field types ──────────────────────────────────────────────────────

@dataclass
class Field(BaseField):
    """
    A plain text input field.

    The engine sends a text prompt and accepts any text reply.

    Args:
        name:      Key under which the collected value is stored in session.collected.
        prompt:    Question shown to the user for this field.
        validate:  Optional callable ``(value: str) -> Optional[str]``.
                   Return an error string to reject input, or None to accept.
        transform: Optional callable ``(value: str) -> Any`` applied before storing.
    """
    prompt: str = ""
    validate: Optional[Callable[[Any], Optional[str]]] = None
    transform: Optional[Callable[[Any], Any]] = None
    field_type: str = field(default="text", init=False, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["prompt"] = self.prompt
        return d


# Alias so existing code using Field(name, prompt, ...) keeps working
TextField = Field


@dataclass
class MenuField(BaseField):
    """
    A list-style interactive selection field inside an Input node.

    Renders as a WhatsApp interactive list message. The user picks one
    option; its ``value`` (or ``label`` as fallback) is stored in
    session.collected under ``name``.

    Args:
        name:         Key under which the selected value is stored.
        prompt:       Question / body text shown above the list.
        options:      List of :class:`Option` objects.
        button_label: Custom label for the interactive list open button.
                      Default: "Options".
        header:       Optional header line.
        footer:       Optional footer line.
        allow_numeric: Also accept "1", "2" … digit input as fallback.
    """
    prompt: str = ""
    options: Union[List[Option], Callable[..., List[Option]]] = field(default_factory=list)
    button_label: str = "Options"
    header: str = ""
    footer: str = ""
    allow_numeric: bool = False
    validate: Optional[Callable[[Any], Optional[str]]] = None
    transform: Optional[Callable[[Any], Any]] = None
    field_type: str = field(default="menu", init=False, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["prompt"] = self.prompt
        # options may be a static list OR a callable resolved at render time
        d["options"] = self.options   # kept as-is; InputHandler resolves callables
        d["button_label"] = self.button_label
        d["header"] = self.header
        d["footer"] = self.footer
        d["allow_numeric"] = self.allow_numeric
        return d


@dataclass
class ButtonsField(BaseField):
    """
    An interactive button selection field inside an Input node.

    Renders as WhatsApp reply buttons (max 3 buttons). The user taps one;
    its ``value`` is stored in session.collected under ``name``.

    Args:
        name:         Key under which the selected value is stored.
        prompt:       Question / body text shown with the buttons.
        options:      List of :class:`Option` objects (max 3).
        allow_numeric: Also accept "1" / "2" / "3" digit input as fallback.
    """
    prompt: str = ""
    options: List[Option] = field(default_factory=list)
    allow_numeric: bool = False
    validate: Optional[Callable[[Any], Optional[str]]] = None
    transform: Optional[Callable[[Any], Any]] = None
    field_type: str = field(default="buttons", init=False, repr=False)

    def __post_init__(self):
        if len(self.options) > 3:
            raise ValueError(
                f"ButtonsField '{self.name}' has {len(self.options)} options — "
                "WhatsApp interactive buttons support at most 3."
            )

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["prompt"] = self.prompt
        d["options"] = [o.to_dict() for o in self.options]
        d["allow_numeric"] = self.allow_numeric
        return d


@dataclass
class ImageField(BaseField):
    """
    A media input field that waits for the user to send an image.

    The engine rejects any non-image message with ``rejection_text`` until
    a valid image is received. The collected value is a dict::

        {
            "media_id":   str,   # WhatsApp media ID
            "mime_type":  str,   # e.g. "image/jpeg"
        }

    Args:
        name:           Key under which the collected dict is stored.
        prompt:         Message asking the user to send their image.
        rejection_text: Message shown when the user sends the wrong type.
    """
    prompt: str = ""
    rejection_text: str = "⚠️ Please send an image (photo)."
    validate: Optional[Callable[[Any], Optional[str]]] = None
    transform: Optional[Callable[[Any], Any]] = None
    field_type: str = field(default="image", init=False, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["prompt"] = self.prompt
        d["rejection_text"] = self.rejection_text
        return d


@dataclass
class DocumentField(BaseField):
    """
    A media input field that waits for the user to send a document.

    The engine rejects any non-document message with ``rejection_text`` until
    a valid document is received. Optionally filter by ``accept`` MIME types.

    The collected value is a dict::

        {
            "media_id":   str,   # WhatsApp media ID
            "mime_type":  str,   # e.g. "application/pdf"
            "filename":   str,   # original filename (may be empty)
        }

    Args:
        name:           Key under which the collected dict is stored.
        prompt:         Message asking the user to send their document.
        accept:         Optional list of accepted MIME types,
                        e.g. ``["application/pdf", "image/jpeg"]``.
                        If empty, any document is accepted.
        rejection_text: Message shown when the user sends the wrong type.
    """
    prompt: str = ""
    accept: List[str] = field(default_factory=list)
    rejection_text: str = "⚠️ Please send a document file."
    validate: Optional[Callable[[Any], Optional[str]]] = None
    transform: Optional[Callable[[Any], Any]] = None
    field_type: str = field(default="document", init=False, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["prompt"] = self.prompt
        d["accept"] = list(self.accept)
        d["rejection_text"] = self.rejection_text
        return d


@dataclass
class LocationField(BaseField):
    """
    A location input field that sends a location-request message and waits
    for the user to share their location.

    The engine rejects any non-location message with ``rejection_text``.

    The collected value is a dict::

        {
            "latitude":  float,
            "longitude": float,
            "name":      str | None,
            "address":   str | None,
        }

    Args:
        name:               Key under which the collected dict is stored.
        prompt:             Text shown alongside the "Send Location" button.
                            On WhatsApp, this becomes the body of a
                            ``interactive.type = "location_request_message"``.
        rejection_text:     Message shown when the user sends something other
                            than a location.
    """
    prompt: str = "Please share your location 📍"
    rejection_text: str = "⚠️ Please use the 📍 button to share your location."
    validate: Optional[Callable[[Any], Optional[str]]] = None
    transform: Optional[Callable[[Any], Any]] = None
    field_type: str = field(default="location", init=False, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["prompt"] = self.prompt
        d["rejection_text"] = self.rejection_text
        return d


@dataclass
class BranchField:
    """
    A conditional group of fields inside an Input node.

    Acts like a :class:`Route` but for fields — when ``when(session)`` returns
    ``True`` the enclosed fields are injected into the active field sequence at
    that position; when it returns ``False`` they are silently skipped.

    Unlike ``skip_if`` (which marks individual fields), ``BranchField`` groups
    a whole set of related fields under a single condition, keeping the form
    definition readable and logically organised.

    Multiple ``BranchField`` blocks can share the same branch point (e.g. one
    for each value of an earlier MenuField), giving you Router-style branching
    inside a single Input node.

    ``BranchField`` objects are *not* ``BaseField`` subclasses — they carry no
    ``name`` of their own. The InputHandler resolves them at runtime by
    flattening the active field list before processing each step.

    Args:
        when:   Callable ``(session: Session) -> bool``.
                Evaluated once, just before the first field in the branch
                would be presented.  The full ``session`` (including
                ``session.collected`` populated so far) is available.
        fields: Ordered list of field objects to inject when ``when`` is True.
                Any mix of :class:`Field`, :class:`MenuField`,
                :class:`ButtonsField`, :class:`ImageField`,
                :class:`DocumentField`, :class:`LocationField`, or even
                nested :class:`BranchField` objects.

    Example::

        Input(
            title="Property Registration",
            fields=[
                MenuField("property_type", "What type of property?", options=[
                    Option("🏠 Residential", value="residential"),
                    Option("🏢 Commercial",  value="commercial"),
                ]),

                BranchField(
                    when=lambda s: s.collected.get("property_type") == "residential",
                    fields=[
                        Field("num_bedrooms", "How many bedrooms?"),
                        ButtonsField("has_parking", "Does it have parking?", options=[
                            Option("✅ Yes", value="yes"),
                            Option("❌ No",  value="no"),
                        ]),
                    ],
                ),

                BranchField(
                    when=lambda s: s.collected.get("property_type") == "commercial",
                    fields=[
                        Field("floor_area", "What is the floor area (sqm)?"),
                        Field("zoning",     "What is the zoning class?"),
                    ],
                ),

                Field("asking_price", "What is the asking price (Ksh)?"),
            ],
            next="confirm_property",
        )
    """
    when: Callable[..., bool]
    fields: List[Any]   # List[BaseField | BranchField]

    field_type: str = field(default="branch", init=False, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field_type": "branch",
            "when": self.when,
            "fields": [
                f.to_dict() if hasattr(f, "to_dict") else f
                for f in self.fields
            ],
        }


# ── top-level node classes ────────────────────────────────────────────────────

@dataclass
class Menu(BaseNode):
    """
    Show the user a numbered list of options.

    The engine renders this as a WhatsApp interactive list message.

    Args:
        text:          Message body shown above the options.
        options:       List of :class:`Option` objects.
        allow_numeric: If True, also accept plain digit input ("1", "2" …).
        header:        Optional header line shown in the interactive list.
        footer:        Optional footer line shown in the interactive list.
        button_label:  Custom label for the interactive list button. Default: "Options".
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
    Collect several fields in sequence, owned by a single logical form node.

    Each field can be a different type — text, interactive list, buttons,
    image, document, or location. The engine walks through ``fields`` one at a
    time, sends the appropriate prompt, validates the response, stores the value
    in ``session.collected``, then advances to ``next`` when all fields are done.

    Supported field types:
    - :class:`Field` / :class:`TextField`  — plain text answer
    - :class:`MenuField`                   — interactive list selection
    - :class:`ButtonsField`                — interactive button selection (≤3)
    - :class:`ImageField`                  — waits for an image
    - :class:`DocumentField`               — waits for a document
    - :class:`LocationField`               — waits for a shared location

    Args:
        fields: Ordered list of field objects (any mix of types above).
        next:   Node key to navigate to after all fields are collected.
        title:  Optional flow title shown on every step as "Title - Step N of M".
                If omitted, only the bare "(N/M)" counter is shown.
        intro:  Deprecated alias for ``title``. If both are set, ``title`` wins.
    """
    fields: List[BaseField]
    next: str
    title: str = ""
    intro: str = ""   # kept for backward compatibility — maps to title if title is blank

    def to_dict(self) -> NodeDict:
        effective_title = self.title or self.intro   # title wins; fall back to intro
        return {
            "type": "input",
            "fields": [f.to_dict() for f in self.fields],
            "next": self.next,
            "title": effective_title,
        }


@dataclass
class Confirm(BaseNode):
    """
    Show a summary and ask the user to confirm before committing a write action.

    Args:
        text:    Summary text. Can be a plain string or a callable
                 ``(collected: dict) -> str``.
        options: List of :class:`Option` objects (max 3, WhatsApp button limit).
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
    Execute a side-effect function and advance to the next node.

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

    Args:
        routes:  Ordered list of :class:`Route` objects.
        default: Node key used when no route condition matches.
        before:  Optional callable ``(session) -> None`` run before evaluation.
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
    Render a dynamic list of items fetched at runtime with automatic pagination.

    Args:
        fetch:            Callable. Simple: ``(session) -> list``.
                          Paginated: ``(session, page, page_size) -> (list, int) | dict``.
        item_label:       Callable ``(item: Any) -> str`` for display label.
        on_select:        Node to go to when an item is selected.
        title:            Heading shown above the list.
        empty_text:       Text shown when the list is empty.
        item_description: Optional subtitle callable.
        extra_options:    Static :class:`Option` list (max 3, last page only).
        interactive:      If True, render as interactive list.
        button_label:     Custom label for the interactive list button.
        page_size:        Items per page (default 8, clamped to 1–10).
    """
    fetch: Callable[..., Any]
    item_label: Callable[[Any], str]
    on_select: str
    title: str = "Select an option"
    empty_text: str = "No items available."
    item_description: Optional[Callable[[Any], str]] = None
    extra_options: List[Option] = field(default_factory=list)
    interactive: bool = False
    button_label: str = "Options"
    page_size: int = 8

    def __post_init__(self):
        if self.interactive and len(self.extra_options) > 3:
            raise ValueError(
                f"ListNode interactive mode supports at most 3 extra_options, got {len(self.extra_options)}."
            )
        self.page_size = max(1, min(10, self.page_size))

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

    Args:
        generate:  Callable ``(session, collected) -> bytes``.
        filename:  Filename or callable ``(session, collected) -> str``.
        mime_type: MIME type string, e.g. ``"application/pdf"``.
        caption:   Optional caption text or callable ``(session, collected) -> str``.
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


# ── convenience re-exports ────────────────────────────────────────────────────

__all__ = [
    # option / route helpers
    "Option",
    "Route",
    # field types
    "BaseField",
    "Field",
    "TextField",       # alias for Field
    "MenuField",
    "ButtonsField",
    "ImageField",
    "DocumentField",
    "LocationField",
    "BranchField",
    # node types
    "BaseNode",
    "Menu",
    "Input",
    "Confirm",
    "Action",
    "Router",
    "ListNode",
    "MediaReply",
    # type alias
    "NodeDict",
]