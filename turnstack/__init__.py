"""
TurnStack — WhatsApp conversation flow engine.

Quick start::

    from turnstack import BotEngine, FlowTree, InMemorySessionStore, IncomingMessage
    from turnstack.nodes import Menu, Input, Action, Confirm, Router, ListNode, Option, Field, Route

    tree = FlowTree(entry="entry")

    tree.add("entry", Router(
        routes=[Route(when=lambda s: s.context.get("user"), next="home")],
        default="welcome",
    ))

    tree.add("welcome", Menu(
        text="Welcome! What would you like to do?",
        options=[
            Option("Register", next="register_name"),
            Option("Learn More", next="learn_more"),
        ]
    ))

    engine = BotEngine(tree=tree)

    # In your webhook handler:
    reply = await engine.process(IncomingMessage(phone="254711234567", type="text", text="1"))
    # reply.type   → "text" | "media" | "end" | "error"
    # reply.body   → text to send
    # reply.options → ReplyOption list for building interactive messages
"""

from .engine  import BotEngine
from .tree    import FlowTree
from .session import Session, SessionStore
from .message import IncomingMessage
from .reply   import Reply, ReplyOption
from .stores.memory import InMemorySessionStore
from .exceptions import (
    TurnStackError,
    FlowValidationError,
    NodeNotFoundError,
    SessionNotFoundError,
    HandlerNotFoundError,
)

__version__ = "0.2.0"

__all__ = [
    # core
    "BotEngine",
    "FlowTree",
    "Session",
    "SessionStore",
    "IncomingMessage",
    "Reply",
    "ReplyOption",
    "InMemorySessionStore",
    # exceptions
    "TurnStackError",
    "FlowValidationError",
    "NodeNotFoundError",
    "SessionNotFoundError",
    "HandlerNotFoundError",
]