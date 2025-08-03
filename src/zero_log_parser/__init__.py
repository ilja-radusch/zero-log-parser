"""Zero Motorcycle Log Parser.

A modern parser for Zero Motorcycle log files with structured data extraction.
Supports both MBB (Main Bike Board) and BMS (Battery Management System) logs.
"""

from .core import LogData, parse_log
from .parser import BinaryTools, Gen2, Gen3
from .message_parser import improve_message_parsing, determine_log_level

__version__ = "2.0.1"
__all__ = [
    "LogData",
    "parse_log", 
    "BinaryTools",
    "Gen2",
    "Gen3",
    "improve_message_parsing",
    "determine_log_level",
]