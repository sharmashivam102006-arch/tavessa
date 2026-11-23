"""Tavessa realtime.

Stage 4: the in-process ``ConnectionManager`` (``realtime/manager.py``)
backs the kitchen WebSocket (``GET /ws/kitchen``). Single-process
only — see the manager module docstring. No Redis in v1.
"""

from app.realtime.manager import ConnectionManager, manager

__all__ = ["ConnectionManager", "manager"]
