"""Tests for the core parsing functionality."""

import pytest
import tempfile
import os
from zero_log_parser import LogData, parse_log


def test_logdata_basic():
    """Test basic LogData functionality."""
    # Create a minimal test log data
    test_data = bytearray([0xb2, 0x07, 0x00, 0x01, 0x02, 0x03, 0x04])
    
    log_data = LogData(test_data)
    assert log_data.log_version == 3
    assert isinstance(log_data.header_info, dict)
    assert isinstance(log_data.entries, list)


def test_output_formats():
    """Test different output formats."""
    test_data = bytearray([0xb2, 0x07, 0x00, 0x01, 0x02, 0x03, 0x04])
    log_data = LogData(test_data)
    
    # Test text output
    text_output = log_data.emit_text_decoding()
    assert isinstance(text_output, str)
    assert "VIN:" in text_output
    
    # Test CSV output  
    csv_output = log_data.emit_csv_decoding()
    assert isinstance(csv_output, str)
    assert "Entry,Timestamp,LogLevel,Event,Conditions" in csv_output
    
    # Test TSV output
    tsv_output = log_data.emit_tsv_decoding()
    assert isinstance(tsv_output, str)
    assert "Entry\tTimestamp\tLogLevel\tEvent\tConditions" in tsv_output
    
    # Test JSON output
    json_output = log_data.emit_json_decoding()
    assert isinstance(json_output, str)
    assert "metadata" in json_output


def test_parse_log_function():
    """Test the parse_log function."""
    # Create a temporary test file
    test_data = bytearray([0xb2, 0x07, 0x00, 0x01, 0x02, 0x03, 0x04])
    
    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as temp_input:
        temp_input.write(test_data)
        temp_input_path = temp_input.name
    
    with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as temp_output:
        temp_output_path = temp_output.name
    
    try:
        # Test parsing
        parse_log(temp_input_path, temp_output_path, output_format='txt')
        
        # Check that output file was created
        assert os.path.exists(temp_output_path)
        
        # Check output content
        with open(temp_output_path, 'r') as f:
            content = f.read()
            assert "VIN:" in content
            
    finally:
        # Clean up
        os.unlink(temp_input_path)
        os.unlink(temp_output_path)


if __name__ == '__main__':
    pytest.main([__file__])