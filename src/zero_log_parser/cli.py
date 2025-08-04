"""Command line interface for Zero Log Parser."""

import argparse
import logging
import os
import sys
from typing import Optional

from . import __version__
from .core import parse_log
from .utils import console_logger, default_parsed_output_for


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
    
    # Plotting arguments
    parser.add_argument(
        '--plot',
        choices=['all', 'battery', 'power', 'thermal', 'voltage', 
                'performance', 'charging', 'balance', 'range'],
        help="Generate interactive plots (requires plotly and pandas)"
    )
    
    parser.add_argument(
        '--plot-output-dir',
        default='.',
        help="Output directory for plot HTML files (default: current directory)"
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
        
        parse_log(
            log_file=args.input_file,
            output_file=output_file,
            utc_offset_hours=args.timezone,
            verbose=args.verbose,
            logger=logger,
            output_format=args.format
        )
        
        logger.info("Parsing completed successfully")
        
        # Generate plots if requested
        if args.plot:
            try:
                from . import _get_plotter_class
                ZeroLogPlotter = _get_plotter_class()
                
                # Use the generated output file or convert bin to CSV for plotting
                plot_input_file = args.input_file
                if args.format != 'csv':
                    # Generate CSV for plotting
                    csv_output = output_file.replace('.txt', '.csv').replace('.tsv', '.csv').replace('.json', '.csv')
                    if not csv_output.endswith('.csv'):
                        csv_output += '.csv'
                    
                    logger.info(f"Generating CSV for plotting: {csv_output}")
                    parse_log(
                        log_file=args.input_file,
                        output_file=csv_output,
                        utc_offset_hours=args.timezone,
                        verbose=args.verbose,
                        logger=logger,
                        output_format='csv'
                    )
                    plot_input_file = csv_output
                else:
                    plot_input_file = output_file
                
                logger.info(f"Generating plots from: {plot_input_file}")
                plotter = ZeroLogPlotter(plot_input_file)
                
                if args.plot == 'all':
                    plotter.generate_all_plots(args.plot_output_dir)
                    logger.info("All plots generated successfully")
                else:
                    # Generate specific plot
                    plot_methods = {
                        'battery': plotter.plot_battery_performance,
                        'power': plotter.plot_power_consumption,
                        'thermal': plotter.plot_thermal_management,
                        'voltage': plotter.plot_voltage_analysis,
                        'performance': plotter.plot_performance_efficiency,
                        'charging': plotter.plot_charging_analysis,
                        'balance': plotter.plot_cell_balance,
                        'range': plotter.plot_range_analysis,
                    }
                    
                    fig = plot_methods[args.plot]()
                    base_name = os.path.splitext(os.path.basename(args.input_file))[0]
                    plot_output_file = os.path.join(args.plot_output_dir, f"{base_name}_{args.plot}.html")
                    fig.write_html(plot_output_file)
                    logger.info(f"Generated plot: {plot_output_file}")
                    
            except ImportError as e:
                logger.error(f"Plotting dependencies not available: {e}")
                logger.error("Install with: pip install -e \".[plotting]\"")
                return 1
            except Exception as e:
                logger.error(f"Error generating plots: {e}")
                return 1
        
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