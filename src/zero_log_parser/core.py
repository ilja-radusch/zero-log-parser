"""Core parsing logic for Zero log files."""

import json
import logging
import os
from collections import OrderedDict
from datetime import datetime
from typing import List, Dict, Any, Optional, Union

from .parser import BinaryTools, Gen2, Gen3, LogFile
from .utils import get_local_timezone_offset, logger_for_input


class LogData:
    """
    Main class for parsing Zero motorcycle log data.
    
    :type log_version: int
    :type header_info: Dict[str, str]
    :type entries_count: Optional[int]
    :type entries: List[str]
    :type timezone_offset: float
    """

    def __init__(self, log_data: Union[bytes, bytearray, str], timezone_offset: Optional[float] = None):
        """
        Initialize LogData with raw log data.
        
        Args:
            log_data: Raw log data as bytes, bytearray, or file path string
            timezone_offset: Timezone offset in hours from UTC (default: system timezone)
        """
        if isinstance(log_data, str):
            # It's a file path
            with open(log_data, 'rb') as f:
                self.raw_data = bytearray(f.read())
            self.filename = os.path.basename(log_data)
        else:
            # It's raw data
            self.raw_data = bytearray(log_data)
            self.filename = "unknown"
            
        # Set timezone offset
        if timezone_offset is None:
            self.timezone_offset = get_local_timezone_offset() / 3600.0  # Convert to hours
        else:
            self.timezone_offset = timezone_offset
            
        # Parse the log
        self.log_version = self._detect_log_version()
        self.header_info = self._extract_header_info()
        self.entries_count, self.entries = self._parse_entries()

    def _detect_log_version(self) -> int:
        """Detect the log format version."""
        if len(self.raw_data) == 0:
            return 0
            
        # Check for different format indicators
        if self.raw_data[0] == 0xb2:
            return 3  # Gen3/Ring buffer format
        elif len(self.raw_data) == 0x40000:
            return 3  # Fixed size ring buffer
        else:
            return 2  # Gen2 format

    def _extract_header_info(self) -> Dict[str, str]:
        """Extract header information from the log."""
        header_info = OrderedDict()
        
        if self.log_version == 3:
            # Ring buffer format - try to extract VIN from filename
            if self.filename and self.filename.startswith('538'):
                vin_part = self.filename.split('_')[0]
                if len(vin_part) == 17:
                    header_info['VIN'] = vin_part
                else:
                    header_info['VIN'] = 'Unknown'
            else:
                header_info['VIN'] = 'Unknown'
                
            header_info['Serial number'] = 'Unknown'
            header_info['Initial date'] = 'Unknown'
            header_info['Model'] = 'Unknown'
            header_info['Firmware rev'] = 'Unknown'
            header_info['Board rev'] = 'Unknown'
        else:
            # Gen2 format - extract from fixed locations
            try:
                header_info['VIN'] = BinaryTools.unpack_str(self.raw_data, 0x240, 17).strip('\x00')
                header_info['Serial number'] = BinaryTools.unpack_str(self.raw_data, 0x200, 21).strip('\x00')
            except:
                header_info['VIN'] = 'Unknown'
                header_info['Serial number'] = 'Unknown'
                
        return header_info

    def _parse_entries(self) -> tuple[int, List[Dict[str, Any]]]:
        """Parse log entries."""
        entries = []
        entry_count = 0
        
        if self.log_version == 3:
            # Use Gen3 parser
            entries, entry_count = self._parse_gen3_entries()
        else:
            # Use Gen2 parser
            entries, entry_count = self._parse_gen2_entries()
            
        # Add entry numbers and sort by timestamp
        for i, entry in enumerate(entries):
            entry['entry_number'] = entry_count - i
            
        # Sort entries by timestamp (newest first) while preserving entry numbers
        entries.sort(key=lambda x: x.get('sort_timestamp', 0), reverse=True)
        
        return entry_count, entries

    def _parse_gen2_entries(self) -> tuple[List[Dict[str, Any]], int]:
        """Parse Gen2 format entries."""
        entries = []
        offset = 0x10  # Skip header
        entry_count = 0
        
        while offset < len(self.raw_data) - 7:
            if self.raw_data[offset] != 0xb2:
                offset += 1
                continue
                
            try:
                # Decode entry
                length, entry_data, unhandled_count = Gen2.decode_entry_segment(
                    self.raw_data[offset:], self.timezone_offset
                )
                
                if length <= 0:
                    offset += 1
                    continue
                    
                # Convert to standard format
                entry = {
                    'event': entry_data.get('event', ''),
                    'time': entry_data.get('time', ''),
                    'conditions': entry_data.get('conditions', ''),
                    'log_level': entry_data.get('log_level', 'INFO'),
                    'sort_timestamp': self._parse_timestamp_for_sorting(entry_data.get('time', ''))
                }
                
                entries.append(entry)
                entry_count += 1
                offset += length
                
            except Exception as e:
                offset += 1
                continue
                
        return entries, entry_count

    def _parse_gen3_entries(self) -> tuple[List[Dict[str, Any]], int]:
        """Parse Gen3/Ring buffer format entries."""
        entries = []
        offset = 0
        entry_count = 0
        
        while offset < len(self.raw_data) - 7:
            if self.raw_data[offset] != 0xb2:
                offset += 1
                continue
                
            try:
                # Basic entry parsing for Gen3
                length = self.raw_data[offset + 1]
                if length < 7 or offset + length > len(self.raw_data):
                    offset += 1
                    continue
                    
                # Extract timestamp
                timestamp_bytes = self.raw_data[offset + 3:offset + 7]
                timestamp_int = int.from_bytes(timestamp_bytes, byteorder='little')
                
                # Skip invalid timestamps
                if timestamp_int <= 0xfff or timestamp_int > 1893456000:
                    offset += length
                    continue
                    
                # Apply timezone offset
                adjusted_timestamp = timestamp_int + (self.timezone_offset * 3600)
                timestamp_str = datetime.fromtimestamp(adjusted_timestamp).strftime('%Y-%m-%d %H:%M:%S')
                
                entry = {
                    'event': 'Board Status',  # Default for now
                    'time': timestamp_str,
                    'conditions': 'No additional data',
                    'log_level': 'INFO',
                    'sort_timestamp': adjusted_timestamp
                }
                
                entries.append(entry)
                entry_count += 1
                offset += length
                
            except Exception:
                offset += 1
                continue
                
        return entries, entry_count

    def _parse_timestamp_for_sorting(self, timestamp_str: str) -> float:
        """Parse timestamp string for sorting purposes."""
        try:
            if timestamp_str.isdigit():
                return float(timestamp_str)
            dt = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
            return dt.timestamp()
        except:
            return 0.0

    def interpolate_missing_timestamps(self):
        """Interpolate missing timestamps using neighboring entries."""
        # Implementation for timestamp interpolation
        for i, entry in enumerate(self.entries):
            if entry.get('time', '').isdigit():
                # This is a missing timestamp, try to interpolate
                prev_entry = self.entries[i-1] if i > 0 else None
                next_entry = self.entries[i+1] if i < len(self.entries) - 1 else None
                
                # Simple interpolation logic
                if prev_entry and next_entry:
                    prev_ts = prev_entry.get('sort_timestamp', 0)
                    next_ts = next_entry.get('sort_timestamp', 0)
                    if prev_ts > 0 and next_ts > 0:
                        interpolated_ts = (prev_ts + next_ts) / 2
                        entry['time'] = datetime.fromtimestamp(interpolated_ts).strftime('%Y-%m-%d %H:%M:%S')
                        entry['sort_timestamp'] = interpolated_ts

    def emit_text_decoding(self) -> str:
        """Generate text output format."""
        output_lines = []
        
        # Header information
        for key, value in self.header_info.items():
            output_lines.append(f"{key}: {value}")
        output_lines.append("")
        
        # Entries
        for entry in self.entries:
            line = f"{entry.get('entry_number', 0):>5d}     {entry.get('time', '')}  {entry.get('log_level', 'INFO'):<8s}   {entry.get('event', '')}"
            if entry.get('conditions'):
                line += f"            {entry['conditions']}"
            output_lines.append(line)
            
        return '\n'.join(output_lines)

    def emit_csv_decoding(self) -> str:
        """Generate CSV output format."""
        output_lines = ['Entry,Timestamp,LogLevel,Event,Conditions']
        
        for entry in self.entries:
            conditions = entry.get('conditions', '').replace('"', '""')
            line = f"{entry.get('entry_number', 0)},{entry.get('time', '')},{entry.get('log_level', 'INFO')},{entry.get('event', '')},\"{conditions}\""
            output_lines.append(line)
            
        return '\n'.join(output_lines)

    def emit_tsv_decoding(self) -> str:
        """Generate TSV output format."""
        output_lines = ['Entry\tTimestamp\tLogLevel\tEvent\tConditions']
        
        for entry in self.entries:
            conditions = entry.get('conditions', '').replace('\t', ' ')
            line = f"{entry.get('entry_number', 0)}\t{entry.get('time', '')}\t{entry.get('log_level', 'INFO')}\t{entry.get('event', '')}\t{conditions}"
            output_lines.append(line)
            
        return '\n'.join(output_lines)

    def emit_json_decoding(self) -> str:
        """Generate JSON output format."""
        # Parse structured data for JSON entries
        json_entries = []
        for entry in self.entries:
            json_entry = {
                'entry_number': entry.get('entry_number', 0),
                'timestamp': entry.get('time', ''),
                'sort_timestamp': entry.get('sort_timestamp', 0),
                'log_level': entry.get('log_level', 'INFO'),
                'event': entry.get('event', ''),
                'conditions': entry.get('conditions') if entry.get('conditions') not in [None, ''] else None,
                'is_structured_data': False
            }
            
            # Check if conditions contain JSON data
            conditions = entry.get('conditions', '')
            if conditions and conditions.startswith('{') and conditions.endswith('}'):
                try:
                    structured_data = json.loads(conditions)
                    json_entry['is_structured_data'] = True
                    json_entry['structured_data'] = structured_data
                    json_entry['conditions'] = None
                except json.JSONDecodeError:
                    pass
                    
            json_entries.append(json_entry)
        
        output_data = {
            'metadata': {
                'source_file': self.filename,
                'log_type': 'MBB' if 'Mbb' in self.filename else 'BMS' if 'Bms' in self.filename else 'Unknown',
                'parser_version': 'zero-log-parser',
                'generated_at': datetime.now().isoformat(),
                'timezone': f"UTC{'+' if self.timezone_offset >= 0 else ''}{self.timezone_offset}",
                'total_entries': self.entries_count or len(self.entries)
            },
            'log_info': self.header_info,
            'entries': json_entries
        }
        
        return json.dumps(output_data, indent=2, ensure_ascii=False)


