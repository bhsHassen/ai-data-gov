"""
Colored console output — one color per agent.
Uses colorama for Windows compatibility.
"""
from __future__ import annotations

try:
    from colorama import Fore, Style, init
    init(autoreset=True)
    _COLORAMA = True
except ImportError:
    _COLORAMA = False


# Color mapping per agent
COLORS = {
    "collector":  "\033[94m",   # Blue
    "analyst":    "\033[96m",   # Cyan
    "judge":      "\033[93m",   # Yellow
    "validator":  "\033[92m",   # Green
    "writer":     "\033[95m",   # Magenta
    "router":     "\033[90m",   # Gray
    "error":      "\033[91m",   # Red
}
RESET = "\033[0m"
BOLD  = "\033[1m"


def log(agent: str, message: str) -> None:
    """Print a colored log line for the given agent."""
    color  = COLORS.get(agent, "")
    prefix = f"{BOLD}{color}[{agent.upper():10}]{RESET}{color}"
    print(f"{prefix} {message}{RESET}")
