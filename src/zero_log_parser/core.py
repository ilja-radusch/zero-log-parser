"""
Core parsing logic for Zero Motorcycle log files.
This uses the proven working implementation from the standalone zero_log_parser.py
"""

import os
import sys
import importlib.util
from typing import Optional


def _load_standalone_parser():
    """Load the standalone zero_log_parser.py module dynamically."""
    standalone_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'zero_log_parser.py')
    if not os.path.exists(standalone_path):
        raise ImportError(f"Standalone parser not found at {standalone_path}")
    
    spec = importlib.util.spec_from_file_location("standalone_parser", standalone_path)
    standalone_parser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(standalone_parser)
    return standalone_parser


# Load the working standalone implementation
_standalone = _load_standalone_parser()

# Export the working classes and functions directly
BinaryTools = _standalone.BinaryTools
LogFile = _standalone.LogFile
LogData = _standalone.LogData
Gen2 = _standalone.Gen2
Gen3 = _standalone.Gen3

# Export constants
REV0 = _standalone.REV0
REV1 = _standalone.REV1  
REV2 = _standalone.REV2
REV3 = _standalone.REV3

# Export utility functions
is_vin = _standalone.is_vin
get_local_timezone_offset = _standalone.get_local_timezone_offset

# Export exceptions
MismatchingVinError = _standalone.MismatchingVinError


def parse_log(log_file: str, output_file: str, utc_offset_hours: float = None,
              verbose: bool = False, logger=None, output_format: str = 'txt'):
    """
    Parse a log file using the working standalone implementation.
    
    Args:
        log_file: Path to the binary log file
        output_file: Path for the output file
        utc_offset_hours: Timezone offset in hours (uses system default if None)
        verbose: Enable verbose logging
        logger: Logger instance
        output_format: Output format ('txt', 'csv', 'tsv', 'json')
    """
    if not logger:
        logger = _standalone.logger_for_input(log_file)
        
    logger.info(f"Parsing {log_file}")
    
    # Handle timezone offset - use system default if not specified
    if utc_offset_hours is not None:
        # Convert hours to seconds (standalone expects seconds)
        timezone_offset = utc_offset_hours * 3600
    else:
        # Use the same logic as standalone for system timezone (returns seconds)
        timezone_offset = get_local_timezone_offset()
    
    # Create LogFile and LogData objects using the working standalone logic
    log_file_obj = LogFile(log_file)
    log_data = LogData(log_file_obj, timezone_offset=timezone_offset)
    
    # Generate output using standalone methods
    try:
        if output_format == 'csv':
            log_data.emit_tabular_decoding(output_file, out_format='csv')
        elif output_format == 'tsv':
            log_data.emit_tabular_decoding(output_file, out_format='tsv')
        elif output_format == 'json':
            log_data.emit_json_decoding(output_file)
        else:  # Default to txt
            log_data.emit_zero_compatible_decoding(output_file)
    except Exception as e:
        logger.error(f"Error generating output: {e}")
        raise
        
    logger.info(f"Output written to {output_file}")


def generate_merged_output_name(bin_files, output_format='txt'):
    """Generate a meaningful output filename for merged log files"""
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


def parse_multiple_logs(bin_files, output_file, utc_offset_hours=None, verbose=False, logger=None, output_format='txt'):
    """
    Parse multiple Zero binary log files and merge them intelligently
    
    Args:
        bin_files: List of binary log file paths
        output_file: Output filename for merged result
        utc_offset_hours: Timezone offset
        verbose: Enable verbose logging
        logger: Logger instance
        output_format: Output format (txt, csv, tsv, json)
    """
    if not logger:
        logger = _standalone.console_logger(' + '.join(bin_files), verbose=verbose)
    
    logger.info('Multi-file parsing: %d files', len(bin_files))
    
    # Validate all files exist and are readable
    for bin_file in bin_files:
        if not os.path.exists(bin_file):
            raise FileNotFoundError(f"Log file not found: {bin_file}")
        if not _standalone.is_log_file_path(bin_file):
            logger.warning(f"File may not be a valid log file: {bin_file}")
    
    merged_log_data = None
    successful_files = 0
    
    # Process each file and merge
    for i, bin_file in enumerate(bin_files):
        try:
            logger.info('[%d/%d] Processing %s', i+1, len(bin_files), bin_file)
            
            # Parse individual file to LogData
            log_file = LogFile(bin_file, logger=logger)
            
            # Convert timezone offset to seconds
            if isinstance(utc_offset_hours, int):
                timezone_offset = utc_offset_hours * 60 * 60
            elif utc_offset_hours is not None:
                try:
                    timezone_offset = float(utc_offset_hours) * 60 * 60
                except (ValueError, TypeError):
                    timezone_offset = get_local_timezone_offset()
            else:
                timezone_offset = get_local_timezone_offset()
            
            log_data = LogData(log_file, timezone_offset=timezone_offset)
            
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
        merged_log_data.emit_tabular_decoding(output_file, out_format=output_format.lower())
    elif output_format.lower() == 'json':
        # Generate JSON output
        merged_log_data.emit_json_decoding(output_file)
    elif output_format.lower() == 'txt':
        # Generate standard text output
        if merged_log_data.has_official_output_reference():
            merged_log_data.emit_zero_compatible_decoding(output_file)
        else:
            merged_log_data.emit_tabular_decoding(output_file)
            merged_log_data.emit_zero_compatible_decoding(output_file)
    else:
        # Default to text format for unknown formats
        if merged_log_data.has_official_output_reference():
            merged_log_data.emit_zero_compatible_decoding(output_file)
        else:
            merged_log_data.emit_tabular_decoding(output_file)
            merged_log_data.emit_zero_compatible_decoding(output_file)
    
    logger.info('Multi-file parsing completed: %s', output_file)