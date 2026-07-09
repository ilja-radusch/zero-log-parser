"""Public API facade for zero_log_parser.

New code should import from the focused modules directly; this module re-exports
the stable public surface for backward compatibility.
"""

from .binary import BinaryTools, is_vin
from .constants import REV0, REV1, REV2, REV3, REV4
from .gen2 import Gen2
from .gen3 import Gen3
from .models import LogData, LogFile, MismatchingVinError, ProcessedLogEntry
from .runner import generate_merged_output_name, parse_log, parse_multiple_logs

__all__ = [
    "BinaryTools",
    "is_vin",
    "REV0",
    "REV1",
    "REV2",
    "REV3",
    "REV4",
    "Gen2",
    "Gen3",
    "LogData",
    "LogFile",
    "MismatchingVinError",
    "ProcessedLogEntry",
    "parse_log",
    "parse_multiple_logs",
    "generate_merged_output_name",
]
