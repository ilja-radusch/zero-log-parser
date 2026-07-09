#!/usr/bin/env python3

"""
Little decoder utility to parse Zero Motorcycle main bike board (MBB) and
battery management system (BMS) logs. These may be extracted from the bike
using the Zero mobile app. Once paired over bluetooth, select 'Support' >
'Email bike logs' and send the logs to yourself rather than / in addition to
zero support.

Usage:

   $ python zero_log_parser.py <*.bin file> [-o output_file]

Architecture:
This parser uses a hybrid approach combining Gen2/Gen3 binary parsing with
structured data extraction. Gen2 parsers have been optimized to generate
structured data directly from binary data, eliminating the "string formatting
→ regex re-parsing" overhead that existed previously.

Gen2 Parser Optimization - Completed (19 methods):
- Phase 1: disarmed_status(), bms_contactor_state(), bms_soc_adj_voltage()
- Phase 2: battery_status(), bms_discharge_cut(), bms_contactor_drive()
- Phase 3: bms_curr_sens_zero(), sevcon_status(), bms_isolation_fault(), power_state()
- Phase 4: charger_status(), bms_reflash(), key_state()
- Phase 5: battery_discharge_current_limited(), low_chassis_isolation(), sevcon_power_state(), battery_contactor_closed()
- Additional: vehicle_state_telemetry(), sensor_data()

All optimized parsers use direct binary extraction via BinaryTools.unpack() and
generate structured JSON data with human-readable conditions for all output formats.
This eliminates the previous "string formatting → regex re-parsing" overhead while
maintaining full backward compatibility. Redundant patterns removed from improve_message_parsing.

"""

import codecs
import json
import logging
import os
import re
import string
import struct
import sys
from collections import OrderedDict, namedtuple
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import trunc
from time import gmtime, localtime, strftime
from typing import Dict, List, Union, Optional

# Ensure the `zero_log_parser` package under src/ is importable when this file
# is run as a standalone script from any working directory (uninstalled). This
# lets the imports below resolve to the package rather than re-importing this
# root file. Inserted at position 0 so the package wins over the root module.
_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
# Force src/ to the FRONT: when run as a script the repo root (containing this
# file) is sys.path[0] and would otherwise re-import this root module as the
# `zero_log_parser` package. Move any pre-existing entry (e.g. from an editable
# install) ahead of the script directory.
if _SRC in sys.path:
    sys.path.remove(_SRC)
sys.path.insert(0, _SRC)

# Parser version - single-sourced from the package.
from zero_log_parser import __version__ as PARSER_VERSION

# Constants, binary tools, and value formatters now live in the package.
from zero_log_parser.constants import (
    ZERO_TIME_FORMAT, MBB_TIMESTAMP_GMT_OFFSET, EMPTY_CSV_VALUE, CSV_DELIMITER,
    REV0, REV1, REV2, REV3, REV4,
)
from zero_log_parser.binary import (
    BinaryTools, is_vin, vin_length, vin_guaranteed_prefix,
    convert_mv_to_v, convert_ratio_to_percent, convert_bit_to_on_off,
    hex_of_value, display_bytes_hex, print_value_tabular,
)
from zero_log_parser.parsing import improve_message_parsing, determine_log_level
from zero_log_parser.gen2 import Gen2
from zero_log_parser.gen3 import Gen3
from zero_log_parser.models import (
    MismatchingVinError, ProcessedLogEntry, LogFile, LogData,
)




def _load_utils():
    """Return the zero_log_parser.utils module.

    src/ is guaranteed on sys.path (see the bootstrap at the top of this file),
    so this is now a plain package import. Retained as a thin wrapper for the
    existing call sites in LogData and main().
    """
    import zero_log_parser.utils as _u
    return _u


from zero_log_parser.utils import get_timezone_offset


