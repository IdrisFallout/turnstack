class TurnStackError(Exception):
    """Base exception for all TurnStack errors."""
    pass

class FlowValidationError(TurnStackError):
    """Raised when the flow tree is invalid at startup."""
    pass

class NodeNotFoundError(TurnStackError):
    """Raised when a node key referenced in the tree does not exist."""
    pass

class SessionNotFoundError(TurnStackError):
    """Raised when the session store cannot find a session."""
    pass

class HandlerNotFoundError(TurnStackError):
    """Raised when no handler is registered for a node type."""
    pass