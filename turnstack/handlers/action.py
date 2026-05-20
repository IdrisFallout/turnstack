from __future__ import annotations
import inspect
from typing import Any, Dict, TYPE_CHECKING

from ..message import IncomingMessage
from ..reply import Reply
from ..session import Session
from .base import NodeHandler

if TYPE_CHECKING:
    from ..tree import FlowTree


class ActionHandler(NodeHandler):
    """
    Handles ``action`` nodes.

    The developer's ``fn`` may return:
    - A ``str``   — sent as a text message, then the flow advances to ``next``.
    - A ``Reply`` — used as-is (allows sending media, ending session, etc.),
                    then the flow advances to ``next``.
    - ``None``    — nothing extra is sent; the flow advances to ``next``.

    Both sync and async functions are supported.
    """

    async def handle(
        self,
        node: Dict[str, Any],
        session: Session,
        message: IncomingMessage,
        tree: "FlowTree",
    ) -> Reply:
        return await self._run_action(node, session, tree, _depth=0)

    async def _run_action(
        self,
        node: Dict[str, Any],
        session: Session,
        tree: "FlowTree",
        _depth: int = 0,
    ) -> Reply:
        fn = node.get("fn")
        if not fn:
            return self._error(session, "Action node has no 'fn' defined.")

        # ── call fn (sync or async) ───────────────────────────────────
        try:
            if inspect.iscoroutinefunction(fn):
                result = await fn(session, session.collected)
            else:
                result = fn(session, session.collected)
        except Exception as exc:
            return self._error(session, f"Action fn raised: {exc}")

        # ── advance to next node ──────────────────────────────────────
        next_key = node.get("next", "welcome")
        if next_key == "__end__":
            body = result if isinstance(result, str) else (result.body if result else "")
            return Reply(type="end", body=body, phone=session.user_id,
                         current_node=session.current_node)

        if next_key == session.tree_entry if hasattr(session, "tree_entry") else next_key == "welcome":
            session.collected = {}

        self._transition_to(session, next_key)

        # ── build the combined reply ──────────────────────────────────
        # Enter next node (may chain another action/router silently)
        next_reply = await self._enter_node(session, tree, _depth + 1)

        if isinstance(result, Reply):
            # Developer returned a full Reply — send it, then queue next render
            # We concatenate bodies so the user sees both in one message
            if next_reply.body:
                result.body = result.body + "\n\n" + next_reply.body
            result.current_node = next_reply.current_node
            result.options = next_reply.options
            result.node_type = next_reply.node_type
            return result

        # Developer returned a str or None
        action_text = str(result) if result is not None else ""
        if action_text and next_reply.body:
            combined = action_text + "\n\n" + next_reply.body
        elif action_text:
            combined = action_text
        else:
            combined = next_reply.body

        return Reply(
            type=next_reply.type,
            body=combined,
            phone=session.user_id,
            options=next_reply.options,
            node_type=next_reply.node_type,
            file_bytes=next_reply.file_bytes,
            filename=next_reply.filename,
            mime_type=next_reply.mime_type,
            current_node=next_reply.current_node,
        )