def parse_log(bin_file: str, output_file: str, tz_code=None, verbosity_level=1, verbose=None, logger=None, output_format='txt', start_time=None, end_time=None, unnest=False):
    """
    Parse a Zero binary log file into a human readable text file
    """
    # Handle backward compatibility for verbose parameter
    if verbose is not None:
        verbosity_level = 2 if verbose else 1

    if not logger:
        logger = console_logger(bin_file, verbosity_level=verbosity_level)
    logger.info('Parsing %s', bin_file)

    timezone_offset = get_timezone_offset(tz_code)

    log = LogFile(bin_file)
    log_data = LogData(log, timezone_offset=timezone_offset, verbosity_level=verbosity_level)

    if output_format.lower() in ['csv', 'tsv']:
        # Generate CSV/TSV output
        log_data.emit_tabular_decoding(output_file, out_format=output_format.lower(), start_time=start_time, end_time=end_time, unnest=unnest)
    elif output_format.lower() == 'json':
        # Generate JSON output
        log_data.emit_json_decoding(output_file, start_time=start_time, end_time=end_time)
    elif output_format.lower() == 'txt':
        # Generate standard text output
        if log_data.has_official_output_reference():
            log_data.emit_zero_compatible_decoding(output_file, start_time=start_time, end_time=end_time)
        else:
            log_data.emit_tabular_decoding(output_file, start_time=start_time, end_time=end_time, unnest=unnest)
            log_data.emit_zero_compatible_decoding(output_file, start_time=start_time, end_time=end_time)
    else:
        # Default to text format for unknown formats
        if log_data.has_official_output_reference():
            log_data.emit_zero_compatible_decoding(output_file, start_time=start_time, end_time=end_time)
        else:
            log_data.emit_tabular_decoding(output_file, start_time=start_time, end_time=end_time, unnest=unnest)
            log_data.emit_zero_compatible_decoding(output_file, start_time=start_time, end_time=end_time)


def default_parsed_output_for(bin_file_path: str):
    return os.path.splitext(bin_file_path)[0] + '.txt'


def is_log_file_path(file_path: str):
    return file_path.endswith('.bin')


def console_logger(name: str, verbosity_level=1, verbose=None):
    # Handle backward compatibility for verbose parameter
    if verbose is not None:
        verbosity_level = 2 if verbose else 1

    # Map verbosity levels to logging levels
    level_map = {
        0: logging.ERROR,    # Quiet - only errors
        1: logging.INFO,     # Normal - info and above
        2: logging.DEBUG,    # Verbose - debug and above
        3: logging.DEBUG,    # Very verbose - same as verbose for logging
        4: logging.DEBUG     # Debug - same as verbose for logging
    }

    log_level = level_map.get(verbosity_level, logging.INFO)
    logger = logging.Logger(name, level=log_level)
    logger_formatter = logging.Formatter('%(asctime)s [%(name)s] [%(levelname)s] %(message)s')
    logger_handler = logging.StreamHandler()
    logger_handler.setFormatter(logger_formatter)
    logger.addHandler(logger_handler)
    return logger


def logger_for_input(bin_file):
    return logging.getLogger(bin_file)


def generate_merged_output_name(bin_files, output_format='txt'):
    """Generate a meaningful output filename for merged log files"""
    import os
    from datetime import datetime

    # Extract common elements from filenames
    basenames = [os.path.basename(f) for f in bin_files]

    # Try to find common VIN/serial pattern
    common_vin = None
    for name in basenames:
        if len(name) > 17 and name.startswith('538'):  # Zero VIN pattern
            vin_candidate = name[:17]
            if all(vin_candidate in other_name for other_name in basenames):
                common_vin = vin_candidate
                break

    # Create descriptive filename
    if common_vin:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M')
        filename = f"{common_vin}_merged_{len(bin_files)}files_{timestamp}.{output_format}"
    else:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M')
        filename = f"zero_logs_merged_{len(bin_files)}files_{timestamp}.{output_format}"

    return filename


