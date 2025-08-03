"""Zero Motorcycle Log Parser.

A modern parser for Zero Motorcycle log files with structured data extraction.
Supports both MBB (Main Bike Board) and BMS (Battery Management System) logs.
"""

from .core import LogData, parse_log

__version__ = "2.1.0"
__all__ = [
    "LogData",
    "parse_log", 
]
