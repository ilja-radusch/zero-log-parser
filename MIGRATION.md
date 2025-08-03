# Migration Guide: Zero Log Parser v2.0

This guide helps you migrate from the old monolithic `zero_log_parser.py` script to the new modern Python package structure.

## What Changed

### Package Structure
- **Old**: Single `zero_log_parser.py` file
- **New**: Proper Python package with modular structure in `src/zero_log_parser/`

### Installation
- **Old**: Direct script execution or `python setup.py develop`
- **New**: Standard pip installation with `pip install -e .`

### Python Requirements  
- **Old**: Python 3.x (any version)
- **New**: Python 3.10+ (modern type hints and features)

### CLI Commands
- **Old**: `python zero_log_parser.py <file>`
- **New**: `zero-log-parser <file>` or `zlp <file>` (after installation)

## Migration Steps

### 1. Update Python Version
Ensure you have Python 3.10 or higher:
```bash
python3 --version  # Should be 3.10+
```

### 2. Install New Package
```bash
# Remove old dependencies if any
pip uninstall zero-log-parser || true

# Install new package
pip install -e .

# Or for development
pip install -e ".[dev]"
```

### 3. Update Command Usage

#### Old Way
```bash
python3 zero_log_parser.py logfile.bin -o output.txt
python3 zero_log_parser.py logfile.bin -f csv
```

#### New Way
```bash
zero-log-parser logfile.bin -o output.txt
zero-log-parser logfile.bin -f csv

# Or use the short alias
zlp logfile.bin -f json
```

### 4. Update Import Statements (Python API)

#### Old Way
```python
# Direct import from script (not recommended)
import zero_log_parser
log_data = zero_log_parser.LogData(log_file)
```

#### New Way
```python
# Proper package imports
from zero_log_parser import LogData, parse_log
from zero_log_parser.core import LogData
from zero_log_parser.message_parser import improve_message_parsing

# Usage
log_data = LogData("logfile.bin")
parse_log("input.bin", "output.json", output_format="json")
```

### 5. Update Scripts and Automation

#### Old Shell Scripts
```bash
#!/bin/bash
python3 zero_log_parser.py "$1" -o "${1%.*}.txt"
```

#### New Shell Scripts
```bash
#!/bin/bash
zero-log-parser "$1" -o "${1%.*}.txt"
```

#### Docker Updates
Update your Dockerfiles to use the new package structure.

## New Features Available

### Enhanced Output Formats
```bash
# JSON with structured data
zero-log-parser logfile.bin -f json

# CSV/TSV for data analysis
zero-log-parser logfile.bin -f csv
zero-log-parser logfile.bin -f tsv
```

### Improved Structured Data
The new parser automatically converts many message types to structured JSON:
- Firmware version information
- Battery pack configuration
- Discharge level telemetry
- SOC data with voltage readings
- Sensor data (tipover detection)
- Error conditions

### Better CLI Experience
```bash
# Help system
zero-log-parser --help

# Version information
zero-log-parser --version

# Verbose output for debugging
zero-log-parser logfile.bin --verbose
```

## Backward Compatibility

### Old Script Still Works
The original `zero_log_parser.py` file still exists and works as before:
```bash
python3 zero_log_parser.py logfile.bin  # Still works
```

### No More setup.py
The old `setup.py` file has been completely removed. The project now uses only `pyproject.toml` for configuration, following modern Python packaging standards (PEP 621).

## Troubleshooting

### Import Errors
If you get import errors, ensure the package is properly installed:
```bash
pip install -e .
```

### Command Not Found
If `zero-log-parser` command is not found:
```bash
# Check if installed correctly
pip list | grep zero-log-parser

# Use Python module syntax as fallback
python3 -m zero_log_parser.cli logfile.bin
```

### Python Version Issues
If you get syntax errors, check your Python version:
```bash
python3 --version  # Must be 3.10+
```

## Development Changes

### Code Quality Tools
New development setup includes modern Python tools:
```bash
# Install development dependencies
pip install -e ".[dev]"

# Code formatting
black src/ tests/

# Linting
ruff check src/ tests/

# Type checking
mypy src/

# Testing
pytest
```

### Project Structure
```
zero-log-parser/
├── src/zero_log_parser/          # Main package (NEW)
├── tests/                        # Test suite (NEW)
├── pyproject.toml               # Modern packaging (NEW)
└── zero_log_parser.py           # Original script (LEGACY)
```

## Getting Help

- **Documentation**: See updated [README.md](README.md)
- **Issues**: [GitHub Issues](https://github.com/ilja-radusch/zero-log-parser/issues)
- **Examples**: Check the `tests/` directory for usage examples

## Benefits of Migration

1. **Better Performance**: Modular code structure
2. **Enhanced Features**: Structured data extraction, multiple output formats
3. **Modern Python**: Type hints, better error handling
4. **Easy Installation**: Standard pip installation
5. **Development Tools**: Code quality tools, testing framework
6. **Future-Proof**: Built for Python 3.10+ with modern best practices