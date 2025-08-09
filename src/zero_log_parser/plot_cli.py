"""Command line interface for Zero Log plotting functionality."""

import argparse
import sys
from typing import Optional

from . import __version__


def create_parser() -> argparse.ArgumentParser:
    """Create the command line argument parser for plotting."""
    parser = argparse.ArgumentParser(
        description="Generate interactive plots from Zero Motorcycle logs",
        epilog="For more information, visit: https://github.com/ilja-radusch/zero-log-parser"
    )
    
    parser.add_argument(
        'input_files',
        nargs='+',
        help="Input file(s) (.bin or .csv). Multiple files will be merged before plotting."
    )
    
    parser.add_argument(
        '--plot',
        choices=['all', 'battery', 'power', 'thermal', 'voltage', 
                'performance', 'charging', 'balance', 'range'],
        default='all',
        help="Type of plot to generate (default: all)"
    )
    
    parser.add_argument(
        '-o', '--output-dir',
        default='.',
        help="Output directory for HTML files (default: current directory)"
    )
    
    parser.add_argument(
        '--start',
        help="Filter data from this time (e.g., 'last month', 'June 2025', '2025-06-15')"
    )
    
    parser.add_argument(
        '--end',
        help="Filter data until this time (e.g., 'last week', 'July 2025', '2025-07-31')"
    )
    
    parser.add_argument('--timezone', help='Timezone offset in hours from UTC (e.g., -8 for PST, +1 for CET). Defaults to local system timezone.')

    parser.add_argument(
        '--version',
        action='version',
        version=f'zero-plotting {__version__}'
    )
    
    return parser


def main() -> int:
    """Main entry point for the plotting CLI."""
    try:
        # Parse command line arguments first (for --help)
        parser = create_parser()
        args = parser.parse_args()
        
        # Check if plotting dependencies are available
        try:
            from . import _get_plotter_class
            ZeroLogPlotter = _get_plotter_class()
        except ImportError:
            print("Error: plotly and pandas are required for plotting.", file=sys.stderr)
            print("Install with: pip install -e \".[plotting]\"", file=sys.stderr)
            return 1
        
        # Parse time filters
        start_time = None
        end_time = None
        
        tz_code = args.timezone

        if args.start or args.end:
            from .utils import parse_time_filter
            
            if args.start:
                try:
                    start_time = parse_time_filter(args.start)
                    print(f"Filtering data from: {start_time}")
                except ValueError as e:
                    print(f"Error parsing start time: {e}", file=sys.stderr)
                    return 1
            
            if args.end:
                try:
                    end_time = parse_time_filter(args.end)
                    print(f"Filtering data until: {end_time}")
                except ValueError as e:
                    print(f"Error parsing end time: {e}", file=sys.stderr)
                    return 1
        
        # Create plotter and generate plots
        if len(args.input_files) == 1:
            # Single file - use existing logic
            plotter = ZeroLogPlotter(args.input_files[0],
                                     start_time=start_time, end_time=end_time,
                                     tz_code=tz_code)
        else:
            # Multiple files - merge and then plot
            print(f"Merging {len(args.input_files)} log files...")
            plotter = ZeroLogPlotter.from_multiple_files(args.input_files,
                                                         start_time=start_time, end_time=end_time,
                                                         tz_code=tz_code)
        
        if args.plot == 'all':
            plotter.generate_all_plots(args.output_dir)
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
            import os
            
            # Generate base name from multiple files
            if len(args.input_files) == 1:
                base_name = os.path.splitext(os.path.basename(args.input_files[0]))[0]
            else:
                # Use meaningful name from plotter (VIN + date)
                base_name = os.path.splitext(os.path.basename(plotter.input_file))[0]
            
            output_file = os.path.join(args.output_dir, f"{base_name}_{args.plot}.html")
            fig.write_html(output_file)
            print(f"Generated: {output_file}")
            
        return 0
        
    except KeyboardInterrupt:
        print("\nOperation cancelled by user", file=sys.stderr)
        return 130
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
