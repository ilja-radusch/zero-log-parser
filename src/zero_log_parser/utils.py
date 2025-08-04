"""Utility functions for Zero log parsing."""

import logging
import os
import re
import string
from datetime import datetime, timezone, timedelta
from typing import Union, List, Optional


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


def parse_time_filter(time_str: str) -> Optional[datetime]:
    """Parse flexible time filter strings into datetime objects.
    
    Supports formats like:
    - "last month", "last week", "last 30 days"
    - "June 2025", "December 2024"
    - "2025-06-15", "2025-06-15 14:30"
    - "June 15, 2025"
    - ISO formats
    """
    if not time_str:
        return None
    
    time_str = time_str.strip().lower()
    now = datetime.now()
    
    # Handle relative dates
    if time_str.startswith('last '):
        relative_part = time_str[5:]  # Remove "last "
        
        if relative_part == 'week':
            return now - timedelta(weeks=1)
        elif relative_part == 'month':
            # Approximate month as 30 days
            return now - timedelta(days=30)
        elif relative_part == 'year':
            return now - timedelta(days=365)
        elif relative_part.endswith(' days'):
            try:
                days = int(relative_part.split()[0])
                return now - timedelta(days=days)
            except (ValueError, IndexError):
                pass
        elif relative_part.endswith(' weeks'):
            try:
                weeks = int(relative_part.split()[0])
                return now - timedelta(weeks=weeks)
            except (ValueError, IndexError):
                pass
        elif relative_part.endswith(' months'):
            try:
                months = int(relative_part.split()[0])
                return now - timedelta(days=months * 30)  # Approximate
            except (ValueError, IndexError):
                pass
    
    # Handle "Month Year" format (e.g., "June 2025")
    month_year_pattern = r'^([a-z]+)\s+(\d{4})$'
    match = re.match(month_year_pattern, time_str)
    if match:
        month_name, year = match.groups()
        month_names = {
            'january': 1, 'jan': 1, 'february': 2, 'feb': 2, 'march': 3, 'mar': 3,
            'april': 4, 'apr': 4, 'may': 5, 'june': 6, 'jun': 6,
            'july': 7, 'jul': 7, 'august': 8, 'aug': 8, 'september': 9, 'sep': 9,
            'october': 10, 'oct': 10, 'november': 11, 'nov': 11, 'december': 12, 'dec': 12
        }
        if month_name in month_names:
            return datetime(int(year), month_names[month_name], 1)
    
    # Try various datetime formats
    formats_to_try = [
        '%Y-%m-%d %H:%M:%S',  # 2025-06-15 14:30:00
        '%Y-%m-%d %H:%M',     # 2025-06-15 14:30
        '%Y-%m-%d',           # 2025-06-15
        '%m/%d/%Y %H:%M:%S',  # 06/15/2025 14:30:00
        '%m/%d/%Y %H:%M',     # 06/15/2025 14:30
        '%m/%d/%Y',           # 06/15/2025
        '%d/%m/%Y %H:%M:%S',  # 15/06/2025 14:30:00
        '%d/%m/%Y %H:%M',     # 15/06/2025 14:30
        '%d/%m/%Y',           # 15/06/2025
        '%B %d, %Y',          # June 15, 2025
        '%b %d, %Y',          # Jun 15, 2025
        '%Y-%m-%dT%H:%M:%S',  # ISO format
    ]
    
    for fmt in formats_to_try:
        try:
            return datetime.strptime(time_str, fmt)
        except ValueError:
            continue
    
    # If nothing worked, raise an error
    raise ValueError(f"Unable to parse time filter: '{time_str}'. "
                    f"Supported formats: 'last month', 'June 2025', '2025-06-15', etc.")


def apply_time_filter(df, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None):
    """Apply time filtering to a DataFrame with a 'timestamp' column.
    
    Args:
        df: DataFrame with 'timestamp' column
        start_time: Filter data after this time (inclusive)
        end_time: Filter data before this time (inclusive)
    
    Returns:
        Filtered DataFrame
    """
    if df.empty or 'timestamp' not in df.columns:
        return df
    
    # Convert timestamp column to datetime if it's not already
    try:
        import pandas as pd
        if not hasattr(df['timestamp'].dtype, 'tz'):
            df['timestamp'] = pd.to_datetime(df['timestamp'])
    except ImportError:
        # If pandas is not available, assume timestamps are already datetime objects
        pass
    
    filtered_df = df.copy()
    
    if start_time:
        filtered_df = filtered_df[filtered_df['timestamp'] >= start_time]
    
    if end_time:
        filtered_df = filtered_df[filtered_df['timestamp'] <= end_time]
    
    return filtered_df