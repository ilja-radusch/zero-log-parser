"""Command line interface for Zero Log Parser."""

import argparse
import logging
import os
import sys
from typing import Optional

from . import __version__
from .core import parse_log, parse_multiple_logs, generate_merged_output_name
from .utils import console_logger, default_parsed_output_for, get_timezone_offset


def create_parser() -> argparse.ArgumentParser:
    """Create the command line argument parser."""
    parser = argparse.ArgumentParser(
        description="Parse Zero Motorcycle log files into human-readable format",
        epilog="For interactive plotting, use: zero-plotting <input_files>\n"
               "For more information, visit: https://github.com/ilja-radusch/zero-log-parser"
    )
    
    parser.add_argument(
        'input_files',
        nargs='+',
        help="Input log file(s) (.bin). Multiple files will be intelligently merged."
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
        help="Timezone offset in hours from UTC (e.g., -8, +1) or timezone name (e.g., Europe/Berlin, America/New_York). Default: system timezone"
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


def validate_input_files(file_paths: list) -> None:
    """Validate that all input files exist and are readable."""
    for file_path in file_paths:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Input file not found: {file_path}")
        
        if not os.path.isfile(file_path):
            raise ValueError(f"Input path is not a file: {file_path}")
        
        if not os.access(file_path, os.R_OK):
            raise PermissionError(f"Cannot read input file: {file_path}")


def determine_output_file(input_files: list, output_file: Optional[str], format_type: str) -> str:
    """Determine the output file path for single or multiple input files."""
    if output_file:
        return output_file
    
    if len(input_files) == 1:
        # Single file - use traditional naming
        base_name = os.path.splitext(input_files[0])[0]
        extensions = {
            'txt': '.txt',
            'csv': '.csv',
            'tsv': '.tsv',
            'json': '.json'
        }
        return base_name + extensions.get(format_type, '.txt')
    else:
        # Multiple files - use merged naming
        return generate_merged_output_name(input_files, format_type)


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
        
        # Validate input files
        validate_input_files(args.input_files)
        
        # Determine output file
        output_file = determine_output_file(args.input_files, args.output, args.format)
        
        # Check if output directory exists
        output_dir = os.path.dirname(output_file)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
        
        # Parse the log file(s)
        if len(args.input_files) == 1:
            # Single file parsing
            input_file = args.input_files[0]
            logger.info(f"Parsing {input_file} -> {output_file} (format: {args.format})")
            
            parse_log(
                bin_file=input_file,
                output_file=output_file,
                tz_code=args.timezone,
                verbose=args.verbose,
                logger=logger,
                output_format=args.format
            )
        else:
            # Multiple file parsing with merging
            logger.info(f"Parsing {len(args.input_files)} files -> {output_file} (format: {args.format})")
            
            parse_multiple_logs(
                bin_files=args.input_files,
                output_file=output_file,
                tz_code=args.timezone,
                verbose=args.verbose,
                logger=logger,
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
