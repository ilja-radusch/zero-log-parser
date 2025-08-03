"""Tests for the CLI functionality."""

import pytest
import tempfile
import os
from unittest.mock import patch
from zero_log_parser.cli import main, create_parser, validate_input_file


def test_create_parser():
    """Test CLI parser creation."""
    parser = create_parser()
    assert parser is not None
    
    # Test help doesn't crash
    help_text = parser.format_help()
    assert "Zero Motorcycle log files" in help_text


def test_validate_input_file():
    """Test input file validation."""
    # Test with non-existent file
    with pytest.raises(FileNotFoundError):
        validate_input_file("nonexistent.bin")
    
    # Test with valid file
    with tempfile.NamedTemporaryFile() as temp_file:
        validate_input_file(temp_file.name)  # Should not raise


def test_cli_help():
    """Test CLI help output."""
    with patch('sys.argv', ['zero-log-parser', '--help']):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0


def test_cli_version():
    """Test CLI version output."""
    with patch('sys.argv', ['zero-log-parser', '--version']):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0


def test_cli_basic_usage():
    """Test basic CLI usage."""
    # Create a temporary test file
    test_data = bytearray([0xb2, 0x07, 0x00, 0x01, 0x02, 0x03, 0x04])
    
    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as temp_input:
        temp_input.write(test_data)
        temp_input_path = temp_input.name
    
    with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as temp_output:
        temp_output_path = temp_output.name
    
    try:
        # Test CLI parsing
        with patch('sys.argv', ['zero-log-parser', temp_input_path, '-o', temp_output_path]):
            result = main()
            assert result == 0
        
        # Check that output file was created
        assert os.path.exists(temp_output_path)
        
    finally:
        # Clean up
        os.unlink(temp_input_path)
        if os.path.exists(temp_output_path):
            os.unlink(temp_output_path)


if __name__ == '__main__':
    pytest.main([__file__])