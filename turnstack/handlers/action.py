from __future__ import annotations
import inspect
from typing import Any, Dict, List, TYPE_CHECKING

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
    - A ``str``   — sent as a separate text message, then the next node is
                    sent as a second message.
    - A ``Reply`` — used as-is (allows sending media, ending session, etc.),
                    then the next node is sent as a second message.
    - ``None``    — nothing extra is sent; only the next node reply is sent.

    Both sync and async functions are supported.

    The engine receives a list and sends each reply in order, so the action
    text and the follow-up node always arrive as two distinct messages.
    """

    # Sentinel attribute the engine checks to unwrap multi-reply actions.
    MULTI_REPLY_ATTR = "_action_replies"

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

        if next_key == tree.entry:
            session.collected = {}

        self._transition_to(session, next_key)

        # ── get next node reply ───────────────────────────────────────
        next_reply = await self._enter_node(session, tree, _depth + 1)
        if next_reply is None:
            next_reply = self._error(session, "Next node returned no reply.")

        # ── no action text → just return next reply as usual ─────────
        if result is None:
            return next_reply

        # ── action produced text or a Reply → send as TWO messages ───
        # We attach the pair on the reply object; engine.py unwraps it.
        if isinstance(result, Reply):
            action_reply = result
        else:
            action_reply = Reply(
                type="text",
                body=str(result),
                phone=session.user_id,
                current_node=session.current_node,
            )

        # Carry options/node_type only on the SECOND (next_reply) message.
        # Tag the first reply so the engine knows to split them.
        setattr(action_reply, self.MULTI_REPLY_ATTR, [action_reply, next_reply])
        return action_reply