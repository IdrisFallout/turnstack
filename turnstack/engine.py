"""
turnstack.engine
================
BotEngine — the main entry point.

Usage::

    engine = BotEngine(tree=tree, session_store=InMemorySessionStore())
    reply  = await engine.process(incoming_message)
"""

from __future__ import annotations
from typing import Dict, Optional, Set, Union

from .message import IncomingMessage
from .reply import Reply
from .session import Session, SessionStore
from .stores.memory import InMemorySessionStore
from .tree import (
    FlowTree,
    NODE_MENU, NODE_INPUT, NODE_CONFIRM, NODE_ACTION,
    NODE_ROUTER, NODE_LIST, NODE_MEDIA, NODE_MULTI_INPUT,
)
from .handlers.base import NodeHandler
from .handlers.menu import MenuHandler
from .handlers.input import InputHandler
from .handlers.confirm import ConfirmHandler
from .handlers.action import ActionHandler
from .handlers.router import RouterHandler
from .handlers.list_handler import ListHandler
from .handlers.multi_input import MultiInputHandler
from .handlers.media_handler import MediaHandler


class BotEngine:
    """
    The TurnStack engine.

    Parameters
    ----------
    tree:            A validated :class:`FlowTree`.
    session_store:   Optional :class:`SessionStore` implementation.
                     Defaults to :class:`InMemorySessionStore` (dev/testing).
    session_timeout: Seconds of inactivity before a session expires. Default: 300.
    back_keywords:   Set of text strings that trigger "go back" (default: {"0","back","go back"}).
    home_keywords:   Set of text strings that trigger "go home" (default: {"00","home","main menu","start over"}).
    exit_keywords:   Set of text strings that trigger "exit/reset session" (default: {"exit","quit","reset","goodbye"}).

    The engine:
    1. Loads or creates the user's session.
    2. Checks for expiry and resets if needed.
    3. Intercepts global navigation commands (back, home, exit).
    4. Dispatches to the correct :class:`NodeHandler`.
    5. Saves the updated session.
    6. Returns a :class:`Reply` the developer maps to their WhatsApp provider.
    """

    def __init__(
        self,
        tree: FlowTree,
        session_store: Optional[SessionStore] = None,
        session_timeout: int = 300,
        back_keywords: Optional[Set[str]] = None,
        home_keywords: Optional[Set[str]] = None,
        exit_keywords: Optional[Set[str]] = None,
    ):
        self.tree = tree
        self.session_store = session_store or InMemorySessionStore(session_timeout=session_timeout)
        self.session_timeout = session_timeout

        # Global command keywords (case‑insensitive)
        self.back_keywords = back_keywords or {"0", "back", "go back"}
        self.home_keywords = home_keywords or {"00", "home", "main menu", "start over"}
        self.exit_keywords = exit_keywords or {"000", "exit", "quit", "reset", "goodbye", "bye"}

        # validate tree on startup — fail loud, not silent
        self.tree.validate()

        # ── default handler registry ──────────────────────────────────
        self._handlers: Dict[str, NodeHandler] = {
            NODE_MENU:        MenuHandler(),
            NODE_INPUT:       InputHandler(),
            NODE_CONFIRM:     ConfirmHandler(),
            NODE_ACTION:      ActionHandler(),
            NODE_ROUTER:      RouterHandler(),
            NODE_LIST:        ListHandler(),
            NODE_MULTI_INPUT: MultiInputHandler(),
            NODE_MEDIA:       MediaHandler(),
        }

    def register_handler(self, node_type: str, handler: NodeHandler) -> None:
        """
        Register a custom handler for a node type.

        Use this to extend the engine with new node types or override
        built-in behaviour::

            engine.register_handler("payment_prompt", MyPaymentHandler())
        """
        self._handlers[node_type] = handler

    async def process(self, incoming: IncomingMessage) -> Reply:
        """
        Process one incoming message and return a Reply.

        This is the only public method the developer calls.
        """
        # ── 1. load or create session ─────────────────────────────────
        session = await self.session_store.get(incoming.user_id)
        if session is None:
            session = Session(user_id=incoming.user_id, current_node=self.tree.entry)

        # ── 2. handle expiry ──────────────────────────────────────────
        if session.is_expired(self.session_timeout):
            session.reset(self.tree.entry)
            await self.session_store.save(session)
            # re-process as a fresh session (shows entry node)
            entry_reply = await self._dispatch(
                session,
                IncomingMessage(user_id=incoming.user_id, type="text", text=""),
            )
            if entry_reply is None:
                entry_reply = Reply(
                    type="error",
                    body="Internal error.",
                    phone=incoming.user_id,
                )
            entry_reply = self._enrich_menu_reply(entry_reply, session)
            await self.session_store.save(session)
            return entry_reply

        # ── 3. touch (activate if new) ────────────────────────────────
        session.touch()

        # ── 4. new session first message — always render entry node ───
        if session.lifecycle_state == "new" or _is_blank(incoming):
            session.touch()
            reply = await self._render_current(session)
            if reply is None:
                reply = Reply(
                    type="error",
                    body="Failed to render entry node.",
                    phone=incoming.user_id,
                )
            reply = self._enrich_menu_reply(reply, session)
            await self.session_store.save(session)
            return reply

        # ── 5. INTERCEPT GLOBAL COMMANDS (before dispatch) ────────────
        # Only plain text messages (not interactive selections) can be commands
        if incoming.type == "text" and incoming.text:
            cmd_reply = await self._handle_global_command(session, incoming.text.strip().lower())
            if cmd_reply:
                # Command handled – render the resulting node
                reply = await self._render_current(session)
                if reply is None:
                    reply = Reply(
                        type="error",
                        body="Failed to render after command.",
                        phone=incoming.user_id,
                    )
                reply = self._enrich_menu_reply(reply, session)
                reply.session_state = session.lifecycle_state
                reply.current_node = session.current_node
                await self.session_store.save(session)
                return reply

        # ── 6. normal dispatch ────────────────────────────────────────
        reply = await self._dispatch(session, incoming)
        if reply is None:
            reply = Reply(
                type="error",
                body="No reply generated.",
                phone=session.user_id,
                current_node=session.current_node,
            )
        reply = self._enrich_menu_reply(reply, session)

        # ── 7. attach meta and save ───────────────────────────────────
        reply.session_state = session.lifecycle_state
        reply.current_node  = session.current_node
        await self.session_store.save(session)
        return reply

    # ── internal ──────────────────────────────────────────────────────────

    async def _handle_global_command(self, session: Session, text: str) -> Optional[Reply]:
        """
        Check if the input matches a global command.
        Returns a Reply only if the command should interrupt normal flow.
        Otherwise returns None.
        """
        # Exit / reset
        if text in self.exit_keywords:
            session.reset(self.tree.entry)
            # Optionally send a goodbye message (could be configured)
            return Reply(
                type="text",
                body="👋 Session reset. Type anything to start over.",
                phone=session.user_id,
                node_type="text",
                current_node=session.current_node,
            )

        # Go home
        if text in self.home_keywords:
            session.go_home(self.tree.entry)
            return None  # No extra message; we will re‑render the entry node

        # Go back
        if text in self.back_keywords:
            previous = session.go_back()
            if previous:
                session.current_node = previous
            else:
                # Already at root – stay where you are
                pass
            return None

        return None

    async def _dispatch(self, session: Session, incoming: IncomingMessage) -> Optional[Reply]:
        node = self.tree.get(session.current_node)
        if not node:
            return Reply(
                type="error",
                body=f"Node '{session.current_node}' not found in tree.",
                phone=incoming.user_id,
                session_state=session.lifecycle_state,
            )

        node_type = node.get("type")
        handler = self._handlers.get(node_type)
        if not handler:
            return Reply(
                type="error",
                body=f"No handler registered for node type '{node_type}'.",
                phone=incoming.user_id,
                session_state=session.lifecycle_state,
            )

        return await handler.handle(node, session, incoming, self.tree)

    async def _render_current(self, session: Session) -> Optional[Reply]:
        """Render the current node without processing any input."""
        node = self.tree.get(session.current_node)
        if not node:
            return Reply(type="error", body="Entry node not found.", phone=session.user_id)

        t = node.get("type")

        # router and action nodes run immediately even on first render
        if t in ("router", "action"):
            return await self._dispatch(
                session,
                IncomingMessage(user_id=session.user_id, type="text", text=""),
            )

        # renderable nodes
        from .handlers.base import NodeHandler as _Base
        dummy = _Base.__new__(_Base)
        return dummy._render(node, session)

    def _enrich_menu_reply(self, reply: Reply, session: Session) -> Reply:
        """
        Ensure menu replies have button_label from the node.

        This method is called automatically after every reply is generated.
        Developers never need to touch `reply.meta` – they only set `button_label`
        on their Menu nodes, and the engine propagates it to the WhatsApp adapter.
        """
        # Guard against None (should not happen, but safety)
        if reply is None:
            return Reply(
                type="error",
                body="Internal error: missing reply.",
                phone=session.user_id,
                current_node=session.current_node,
            )
        if reply.node_type == "menu" and not reply.meta.get("button_label"):
            node_name = reply.current_node or session.current_node
            node = self.tree.get(node_name)
            if node and node.get("button_label"):
                reply.meta["button_label"] = node["button_label"]
        return reply


def _is_blank(msg: IncomingMessage) -> bool:
    return (
        not msg.text
        and not msg.interactive_id
        and not msg.media_id
        and not msg.location
    )