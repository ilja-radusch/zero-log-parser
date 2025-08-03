"""Command line interface for Zero Log Parser."""

import argparse
import logging
import os
import sys
from typing import Optional

from . import __version__
from .utils import console_logger, default_parsed_output_for

# Import the standalone parsing logic
import sys
import os
import importlib.util

def load_standalone_parser():
    """Load the standalone zero_log_parser.py module dynamically."""
    standalone_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'zero_log_parser.py')
    if not os.path.exists(standalone_path):
        raise ImportError(f"Standalone parser not found at {standalone_path}")
    
    spec = importlib.util.spec_from_file_location("standalone_parser", standalone_path)
    standalone_parser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(standalone_parser)
    return standalone_parser


def create_parser() -> argparse.ArgumentParser:
    """Create the command line argument parser."""
    parser = argparse.ArgumentParser(
        description="Parse Zero Motorcycle log files into human-readable format",
        epilog="For more information, visit: https://github.com/ilja-radusch/zero-log-parser"
    )
    
    parser.add_argument(
        'input_file',
        help="Input log file (.bin)"
    )
    
    parser.add_argument(
        '-o', '--output',
        help="Output file (default: input filename with .txt extension)"
    )
    
    parser.add_argument(
        '-f', '--format',
        choices=['txt', 'csv', 'tsv', 'json'],
        default='txt',
        help="Output format (default: txt)"
    )
    
    parser.add_argument(
        '-t', '--timezone',
        type=float,
        help="Timezone offset in hours from UTC (default: system timezone)"
    )
    
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help="Enable verbose output"
    )
    
    parser.add_argument(
        '--version',
        action='version',
        version=f'zero-log-parser {__version__}'
    )
    
    return parser


def validate_input_file(file_path: str) -> None:
    """Validate the input file exists and is readable."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Input file not found: {file_path}")
    
    if not os.path.isfile(file_path):
        raise ValueError(f"Input path is not a file: {file_path}")
    
    if not os.access(file_path, os.R_OK):
        raise PermissionError(f"Cannot read input file: {file_path}")


def determine_output_file(input_file: str, output_file: Optional[str], format_type: str) -> str:
    """Determine the output file path."""
    if output_file:
        return output_file
    
    # Generate default output filename based on format
    base_name = os.path.splitext(input_file)[0]
    extensions = {
        'txt': '.txt',
        'csv': '.csv',
        'tsv': '.tsv',
        'json': '.json'
    }
    
    return base_name + extensions.get(format_type, '.txt')


def setup_logging(verbose: bool) -> logging.Logger:
    """Setup logging configuration."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(name)s] [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    return logging.getLogger('zero-log-parser')


def main() -> int:
    """Main entry point for the CLI."""
    try:
        # Parse command line arguments
        parser = create_parser()
        args = parser.parse_args()
        
        # Setup logging
        logger = setup_logging(args.verbose)
        
        # Validate input file
        validate_input_file(args.input_file)
        
        # Determine output file
        output_file = determine_output_file(args.input_file, args.output, args.format)
        
        # Check if output directory exists
        output_dir = os.path.dirname(output_file)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
        
        # Parse the log file
        logger.info(f"Parsing {args.input_file} -> {output_file} (format: {args.format})")
        
        # Use standalone parser logic for consistency
        try:
            standalone_parser = load_standalone_parser()
            
            # Create a LogFile object first, then LogData using standalone logic
            log_file = standalone_parser.LogFile(args.input_file)
            # Handle timezone offset - use system default if not specified
            if args.timezone is not None:
                timezone_offset = args.timezone
            else:
                # Use the same logic as standalone for system timezone
                from datetime import datetime, timezone as dt_timezone
                local_now = datetime.now()
                utc_now = datetime.now(dt_timezone.utc).replace(tzinfo=None)
                timezone_offset = (local_now - utc_now).total_seconds() / 3600
            log_data = standalone_parser.LogData(log_file, timezone_offset=timezone_offset)
            
            # Generate output using standalone methods
            if args.format == 'csv':
                log_data.emit_tabular_decoding(output_file, out_format='csv')
            elif args.format == 'tsv':
                log_data.emit_tabular_decoding(output_file, out_format='tsv')
            elif args.format == 'json':
                log_data.emit_json_decoding(output_file)
            else:  # Default to txt
                log_data.emit_zero_compatible_decoding(output_file)
                
        except Exception as e:
            logger.error(f"Error using standalone parser: {e}")
            # Fallback to package parser
            from .core import parse_log
            parse_log(
                log_file=args.input_file,
                output_file=output_file,
                utc_offset_hours=args.timezone,
                verbose=args.verbose,
                logger=None,
                output_format=args.format
            )
        
        logger.info("Parsing completed successfully")
        return 0
        
    except KeyboardInterrupt:
        print("\nOperation cancelled by user", file=sys.stderr)
        return 130
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    except PermissionError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 13
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        if '--verbose' in sys.argv or '-v' in sys.argv:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())