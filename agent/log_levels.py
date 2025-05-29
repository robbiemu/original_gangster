# agent/log_levels.py
from enum import IntEnum


class LogLevel(IntEnum):
    DEBUG = 0
    INFO = 1
    WARN = 2
    NONE = 3
