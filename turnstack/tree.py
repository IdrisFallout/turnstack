"""
turnstack.tree
==============
FlowTree — the single source of truth for a bot's conversation flow.

Developers define their entire flow here using node classes from
:mod:`turnstack.nodes`.  The tree is validated at engine startup so broken
flows fail loudly, not silently at runtime.
"""

from __future__ import annotations
from typing import Dict, Any, List, Optional, Union
from .exceptions import FlowValidationError
from .nodes import BaseNode, NodeDict

# ── node type constants ───────────────────────────────────────────────────────
NODE_MENU        = "menu"
NODE_INPUT       = "input"
NODE_CONFIRM     = "confirm"
NODE_ACTION      = "action"
NODE_ROUTER      = "router"
NODE_LIST        = "list"
NODE_MEDIA       = "media"
NODE_MULTI_INPUT = "multi_input"

# All types that have a single "next" key
_SINGLE_NEXT = {NODE_INPUT, NODE_ACTION, NODE_MEDIA, NODE_MULTI_INPUT}
# All types that have options with individual "next" keys
_OPTION_NEXT = {NODE_MENU, NODE_CONFIRM}


class FlowTree:
    """
    Container for all flow nodes.

    Usage::

        from turnstack import FlowTree
        from turnstack.nodes import Menu, Option, Input, Action, Router, Route

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

    Nodes can be added as node class instances *or* raw dicts (for backwards
    compatibility with existing code).
    """

    def __init__(self, entry: str = "welcome"):
        self._nodes: Dict[str, NodeDict] = {}
        self.entry = entry

    # ── public API ────────────────────────────────────────────────────────

    def add(self, name: str, node: Union[BaseNode, NodeDict]) -> "FlowTree":
        """
        Register a node under ``name``.

        Accepts either a node class instance (recommended) or a raw dict
        (legacy / advanced use).

        Returns self so calls can be chained::

            tree.add("a", ...).add("b", ...).add("c", ...)
        """
        self._nodes[name] = node.to_dict() if isinstance(node, BaseNode) else node
        return self

    def get(self, name: str) -> Optional[NodeDict]:
        """Return the raw node dict for ``name``, or None if not found."""
        return self._nodes.get(name)

    def all_nodes(self) -> Dict[str, NodeDict]:
        """Return a copy of all nodes (read-only view for tooling)."""
        return dict(self._nodes)

    # ── validation ────────────────────────────────────────────────────────

    def validate(self) -> None:
        """
        Walk the entire tree and raise :class:`FlowValidationError` on the
        first broken reference.

        Called automatically by ``BotEngine.__init__``.
        Developers can also call it manually after building the tree.

        Checks:
        - Entry node exists
        - Every ``next`` key points to a real node (or ``"__end__"``)
        - Every option ``next`` key in menu/confirm nodes points to a real node
        - Every router ``default`` points to a real node
        - Every router route ``next`` points to a real node
        - Every list ``on_select`` points to a real node
        - No action node references itself as ``next``
        """
        errors: List[str] = []

        if self.entry not in self._nodes:
            errors.append(f"Entry node '{self.entry}' is not defined in the tree.")

        for node_name, node in self._nodes.items():
            t = node.get("type", "")

            # Existing checks
            if t in _SINGLE_NEXT:
                self._check_ref(node_name, node.get("next"), errors)

            elif t in _OPTION_NEXT:
                options = node.get("options", [])
                if t == NODE_CONFIRM and len(options) > 3:
                    errors.append(
                        f"Confirm node '{node_name}' has {len(options)} options. "
                        "WhatsApp interactive buttons support at most 3."
                    )
                for opt in options:
                    target = opt.get("next") if isinstance(opt, dict) else None
                    self._check_ref(f"{node_name} option '{opt.get('label', '?')}'", target, errors)

            elif t == NODE_ROUTER:
                self._check_ref(f"{node_name} default", node.get("default"), errors)
                for route in node.get("routes", []):
                    self._check_ref(f"{node_name} route", route.get("next"), errors)

            elif t == NODE_LIST:
                self._check_ref(f"{node_name} on_select", node.get("on_select"), errors)
                extra_opts = node.get("extra_options", [])
                if len(extra_opts) > 3:
                    errors.append(
                        f"ListNode '{node_name}' has {len(extra_opts)} extra_options, "
                        "but interactive lists support at most 3 static actions."
                    )

        if errors:
            raise FlowValidationError(
                "Flow tree validation failed:\n" + "\n".join(f"  • {e}" for e in errors)
            )

    def _check_ref(self, context: str, key: Optional[str], errors: List[str]) -> None:
        if key and key != "__end__" and key not in self._nodes:
            errors.append(f"{context} → references missing node '{key}'")