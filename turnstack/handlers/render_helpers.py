from typing import Dict, Any
from ..session import Session

def render_node_prompt(node: Dict[str, Any], session: Session) -> str:
    t = node.get("type")
    if t == "menu":
        lines = [node.get("text", ""), ""]
        for i, opt in enumerate(node.get("options", []), 1):
            label = opt.get("label") if isinstance(opt, dict) else opt[0]
            lines.append(f"{i}. {label}")
        return "\n".join(lines)
    elif t == "input":
        return node.get("prompt", "")
    elif t == "confirm":
        text = node.get("text", "")
        if callable(text):
            body = text(session.collected)
        else:
            body = text
        lines = [body, ""]
        for i, opt in enumerate(node.get("options", []), 1):
            label = opt.get("label") if isinstance(opt, dict) else opt[0]
            lines.append(f"{i}. {label}")
        return "\n".join(lines)
    elif t == "action":
        return ""  # Actions produce their own reply
    else:
        return "Unknown node type."