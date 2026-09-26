"""Short, useful errors across async MCP task-group boundaries."""


def error_message(error: BaseException, limit: int = 400) -> str:
    """
    Convert normal or grouped async exceptions into one short, deduplicated, user-friendly error string.
    """
    if isinstance(error, BaseExceptionGroup):
        parts = [error_message(child, limit) for child in error.exceptions]
        return "; ".join(dict.fromkeys(parts))[:limit]
    return (str(error) or type(error).__name__)[:limit]
