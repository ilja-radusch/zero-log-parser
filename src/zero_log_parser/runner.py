"""Top-level orchestration: parse single or multiple Zero logs into output files.

console_logger / is_log_file_path are kept here (verbatim from the original
standalone script) so the public parse_log/parse_multiple_logs API and the
standalone entry keep their exact logging and file-acceptance behavior. utils.py
carries its own (CLI-facing) variants; the two are intentionally not merged
because they differ in signature and handler behavior.
"""

import logging
import os

from .models import LogData, LogFile, MismatchingVinError
from .utils import get_timezone_offset


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


def is_log_file_path(file_path: str):
    return file_path.endswith('.bin')


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
