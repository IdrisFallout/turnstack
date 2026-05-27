"""
turnstack.engine
================
BotEngine — the main entry point.

Usage::

    engine = BotEngine(tree=tree, session_store=InMemorySessionStore())
    replies = await engine.process(incoming_message)
    for reply in replies:
        await send_whatsapp(reply)

``process()`` always returns a **list** of :class:`Reply` objects.
In the common case the list has one item.  When a ``media`` node fires,
the list has two items: the file reply followed immediately by the
rendered next node — so the developer never has to touch session internals.
"""

from __future__ import annotations
from typing import Dict, List, Optional, Set, Union

from .message import IncomingMessage
from .reply import Reply
from .session import Session, SessionStore
from .stores.memory import InMemorySessionStore
from .tree import (
    FlowTree,
    NODE_MENU, NODE_INPUT, NODE_CONFIRM, NODE_ACTION,
    NODE_ROUTER, NODE_LIST, NODE_MEDIA, NODE_INPUT,
)
from .handlers.base import NodeHandler
from .handlers.menu import MenuHandler
from .handlers.confirm import ConfirmHandler
from .handlers.action import ActionHandler
from .handlers.router import RouterHandler
from .handlers.list_handler import ListHandler
from .handlers.input import InputHandler
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
        unsupported_text: Optional[str] = None,
    ):
        self.tree = tree
        self.session_store = session_store or InMemorySessionStore(session_timeout=session_timeout)
        self.session_timeout = session_timeout

        # Global command keywords (case‑insensitive)
        self.back_keywords = back_keywords or {"0", "back", "go back"}
        self.home_keywords = home_keywords or {"00", "home", "menu", "start over"}
        self.exit_keywords = exit_keywords or {"000", "exit", "quit", "reset", "goodbye", "bye"}

        # Message shown when an unsupported type (sticker, audio, reaction…) arrives
        self.unsupported_text = (
            unsupported_text
            or "⚠️ Sorry, I can't process the message. Please retry."
        )

        # Types the engine knows how to route; everything else gets unsupported_text
        self._supported_types = {"text", "interactive", "image", "document", "location"}

        # validate tree on startup — fail loud, not silent
        self.tree.validate()

        # ── default handler registry ──────────────────────────────────
        self._handlers: Dict[str, NodeHandler] = {
            NODE_MENU:        MenuHandler(),
            NODE_CONFIRM:     ConfirmHandler(),
            NODE_ACTION:      ActionHandler(),
            NODE_ROUTER:      RouterHandler(),
            NODE_LIST:        ListHandler(),
            NODE_INPUT:       InputHandler(),
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

    async def process(self, incoming: IncomingMessage) -> List[Reply]:
        """
        Process one incoming message and return a list of Reply objects.

        This is the only public method the developer calls.

        Usually the list contains a single item.  When a ``media`` node
        fires, the list contains two items: the file reply followed by the
        rendered follow-up node (e.g. the menu the user lands on after
        receiving the file).  The developer simply iterates and sends each
        reply in order — no session introspection required.
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
            return [entry_reply]

        # ── 3. touch (activate if new) ────────────────────────────────
        session.touch()

        # ── 4. unsupported message type → polite reply, no state change ──
        if incoming.type not in self._supported_types:
            reply = await self._render_current(session)
            if reply is None:
                reply = Reply(
                    type="text",
                    body=self.unsupported_text,
                    phone=incoming.user_id,
                )
            else:
                reply.body = self.unsupported_text + "\n\n" + reply.body
            reply = self._enrich_menu_reply(reply, session)
            reply.session_state = session.lifecycle_state
            reply.current_node = session.current_node
            await self.session_store.save(session)
            return [reply]

        # ── 5. new session first message — always render entry node ───
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
            return [reply]

        # ── 6. INTERCEPT GLOBAL COMMANDS (before dispatch) ────────────
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
                return [reply]

        # ── 7. normal dispatch ────────────────────────────────────────
        reply = await self._dispatch(session, incoming)
        if reply is None:
            reply = Reply(
                type="error",
                body="No reply generated.",
                phone=session.user_id,
                current_node=session.current_node,
            )
        reply = self._enrich_menu_reply(reply, session)

        # ── 8. attach meta and save ───────────────────────────────────
        reply.session_state = session.lifecycle_state
        reply.current_node  = session.current_node
        await self.session_store.save(session)

        # ── 9. media follow-up: render the next node automatically ────
        # When a media node fires it sends a file and then advances the
        # session to `next`.  The user must receive that follow-up node
        # (usually a menu) as a second message immediately — the engine
        # handles this so the developer never has to call session_store or
        # _render_current themselves.
        if reply.type == "media" and session.current_node:
            follow_reply = await self._render_current(session)
            if follow_reply:
                follow_reply = self._enrich_menu_reply(follow_reply, session)
                follow_reply.session_state = session.lifecycle_state
                follow_reply.current_node  = session.current_node
                await self.session_store.save(session)
                return [reply, follow_reply]

        return [reply]

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
            # ── input-aware back: step within the input node first ────
            node = self.tree.get(session.current_node)
            if node and node.get("type") == "input":
                from .handlers.input import _IDX_KEY_TMPL
                idx_key = _IDX_KEY_TMPL.format(node=session.current_node)
                idx     = session.pagination.get(idx_key, 0)
                if idx > 0:
                    # Clear the previously collected value for that field
                    fields     = node.get("fields", [])
                    prev_field = fields[idx - 1]
                    session.collected.pop(prev_field.get("name", ""), None)
                    session.pagination[idx_key] = idx - 1
                    return None  # engine will call _render_current → re-renders the stepped-back field
                # idx == 0: fall through to normal node-level back below
            # ── normal node-level back ────────────────────────────────
            previous = session.go_back()
            if previous:
                session.current_node = previous
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

        # router, action, and media nodes run immediately even on first render
        if t in ("router", "action", "media"):
            return await self._dispatch(
                session,
                IncomingMessage(user_id=session.user_id, type="text", text=""),
            )

        # renderable nodes
        handler = self._handlers.get(t) or next(iter(self._handlers.values()))
        return handler._render(node, session)

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