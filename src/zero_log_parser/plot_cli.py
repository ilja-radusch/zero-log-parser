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
        'input_file',
        help="Input file (.bin or .csv)"
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
        '--version',
        action='version',
        version=f'zero-plotting {__version__}'
    )
    
    return parser


def main() -> int:
    """Main entry point for the plotting CLI."""
    try:
        # Check if plotting dependencies are available
        try:
            from . import _get_plotter_class
            ZeroLogPlotter = _get_plotter_class()
        except ImportError:
            print("Error: plotly and pandas are required for plotting.", file=sys.stderr)
            print("Install with: pip install -e \".[plotting]\"", file=sys.stderr)
            return 1
        
        # Parse command line arguments
        parser = create_parser()
        args = parser.parse_args()
        
        # Create plotter and generate plots
        plotter = ZeroLogPlotter(args.input_file)
        
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
            base_name = os.path.splitext(os.path.basename(args.input_file))[0]
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