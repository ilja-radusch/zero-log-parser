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