def parse_log(log_file: str, output_file: str, utc_offset_hours: Optional[float] = None, 
              verbose: bool = False, logger: Optional[logging.Logger] = None, 
              output_format: str = 'txt') -> None:
    """
    Parse a Zero motorcycle log file and generate output.
    
    Args:
        log_file: Path to the input log file
        output_file: Path to the output file
        utc_offset_hours: UTC offset in hours (default: system timezone)
        verbose: Enable verbose logging
        logger: Logger instance (optional)
        output_format: Output format ('txt', 'csv', 'tsv', 'json')
    """
    if logger is None:
        logger = logging.getLogger(__name__)
        
    logger.info(f"Parsing {log_file}")
    
    try:
        # Parse the log
        log_data = LogData(log_file, timezone_offset=utc_offset_hours)
        
        # Interpolate missing timestamps
        log_data.interpolate_missing_timestamps()
        
        # Generate output based on format
        if output_format == 'csv':
            output_text = log_data.emit_csv_decoding()
        elif output_format == 'tsv':
            output_text = log_data.emit_tsv_decoding()
        elif output_format == 'json':
            output_text = log_data.emit_json_decoding()
        else:  # Default to txt
            output_text = log_data.emit_text_decoding()
            
        # Write output
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(output_text)
            
        logger.info(f"Output written to {output_file}")
        
    except Exception as e:
        logger.error(f"Error parsing {log_file}: {e}")
        raise