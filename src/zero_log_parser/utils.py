"""Utility functions for Zero log parsing."""

import logging
import os
import string
from datetime import datetime, timezone
from typing import Union, List


# Localized time format - use system locale preference
ZERO_TIME_FORMAT = '%Y-%m-%d %H:%M:%S'  # ISO format is more universal
# The output from the MBB (via serial port) lists time as GMT-7
MBB_TIMESTAMP_GMT_OFFSET = -7 * 60 * 60


def get_local_timezone_offset() -> int:
    """Get the local system timezone offset in seconds from UTC"""
    local_now = datetime.now()
    utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Calculate offset in seconds
    offset = (local_now - utc_now).total_seconds()
    return int(offset)


def is_vin(vin: str) -> bool:
    """Check if a string looks like a VIN number."""
    if len(vin) != 17:
        return False
    if not all(c in string.ascii_uppercase + string.digits for c in vin):
        return False
    return True


def convert_mv_to_v(milli_volts: int) -> float:
    """Convert millivolts to volts."""
    return round(milli_volts / 1000.0, 3)


def convert_ratio_to_percent(numerator: Union[int, float], denominator: Union[int, float]) -> float:
    """Convert a ratio to percentage."""
    return round((numerator / denominator) * 100.0, 1)


def convert_bit_to_on_off(bit: int) -> str:
    """Convert bit value to On/Off string."""
    return 'On' if bit else 'Off'


def hex_of_value(value) -> str:
    """Return hex representation of a value."""
    if hasattr(value, '__iter__') and not isinstance(value, (str, bytes)):
        return ', '.join(f'0x{v:02x}' for v in value)
    elif isinstance(value, int):
        return f'0x{value:02x}'
    else:
        return str(value)


def display_bytes_hex(x: Union[List[int], bytearray, bytes, str]) -> str:
    """Display bytes as hex string."""
    if isinstance(x, str):
        x = x.encode('utf-8')
    if isinstance(x, (bytes, bytearray)):
        x = list(x)
    return ' '.join(f'{b:02x}' for b in x)


def print_value_tabular(value, omit_units=False) -> str:
    """Format value for tabular output."""
    if isinstance(value, dict):
        if omit_units:
            return str(value)
        return str(value)
    elif isinstance(value, (list, tuple)):
        return ', '.join(str(v) for v in value)
    else:
        return str(value)


def default_parsed_output_for(bin_file_path: str) -> str:
    """Generate default output filename for a binary log file."""
    return os.path.splitext(bin_file_path)[0] + '.txt'


def is_log_file_path(file_path: str) -> bool:
    """Check if a file path looks like a log file."""
    return file_path.lower().endswith(('.bin', '.log'))


def console_logger(name: str, verbose: bool = False) -> logging.Logger:
    """Create a console logger."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            '%(asctime)s [%(name)s] [%(levelname)s] %(message)s'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    
    return logger


def logger_for_input(bin_file) -> logging.Logger:
    """Create a logger for a specific input file."""
    if hasattr(bin_file, 'name'):
        log_name = os.path.basename(bin_file.name)
    else:
        log_name = str(bin_file)
    return console_logger(log_name)