"""
handlers/router.py
==================
RouterHandler — silent branching, no user input.

This is the entry-point node type.  When the engine lands on a router node
it evaluates the conditions in order, picks the first True branch, and
transparently navigates to that node — the user never sees the router itself.

The optional ``before`` callable runs first (useful for loading user profile
from DB into ``session.context``).

Both sync and async ``before`` and ``when`` callables are supported.

Example::

    from turnstack.nodes import Router, Route

    async def load_user(session):
        session.context["user"] = await db.get_user(session.phone)

    tree.add("entry", Router(
        before=load_user,
        routes=[
            Route(when=lambda s: s.context.get("user") and s.context["user"]["role"] == "landlord",
                  next="landlord_home"),
            Route(when=lambda s: s.context.get("user") and s.context["user"]["role"] == "tenant",
                  next="tenant_home"),
            Route(when=lambda s: s.context.get("invitation"),
                  next="tenant_onboarding"),
        ],
        default="public_welcome",
    ))
"""

from __future__ import annotations
import inspect
from typing import Any, Dict, TYPE_CHECKING

from ..message import IncomingMessage
from ..reply import Reply
from ..session import Session
from .base import NodeHandler

if TYPE_CHECKING:
    from ..tree import FlowTree


class RouterHandler(NodeHandler):

    async def handle(
        self,
        node: Dict[str, Any],
        session: Session,
        message: IncomingMessage,
        tree: "FlowTree",
    ) -> Reply:
        return await self._run_router(node, session, tree, _depth=0)

    async def _run_router(
        self,
        node: Dict[str, Any],
        session: Session,
        tree: "FlowTree",
        _depth: int = 0,
    ) -> Reply:
        # ── run before hook (e.g. load user from DB) ──────────────────
        before = node.get("before")
        if before:
            try:
                if inspect.iscoroutinefunction(before):
                    await before(session)
                else:
                    before(session)
            except Exception as exc:
                return self._error(session, f"Router 'before' hook raised: {exc}")

        # ── evaluate conditions in order ──────────────────────────────
        target = node.get("default", tree.entry)

        for route in node.get("routes", []):
            condition = route.get("when")
            if condition is None:
                continue
            try:
                result = (
                    await condition(session)
                    if inspect.iscoroutinefunction(condition)
                    else condition(session)
                )
            except Exception:
                continue   # failed condition → skip to next

            if result:
                target = route.get("next", target)
                break

        # ── navigate silently ─────────────────────────────────────────
        # Router nodes are NOT pushed onto the nav stack — they are
        # transparent.  The user's "Back" takes them to whatever was
        # before the router, not to the router itself.
        session.current_node = target

        return await self._enter_node(session, tree, _depth + 1)