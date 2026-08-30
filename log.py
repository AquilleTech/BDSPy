"""
Small leveled/colored logger. No external deps -- just ANSI escape codes,
which Termux's terminal supports fine. Falls back to no color when stdout
isn't a tty (e.g. piped to a file) so log files stay clean.

Usage:
    from log import debug, info, warn, error
    debug("parsed login", tag="login")
    warn(f"packet id {pid} arrived out of order (expected {expected})", tag="session")

DEBUG is off by default -- turn it on via set_level("DEBUG") or config.toml's
[logging] section.
"""

import sys
import time

LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}
_current_level = LEVELS["INFO"]

_USE_COLOR = sys.stdout.isatty()

_COLOR = {
    "DEBUG": "\033[36m",   # cyan
    "INFO": "\033[32m",    # green
    "WARN": "\033[33m",    # yellow
    "ERROR": "\033[31m",   # red
}
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"


def set_level(level_name):
    global _current_level
    level_name = level_name.upper()
    if level_name not in LEVELS:
        raise ValueError(f"unknown log level: {level_name}")
    _current_level = LEVELS[level_name]


def _emit(level, message, tag=None):
    if LEVELS[level] < _current_level:
        return
    ts = time.strftime("%H:%M:%S")
    tag_part = f"{tag} " if tag else ""

    if _USE_COLOR:
        color = _COLOR[level]
        line = f"{_DIM}{ts}{_RESET} {color}{_BOLD}{level:5}{_RESET} {_DIM}{tag_part}{_RESET}{message}"
    else:
        line = f"{ts} {level:5} {tag_part}{message}"

    stream = sys.stderr if level in ("WARN", "ERROR") else sys.stdout
    print(line, file=stream)


def debug(message, tag=None):
    _emit("DEBUG", message, tag)


def info(message, tag=None):
    _emit("INFO", message, tag)


def warn(message, tag=None):
    _emit("WARN", message, tag)


def error(message, tag=None):
    _emit("ERROR", message, tag)
