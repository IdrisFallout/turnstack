from dataclasses import dataclass, field
from typing import Optional, List, Literal, Dict, Any


@dataclass
class ReplyOption:
    """
    A single option hint returned alongside a menu/confirm reply.

    The developer's WhatsApp adapter uses these to build interactive
    button or list payloads.  The engine always populates this on
    menu and confirm nodes so the adapter never has to re-parse the tree.
    """
    label: str
    value: str          # the node key / Option.value — what gets sent back as interactive_id
    description: str = ""


@dataclass
class Reply:
    """
    The engine's response object.

    ``BotEngine.process()`` always returns one of these.
    The developer's adapter pattern-matches on ``type`` to decide
    how to send it via their WhatsApp provider.

    Fields
    ------
    type:             "text" | "media" | "end" | "error"
    body:             Text body to send. For media, this is the caption.
    phone:            Recipient phone number.
    file_bytes:       Raw file bytes (populated when type="media").
    filename:         Filename for media (e.g. "report_june.pdf").
    mime_type:        MIME type for media (e.g. "application/pdf").
    options:          Option hints for menu/confirm nodes.
                      Use these to build interactive buttons/lists without
                      re-reading the tree yourself.
    node_type:        The type of the current node ("menu", "input", etc.).
                      Lets the adapter decide whether to send interactive or plain text.
    current_node:     Current node key — useful for debugging.
    session_state:    "new" | "active" | "expired"
    suggested_replies: Simple string list for quick-reply chips (subset of options.label).
    meta:             Arbitrary metadata dictionary for the adapter.
                      For example, set ``meta={"button_label": "Select Property"}``
                      to override the default "Options" button text in interactive lists.
    """
    type: Literal["text", "media", "end", "error"]
    body: str
    phone: str

    # media fields
    file_bytes: Optional[bytes] = None
    filename: Optional[str] = None
    mime_type: Optional[str] = None

    # interactive hints — populated for menu/confirm nodes
    options: List[ReplyOption] = field(default_factory=list)
    node_type: Optional[str] = None           # "menu" | "confirm" | "input" | etc.

    # convenience
    suggested_replies: List[str] = field(default_factory=list)

    # debug / meta
    current_node: Optional[str] = None
    session_state: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)