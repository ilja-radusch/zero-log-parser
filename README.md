# Zero Log Parser

A modern Python package for parsing Zero Motorcycle log files with structured data extraction and multiple output formats.

This tool parses binary-encoded event logs from Zero Motorcycles' main bike board (MBB) and battery management system (BMS) into human-readable formats, emulating Zero's official log parser functionality with enhanced structured data extraction.

## Features

- **Multiple Output Formats**: Text, CSV, TSV, and JSON
- **Structured Data Extraction**: Automatically converts telemetry data to structured JSON format
- **Timezone Support**: Configurable timezone handling with system default
- **Modern Python Package**: Built for Python 3.10+ with type hints and modern packaging
- **CLI and Library**: Use as command-line tool or import as Python library
- **Enhanced Parsing**: Improved message parsing with descriptive event names and structured sensor data

## Installation

### From PyPI (recommended)

```bash
pip install zero-log-parser
```

### From Source

```bash
git clone https://github.com/ilja-radusch/zero-log-parser.git
cd zero-log-parser
pip install -e .
```

### For Development

```bash
git clone https://github.com/ilja-radusch/zero-log-parser.git
cd zero-log-parser
pip install -e ".[dev]"
```

## Requirements

- Python 3.10 or higher
- No external dependencies (uses only Python standard library)

## Usage

### Getting Logs

You can extract logs from your Zero motorcycle using the [Zero mobile app](http://www.zeromotorcycles.com/app/help/ios/):

1. Download the Zero mobile app
2. Pair your motorcycle with it via Bluetooth
3. Select `Support` > `Email bike logs`
4. Enter your email address to send the logs to yourself
5. Download the attachment from the email

### Command Line Interface

The package provides two CLI commands: `zero-log-parser` and `zlp` (short alias).

#### Basic Usage

```bash
# Parse to text format (default)
zero-log-parser logfile.bin

# Specify output file
zero-log-parser logfile.bin -o output.txt

# Different output formats
zero-log-parser logfile.bin -f csv -o output.csv
zero-log-parser logfile.bin -f tsv -o output.tsv
zero-log-parser logfile.bin -f json -o output.json
```

#### Advanced Options

```bash
# Custom timezone (UTC offset in hours)
zero-log-parser logfile.bin --timezone -8  # PST
zero-log-parser logfile.bin --timezone 1   # CET

# Verbose output
zero-log-parser logfile.bin --verbose

# Short alias
zlp logfile.bin -f json -o structured_data.json
```

#### Help

```bash
zero-log-parser --help
```

### Python Library

```python
from zero_log_parser import LogData, parse_log

# Parse a log file
log_data = LogData("path/to/logfile.bin")

# Access parsed data
print(f"Entries: {log_data.entries_count}")
print(f"Header: {log_data.header_info}")

# Generate different output formats
text_output = log_data.emit_text_decoding()
json_output = log_data.emit_json_decoding()

# Or use the high-level function
parse_log(
    log_file="input.bin",
    output_file="output.json",
    output_format="json",
    timezone_offset=-8  # PST
)
```

### Output Formats

#### Text Format (default)
Human-readable format similar to Zero's official parser:
```
Entry     Timestamp            Level     Event                    Conditions
6490      2025-08-03 12:34:32  DATA      Firmware Version         {"revision": 48, ...}
```

#### CSV Format
Comma-separated values for spreadsheet import:
```csv
Entry,Timestamp,LogLevel,Event,Conditions
6490,2025-08-03 12:34:32,DATA,Firmware Version,"{""revision"": 48, ...}"
```

#### TSV Format  
Tab-separated values for data analysis:
```tsv
Entry	Timestamp	LogLevel	Event	Conditions
6490	2025-08-03 12:34:32	DATA	Firmware Version	{"revision": 48, ...}
```

#### JSON Format
Structured JSON with metadata and parsed telemetry:
```json
{
  "metadata": {
    "source_file": "logfile.bin",
    "log_type": "MBB",
    "total_entries": 6603
  },
  "entries": [
    {
      "entry_number": 6490,
      "timestamp": "2025-08-03 12:34:32",
      "log_level": "DATA",
      "event": "Firmware Version",
      "is_structured_data": true,
      "structured_data": {
        "revision": 48,
        "build_date": "2024-11-17",
        "build_time": "14:19:50"
      }
    }
  ]
}
```

## Structured Data Features

The parser automatically detects and converts various message types to structured JSON:

- **Firmware Version**: Build info, revision, timestamps
- **Battery Pack Configuration**: Pack type, brick count, specifications  
- **Discharge Level**: SOC, current, voltage, temperature data
- **SOC Data**: State of charge with voltage and current readings
- **Voltage Readings**: Contactor and cell voltage measurements
- **Charging/Riding Status**: Comprehensive telemetry during operation
- **Tipover Detection**: Sensor data with roll/pitch measurements
- **Error Conditions**: Structured fault and diagnostic information

## Development

### Setup Development Environment

```bash
git clone https://github.com/ilja-radusch/zero-log-parser.git
cd zero-log-parser
pip install -e ".[dev]"
```

### Code Quality Tools

```bash
# Format code
black src/ tests/

# Lint code  
ruff check src/ tests/

# Type checking
mypy src/

# Run tests
pytest
```

### Project Structure

```
zero-log-parser/
├── src/zero_log_parser/          # Main package
│   ├── __init__.py               # Package exports
│   ├── cli.py                    # Command line interface
│   ├── core.py                   # Main parsing logic
│   ├── parser.py                 # Binary parsing classes
│   ├── message_parser.py         # Message improvement logic
│   ├── utils.py                  # Utility functions
│   └── py.typed                  # Type checking marker
├── tests/                        # Test suite
├── pyproject.toml               # Modern Python packaging (PEP 621)
├── README.md                    # This file
└── requirements.txt             # Development dependencies
```

## Log File Formats

The parser supports multiple Zero motorcycle log formats:

- **MBB Logs**: Main bike board event logs
- **BMS Logs**: Battery management system logs  
- **Legacy Format**: Static addresses for older firmware
- **Ring Buffer Format**: Dynamic format for 2024+ firmware

For detailed format documentation, see [LOG STRUCTURE](LOG_STRUCTURE.md).

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests for new functionality
5. Run the test suite and code quality tools
6. Submit a pull request

## Authors

- **Ilja Radusch** - *Current Maintainer* - [@ilja-radusch](https://github.com/ilja-radusch/)
- **Kim Burgess** - *Original Author* - [@KimBurgess](https://github.com/KimBurgess/)
- **Brian T. Rice** - *Previous Maintainer* - [@BrianTRice](https://github.com/BrianTRice/)
- **Keith Thomas** - *Contributor* - [@keithxemi](https://github.com/keithxemi)

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

Originally developed at https://github.com/KimBurgess/zero-log-parser, this is a modernized fork with enhanced structured data extraction and modern Python packaging.

## Support

- Report issues: [GitHub Issues](https://github.com/ilja-radusch/zero-log-parser/issues)
- Documentation: [GitHub Repository](https://github.com/ilja-radusch/zero-log-parser)
- Community: Zero Motorcycle community forums
