# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python utility for parsing Zero Motorcycle log files. It decodes binary-encoded event logs from Zero Motorcycles' main bike board (MBB) and battery management system (BMS) into human-readable text format. The tool emulates Zero's official log parser functionality.

## Development Commands

### Setup
```bash
python3 setup.py develop
```

### Running the Parser
```bash
python3 zero_log_parser.py <logfile.bin> [-o output_file]
```

### Timezone-aware Parsing
```bash
# Use local system timezone (default)
python3 zero_log_parser.py logfile.bin

# Use specific timezone offset (e.g., PST = UTC-8)
python3 zero_log_parser.py logfile.bin --timezone -8

# Use specific timezone offset (e.g., CET = UTC+1) 
python3 zero_log_parser.py logfile.bin --timezone 1
```

### Output Formats
```bash
# Text format (default)
python3 zero_log_parser.py logfile.bin -f txt

# CSV format - comma-separated values for spreadsheet import
python3 zero_log_parser.py logfile.bin -f csv

# TSV format - tab-separated values for data analysis
python3 zero_log_parser.py logfile.bin -f tsv
```

### Testing
```bash
python3 test.py <directory_of_log_files>
```

### Docker Build and Run
```bash
# Build the Docker image
docker build . -t "zero-log-parser"

# Run with volume mounting (example)
cd ~/zero-logs
docker run --rm -v "$PWD:/root" zero-log-parser /root/VIN_BMS0_2019-04-20.bin -o /root/VIN_BMS0_2019-04-20.txt
```

## Architecture

### Core Components

- **zero_log_parser.py**: Main parser implementation with `BinaryTools` class for handling serialized data and log entry parsing
- **parse_logs.py**: Additional parsing utilities
- **zero_csv_plot.py**: CSV plotting functionality for log data visualization
- **test.py**: Test suite using unittest framework that validates parser output against reference log directories

### Log Structure

The codebase handles two types of log files:
- **MBB logs**: Main bike board logs with event and error sections
- **BMS logs**: Battery management system logs with discharge/charge data

Log entries follow a structured format with:
- Entry header (0xb2)
- Entry length
- Entry type (see log_structure.md for detailed mapping)
- Timestamp (Unix epoch)
- Variable-length entry data

### Key Implementation Details

- Uses little-endian byte order for raw log values
- Implements XOR decoding for certain byte sequences (0xFE handling)
- Supports ring buffer memory dumps of 0x40000 bytes (262144 bytes)
- **Log entry sorting**: Automatically sorts entries by timestamp (newest first) while preserving original entry numbers
- **Timezone support**: Uses local system timezone by default, supports custom timezone offsets via `--timezone` parameter
- **Localized date format**: Uses ISO 8601 format (YYYY-MM-DD HH:MM:SS) for better international compatibility
- **Multiple output formats**: Supports TXT (human-readable), CSV (spreadsheet), and TSV (data analysis) formats
- Handles multiple log file formats:
  - Legacy format: Static addresses for serial numbers, VIN, firmware/board revisions
  - Ring buffer format (REV3): Detects format by file size and starting byte (0xb2)
- Handles multiple log section types identified by header sequences (0xa0-0xa3)
- VIN extraction: Uses filename parsing for ring buffer format, static addresses for legacy
- Serial number detection: Dynamically locates based on section header positions
- Timestamp validation: Filters out invalid future dates (beyond 2030) and corrupted timestamps

### Dependencies

- Standard Python 3 libraries only (no external dependencies beyond `ddt` for testing)
- Uses `struct` module for binary data parsing
- `unittest` framework for testing

## File Structure Notes

- **log_structure.md**: Comprehensive documentation of log file formats, entry types, and data layouts
- **requirements.txt**: Contains development dependencies
- **Dockerfile**: For containerized execution
- Scripts: `make-TXT.sh`, `compare_new_and_old.sh`, `docker-entrypoint.sh`