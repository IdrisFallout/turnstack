from dataclasses import dataclass, field
from typing import Optional, Dict, Any


@dataclass
class IncomingMessage:
    """
    Normalised incoming message — transport-agnostic.

    The developer's adapter (in their webhook handler) converts the raw
    WhatsApp/Twilio/pywa payload into this object and passes it to
    ``BotEngine.process()``.  The engine never sees raw API payloads.

    Fields
    ------
    phone:          Session key — the sender's phone number (e.g. "254711234567").
    type:           Message type: "text" | "interactive" | "image" |
                    "document" | "audio" | "video" | "location" | "unknown".
    text:           Populated for type="text". The raw message body.
    interactive_id: Populated for type="interactive". The ID of the button
                    or list item the user selected (after mapping, this is
                    the node key / value the developer set on the Option).
    media_id:       Provider media ID for image/document/audio/video.
                    Developer must download via their provider's media API.
    media_mime:     MIME type of the media (e.g. "image/jpeg").
    media_name:     Original filename (documents only).
    location:       Dict with keys: latitude, longitude, name, address.
    raw:            The original provider payload — available in action
                    functions via session for advanced use cases.
    """
    user_id: str
    type: str                                          # "text" | "interactive" | ...
    text: Optional[str] = None
    interactive_id: Optional[str] = None
    media_id: Optional[str] = None
    media_mime: Optional[str] = None
    media_name: Optional[str] = None
    location: Optional[Dict[str, Any]] = None
    raw: Optional[Dict[str, Any]] = None