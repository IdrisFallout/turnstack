from __future__ import annotations
import inspect
from typing import Any, Dict, TYPE_CHECKING

from ..message import IncomingMessage
from ..reply import Reply
from ..session import Session
from .base import NodeHandler

if TYPE_CHECKING:
    from ..tree import FlowTree


class MediaHandler(NodeHandler):
    """
    Handles ``media`` nodes.

    Calls ``generate(session, collected)`` to produce file bytes,
    returns a ``Reply(type="media", ...)`` and then advances to ``next``.

    The developer's WhatsApp adapter sends the file using their provider.

    ``filename`` and ``caption`` can be plain strings or callables
    ``(session, collected) -> str``.
    """

    async def handle(
        self,
        node: Dict[str, Any],
        session: Session,
        message: IncomingMessage,
        tree: "FlowTree",
    ) -> Reply:
        generate = node.get("generate")
        if not generate:
            return self._error(session, "MediaNode has no 'generate' function.")

        try:
            if inspect.iscoroutinefunction(generate):
                file_bytes = await generate(session, session.collected)
            else:
                file_bytes = generate(session, session.collected)
        except Exception as exc:
            return self._error(session, f"MediaNode generate raised: {exc}")

        # resolve filename
        filename_raw = node.get("filename", "file")
        if callable(filename_raw):
            filename = filename_raw(session, session.collected)
        else:
            filename = filename_raw

        # resolve caption
        caption_raw = node.get("caption", "")
        if callable(caption_raw):
            caption = caption_raw(session, session.collected)
        else:
            caption = caption_raw

        # advance session
        next_key = node.get("next", "welcome")
        self._transition_to(session, next_key)

        return Reply(
            type="media",
            body=caption,
            phone=session.user_id,
            file_bytes=file_bytes,
            filename=filename,
            mime_type=node.get("mime_type", "application/octet-stream"),
            current_node=session.current_node,
        )