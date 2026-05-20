"""
Lightweight console logger with role-based ANSI colours.
"""
import sys
from datetime import datetime

# 256-colour ANSI codes — readable on both dark and light terminals.
COLORS = {
    "inspector":    "\033[38;5;39m",   # cyan-blue
    "parser":       "\033[38;5;208m",  # orange
    "ir":           "\033[38;5;141m",  # violet
    "doc":          "\033[38;5;76m",   # green
    "data":         "\033[38;5;220m",  # gold
    "rules":        "\033[38;5;208m",  # orange
    "cartographer": "\033[38;5;37m",   # teal
    "validator":    "\033[38;5;160m",  # red
    "migration":    "\033[38;5;165m",  # magenta-pink
    "pipeline":     "\033[38;5;39m",   # cyan-blue
    "error":        "\033[38;5;196m",  # bright red
    "info":         "\033[37m",        # light gray
}
RESET = "\033[0m"


def log(role: str, message: str) -> None:
    """Print a timestamped, role-coloured message."""
    color = COLORS.get(role, "")
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{role:>12}] {message}{RESET}", file=sys.stdout, flush=True)
