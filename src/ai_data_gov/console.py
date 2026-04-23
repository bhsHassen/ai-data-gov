"""
Colored console output — one color per agent.
Uses colorama for Windows compatibility.
Optionally streams events to a queue (used by the web dashboard).
"""
from __future__ import annotations

import queue as _queue_module
from typing import Optional

try:
    from colorama import Fore, Style, init
    init(autoreset=True)
    _COLORAMA = True
except ImportError:
    _COLORAMA = False


# Color mapping per agent
COLORS = {
    "collector":   "\033[94m",       # Blue
    "analyst":     "\033[96m",       # Cyan
    "judge":       "\033[93m",       # Yellow
    "validator":   "\033[92m",       # Green
    "writer":      "\033[95m",       # Magenta
    "router":      "\033[90m",       # Gray
    "error":       "\033[91m",       # Red
    # Code-generation pipeline
    "loader":      "\033[94m",       # Blue (reuse)
    "developer":   "\033[38;5;208m", # Orange (256-color)
    "reviewer":    "\033[38;5;37m",  # Teal   (256-color)
    "code_writer": "\033[95m",       # Magenta (reuse of writer)
}
RESET = "\033[0m"
BOLD  = "\033[1m"

# Optional event queue — set by the dashboard before each run
_event_queue: Optional[_queue_module.Queue] = None


def attach_queue(q: _queue_module.Queue) -> None:
    """Attach a queue — all log/emit calls will push events to it."""
    global _event_queue
    _event_queue = q


def detach_queue() -> None:
    global _event_queue
    _event_queue = None


def emit_event(data: dict) -> None:
    """Push a structured event to the dashboard queue (no-op if not attached)."""
    if _event_queue is not None:
        try:
            _event_queue.put_nowait(data)
        except Exception:
            pass


def log(agent: str, message: str) -> None:
    """Print a colored log line and push a log event to the dashboard."""
    color  = COLORS.get(agent, "")
    prefix = f"{BOLD}{color}[{agent.upper():10}]{RESET}{color}"
    print(f"{prefix} {message}{RESET}")
    emit_event({"type": "log", "agent": agent, "message": message})
