"""Zero Motorcycle Log Parser.

A modern parser for Zero Motorcycle log files with structured data extraction.
Supports both MBB (Main Bike Board) and BMS (Battery Management System) logs.
"""

from .core import LogData, parse_log

# Plotting is optional - only import if dependencies are available
try:
    from .plotting import ZeroLogPlotter
    _has_plotting = True
    __all__ = [
        "LogData",
        "parse_log",
        "ZeroLogPlotter",
    ]
except ImportError:
    _has_plotting = False
    __all__ = [
        "LogData", 
        "parse_log",
    ]

__version__ = "2.1.0"