def parse_multiple_logs(bin_files, output_file, tz_code=None, verbosity_level=1, verbose=None, logger=None, output_format='txt', start_time=None, end_time=None, unnest=False):
    """
    Parse multiple Zero binary log files and merge them intelligently

    Args:
        bin_files: List of binary log file paths
        output_file: Output filename for merged result
        tz_code: Timezone offset
        verbose: Enable verbose logging
        logger: Logger instance
        output_format: Output format (txt, csv, tsv, json)
    """
    # Handle backward compatibility for verbose parameter
    if verbose is not None:
        verbosity_level = 2 if verbose else 1

    if not logger:
        logger = console_logger(' + '.join(bin_files), verbosity_level=verbosity_level)

    logger.info('Multi-file parsing: %d files', len(bin_files))

    # Validate all files exist and are readable
    for bin_file in bin_files:
        if not os.path.exists(bin_file):
            raise FileNotFoundError(f"Log file not found: {bin_file}")
        if not is_log_file_path(bin_file):
            logger.warning(f"File may not be a valid log file: {bin_file}")

    merged_log_data = None
    successful_files = 0

    # Convert timezone offset to seconds
    timezone_offset = get_timezone_offset(tz_code)

    # Process each file and merge
    for i, bin_file in enumerate(bin_files):
        try:
            logger.info('[%d/%d] Processing %s', i+1, len(bin_files), bin_file)

            # Parse individual file to LogData
            log_file = LogFile(bin_file, logger=logger)
            log_data = LogData(log_file, timezone_offset=timezone_offset, verbosity_level=verbosity_level)

            if merged_log_data is None:
                # First file becomes the base
                merged_log_data = log_data
                successful_files = 1
                logger.info('Base log data: %s entries', len(log_data.entries) if hasattr(log_data, 'entries') else 'unknown')
            else:
                # Merge with existing data using LogData's smart merging
                try:
                    merged_log_data = merged_log_data + log_data
                    successful_files += 1
                    logger.info('Merged successfully. Total entries: %s',
                              len(merged_log_data.entries) if hasattr(merged_log_data, 'entries') else 'unknown')
                except MismatchingVinError as e:
                    logger.error('VIN mismatch: %s', e)
                    logger.error('Skipping file: %s', bin_file)
                    continue
                except Exception as e:
                    logger.error('Failed to merge %s: %s', bin_file, e)
                    logger.error('Skipping file: %s', bin_file)
                    continue

        except Exception as e:
            logger.error('Failed to process %s: %s', bin_file, e)
            continue

    if merged_log_data is None:
        raise RuntimeError("No files could be processed successfully")

    if successful_files < len(bin_files):
        logger.warning('Successfully merged %d out of %d files', successful_files, len(bin_files))
    else:
        logger.info('Successfully merged all %d files', successful_files)

    # Output merged result
    logger.info('Writing merged log to %s', output_file)

    if output_format.lower() in ['csv', 'tsv']:
        # Generate CSV/TSV output
        merged_log_data.emit_tabular_decoding(output_file, out_format=output_format.lower(), start_time=start_time, end_time=end_time, unnest=unnest)
    elif output_format.lower() == 'json':
        # Generate JSON output
        merged_log_data.emit_json_decoding(output_file, start_time=start_time, end_time=end_time)
    elif output_format.lower() == 'txt':
        # Generate standard text output
        if merged_log_data.has_official_output_reference():
            merged_log_data.emit_zero_compatible_decoding(output_file, start_time=start_time, end_time=end_time)
        else:
            merged_log_data.emit_tabular_decoding(output_file, start_time=start_time, end_time=end_time, unnest=unnest)
            merged_log_data.emit_zero_compatible_decoding(output_file, start_time=start_time, end_time=end_time)
    else:
        # Default to text format for unknown formats
        if merged_log_data.has_official_output_reference():
            merged_log_data.emit_zero_compatible_decoding(output_file, start_time=start_time, end_time=end_time)
        else:
            merged_log_data.emit_tabular_decoding(output_file, start_time=start_time, end_time=end_time, unnest=unnest)
            merged_log_data.emit_zero_compatible_decoding(output_file, start_time=start_time, end_time=end_time)

    logger.info('Multi-file parsing completed: %s', output_file)


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description='Parse Zero Motorcycle binary log files. Supports single or multiple files with smart merging.',
        epilog='For interactive plotting, use: zero-plotting <input_files>')
    parser.add_argument('bin_files', nargs='+', help='Zero *.bin log file(s) to decode. Multiple files will be intelligently merged.')
    parser.add_argument('--timezone', help='Timezone offset in hours from UTC (e.g., -8 for PST, +1 for CET) or timezone name (e.g., "Europe/Berlin", "US/Pacific"). Defaults to local system timezone.')
    parser.add_argument('-o', '--output', help='decoded log filename (auto-generated if not specified)')
    parser.add_argument('-f', '--format', choices=['txt', 'csv', 'tsv', 'json'], default='txt',
                       help='Output format: txt (default), csv, tsv, or json')
    parser.add_argument('--unnest', action='store_true',
                       help='For CSV/TSV formats: expand structured data into separate rows with condition_key and condition_value columns')
    parser.add_argument('--start', help='Filter entries after this time (e.g., "June 2025", "2025-06-15", "last month")')
    parser.add_argument('--end', help='Filter entries before this time (e.g., "June 2025", "2025-06-15", "last month")')
    parser.add_argument('--start-end', help='Filter entries within this period (e.g., "June 2025" sets both start and end boundaries automatically)')
    parser.add_argument('-v', '--verbose', action='count', default=1, help='Increase verbosity level (-v, -vv, -vvv for levels 2, 3, 4)')
    parser.add_argument('-q', '--quiet', action='store_true', help='Quiet mode (verbosity level 0)')
    args = parser.parse_args()

    # Calculate verbosity level
    if args.quiet:
        verbosity_level = 0
    else:
        verbosity_level = args.verbose

    # Handle single vs multiple files
    bin_files = args.bin_files
    tz_code = args.timezone
    output_format = args.format

    # Parse time filtering parameters
    start_time = None
    end_time = None

    # Handle --start-end shorthand
    if args.start_end:
        if args.start or args.end:
            parser.error("--start-end cannot be used with --start or --end")
        try:
            # Load time parsing utilities from wherever they live
            _utils = _load_utils()
            if _utils is None:
                raise ValueError("Time filtering requires the zero_log_parser.utils module")
            start_time, end_time = _utils.parse_time_range(args.start_end, tz_code)
        except Exception as e:
            parser.error(f"Invalid --start-end time specification: {e}")
    else:
        # Handle individual --start and --end parameters
        if args.start:
            try:
                _utils = _load_utils()
                if _utils is None:
                    raise ValueError("Time filtering requires the zero_log_parser.utils module")
                start_time = _utils.parse_time_filter_start(args.start, tz_code)
            except Exception as e:
                parser.error(f"Invalid --start time specification: {e}")

        if args.end:
            try:
                _utils = _load_utils()
                if _utils is None:
                    raise ValueError("Time filtering requires the zero_log_parser.utils module")
                end_time = _utils.parse_time_filter_end(args.end, tz_code)
            except Exception as e:
                parser.error(f"Invalid --end time specification: {e}")

    if len(bin_files) == 1:
        # Single file - use existing behavior
        bin_file = bin_files[0]
        output_file = args.output or default_parsed_output_for(bin_file)
        parse_log(bin_file, output_file, tz_code=tz_code, verbosity_level=verbosity_level, output_format=output_format,
                  start_time=start_time, end_time=end_time, unnest=args.unnest)
    else:
        # Multiple files - use new multi-file parsing
        output_file = args.output or generate_merged_output_name(bin_files, output_format)
        parse_multiple_logs(bin_files, output_file,
                            tz_code=tz_code, verbosity_level=verbosity_level, output_format=output_format,
                            start_time=start_time, end_time=end_time, unnest=args.unnest)


if __name__ == '__main__':
    main()
