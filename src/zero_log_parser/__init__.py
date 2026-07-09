"""Zero Motorcycle Log Parser.

A modern parser for Zero Motorcycle log files with structured data extraction.
Supports both MBB (Main Bike Board) and BMS (Battery Management System) logs.
"""

# Single source of truth for the version. Defined BEFORE importing .core so
# that the (still circular, until the restructure completes) core -> monolith
# import chain can resolve `zero_log_parser.__version__`.
__version__ = "2.3.0-dev"

from .core import LogData, parse_log

# Don't import plotting at package level - it will be imported only when needed
__all__ = [
    "LogData",
    "parse_log",
]

def _get_plotter_class():
    """Lazy import of plotting functionality."""
    try:
        from .plotting import ZeroLogPlotter
        return ZeroLogPlotter
    except ImportError as e:
        raise ImportError("plotly and pandas are required for plotting. Install with: pip install -e \".[plotting]\"") from e
