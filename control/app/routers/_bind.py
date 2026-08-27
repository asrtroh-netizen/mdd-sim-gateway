"""Bind router handlers to the live ``control.app.main`` module.

Helpers stay on ``main`` so existing tests can ``patch.object(main, ...)``.
Each request copies the current attributes into the handler module globals.
"""
from __future__ import annotations

import asyncio
from functools import wraps


def apply(module_globals: dict, main_module, names: tuple[str, ...]) -> None:
    for name in names:
        if hasattr(main_module, name):
            module_globals[name] = getattr(main_module, name)


def with_main(*names: str):
    """Refresh selected ``main`` attributes into the wrapped function's globals."""

    def decorator(fn):
        if asyncio.iscoroutinefunction(fn):
            @wraps(fn)
            async def wrapper(*args, **kwargs):
                from control.app import main as main_module
                g = fn.__globals__
                for name in names:
                    if hasattr(main_module, name):
                        g[name] = getattr(main_module, name)
                return await fn(*args, **kwargs)
        else:
            @wraps(fn)
            def wrapper(*args, **kwargs):
                from control.app import main as main_module
                g = fn.__globals__
                for name in names:
                    if hasattr(main_module, name):
                        g[name] = getattr(main_module, name)
                return fn(*args, **kwargs)
        return wrapper

    return decorator
