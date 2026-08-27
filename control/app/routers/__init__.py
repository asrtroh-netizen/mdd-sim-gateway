"""FastAPI routers split out of the control-plane monolith.

Route handlers are bound to ``control.app.main`` at include time so existing
tests that patch ``main.hub`` / ``main._unified_devices`` keep working.
"""
from __future__ import annotations


def include_routers(app, main_module) -> None:
    from . import carrier_profiles, devices, engineering, esim, lines, sms

    modules = (devices, engineering, lines, sms, esim, carrier_profiles)
    # Re-export first so bind() can copy cross-router names such as api_instance_start.
    for module in modules:
        for name in getattr(module, "EXPORTS", ()):
            setattr(main_module, name, getattr(module, name))
    for module in modules:
        module.bind(main_module)
        app.include_router(module.router)
