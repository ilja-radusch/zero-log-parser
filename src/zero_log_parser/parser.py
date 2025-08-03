"""Binary parsing classes for Zero log files."""

import codecs
import json
import logging
import struct
from collections import namedtuple
from datetime import datetime, timedelta
from math import trunc
from time import gmtime, strftime
from typing import Union, List, Dict, Any

from .message_parser import improve_message_parsing, determine_log_level
from .utils import ZERO_TIME_FORMAT, display_bytes_hex, hex_of_value


class BinaryTools:
    """
    Utility class for dealing with serialised data from the Zero's
    """

    TYPES = {
        'int8': 'b',
        'uint8': 'B',
        'int16': 'h',
        'uint16': 'H',
        'int32': 'l',
        'uint32': 'L',
        'int64': 'q',
        'uint64': 'Q',
        'float': 'f',
        'double': 'd',
        'char': 's',
        'bool': '?'
    }

    TYPE_CONVERSIONS = {
        'int8': int,
        'uint8': int,
        'int16': int,
        'uint16': int,
        'int32': int,
        'uint32': int,
        'int64': int,
        'uint64': int,
        'float': float,
        'double': float,
        'char': None,  # chr
        'bool': bool
    }

    @classmethod
    def unpack(cls,
               type_name: str,
               buff: bytearray,
               address: int,
               count=1, offset=0) -> Union[bytearray, int, float, bool]:
        # noinspection PyAugmentAssignment
        buff = buff + bytearray(32)
        type_key = type_name.lower()
        type_char = cls.TYPES[type_key]
        type_convert = cls.TYPE_CONVERSIONS[type_key]
        type_format = '<{}{}'.format(count, type_char)
        unpacked = struct.unpack_from(type_format, buff, address + offset)[0]
        if type_convert:
            # if count > 1:
            #     return [type_convert(each) for each in unpacked]
            # else:
            return type_convert(unpacked)
        else:
            return unpacked

    @staticmethod
    def unescape_block(data):
        start_offset = 0

        escape_offset = data.find(b'\xfe')

        while escape_offset != -1:
            escape_offset += start_offset
            if escape_offset + 1 < len(data):
                data[escape_offset] = data[escape_offset] ^ data[escape_offset + 1] - 1
                data = data[0:escape_offset + 1] + data[escape_offset + 2:]
            start_offset = escape_offset + 1
            escape_offset = data[start_offset:].find(b'\xfe')

        return data

    @staticmethod
    def decode_str(log_text_segment: bytearray, encoding='utf-8') -> str:
        """Decodes UTF-8 strings from a test segment, ignoring any errors"""
        return log_text_segment.decode(encoding=encoding, errors='ignore')

    @classmethod
    def unpack_str(cls, log_text_segment: bytearray, address, count=1, offset=0,
                   encoding='utf-8') -> str:
        """Unpacks and decodes UTF-8 strings from a test segment, ignoring any errors"""
        unpacked = cls.unpack('char', log_text_segment, address, count, offset)
        return cls.decode_str(unpacked.partition(b'\0')[0], encoding=encoding)

    @staticmethod
    def is_printable(bytes_or_str: str) -> bool:
        import string
        return all(c in string.printable for c in bytes_or_str)


class LogFile:
    """Represents a log file and its metadata."""
    
    def __init__(self, filename: str):
        self.filename = filename
        self._contents = None
    
    @property
    def contents(self) -> bytearray:
        """Get the file contents as a bytearray."""
        if self._contents is None:
            with open(self.filename, 'rb') as f:
                self._contents = bytearray(f.read())
        return self._contents


class Gen2:
    """Parser for Generation 2 log format."""
    
    entry_data_fencepost = b'\xb2\x00'
    Entry = namedtuple('Gen2EntryType', ['event', 'time', 'conditions', 'uninterpreted', 'log_level'])

    @classmethod
    def get_message_type_description(cls, message_type: int) -> str:
        """Get a descriptive name for a message type code."""
        descriptions = {
            0x00: "Board Status",
            0x01: "Key State",
            0x02: "High Throttle Disable",
            0x03: "Discharge level",
            0x04: "Charge Full",
            0x05: "Unknown Message Type 0x05",
            0x06: "Discharge Low",
            0x07: "Unknown Message Type 0x07",
            0x08: "System Status",
            0x09: "Key State",
            0x0A: "Unknown Message Type 0x0A",
            0x0B: "SOC Adjusted",
            0x0C: "Unknown Message Type 0x0C",
            0x0D: "Current Sensor Zeroed",
            0x0E: "Unknown Message Type 0x0E",
            0x0F: "Unknown Message Type 0x0F",
            0x10: "Hibernate State",
            0x11: "Chassis Isolation Fault",
            0x12: "BMS Reflash",
            0x13: "CAN Node ID Changed",
            0x14: "Unknown Message Type 0x14",
            0x15: "Contactor Status",
            0x16: "Discharge Cutback",
            0x17: "Unknown Message Type 0x17",
            0x18: "Contactor Drive",
            0x19: "Unknown Message Type 0x19",
            0x1A: "Unknown Message Type 0x1A",
            0x1B: "Unknown Message Type 0x1B",
            0x1C: "Unknown Message Type 0x1C",
            0x1D: "Unknown Message Type 0x1D",
            0x1E: "Unknown Message Type 0x1E",
            0x1F: "Unknown Message Type 0x1F",
        }
        return descriptions.get(message_type, f"Unknown Message Type 0x{message_type:02X}")

    @classmethod
    def unhandled_entry_format(cls, message_type, x):
        description = cls.get_message_type_description(message_type)
        
        # For completely unknown/unhandled types, show raw data
        if not x or len(x) == 0:
            return {
                'event': description,
                'conditions': 'No additional data'
            }
        
        # Try to provide meaningful interpretation for common patterns
        conditions = f'Raw data: {display_bytes_hex(x)}'
        
        # Check for specific patterns that might be meaningful
        if len(x) == 1:
            byte_val = BinaryTools.unpack('uint8', x, 0x00)
            conditions += f' (decimal: {byte_val})'
        elif len(x) == 2:
            word_val = BinaryTools.unpack('uint16', x, 0x00)
            conditions += f' (decimal: {word_val}, 0x{word_val:04X})'
            # Check for specific known values
            if word_val == 0x550A:
                conditions += ' [Possible reference: 0x550A]'
            elif word_val == 0x553A:
                conditions += ' [Possible reference: 0x553A]'
        elif len(x) == 4:
            dword_val = BinaryTools.unpack('uint32', x, 0x00)
            conditions += f' (decimal: {dword_val}, 0x{dword_val:08X})'
        
        # Try to detect if it might be ASCII text
        try:
            text_data = x.decode('ascii', errors='ignore').strip('\x00\r\n\t ')
            if text_data and len(text_data) > 2 and BinaryTools.is_printable(text_data):
                conditions += f' (possible text: "{text_data}")'
        except:
            pass
        
        return {
            'event': description,
            'conditions': conditions
        }

    @classmethod
    def timestamp_from_event(cls, data_block: bytearray, timezone_offset: float = 0) -> str:
        """Extract timestamp from event data."""
        timestamp_int = BinaryTools.unpack('uint32', data_block, 0x03)
        
        if timestamp_int <= 0xfff:
            return str(timestamp_int)
            
        # Future dates beyond 2030 are likely corrupted
        if timestamp_int > 1893456000:  # 2030-01-01
            return f"Invalid timestamp: {timestamp_int}"
            
        # Apply timezone offset
        adjusted_timestamp = timestamp_int + (timezone_offset * 3600)
        try:
            return strftime(ZERO_TIME_FORMAT, gmtime(adjusted_timestamp))
        except (ValueError, OSError):
            return f"Invalid timestamp: {timestamp_int}"

    @classmethod
    def discharge_level_format(cls, x: bytearray) -> Dict[str, Any]:
        """Parse discharge level entry format."""
        return {
            'event': 'Discharge level',
            'conditions':
                '{AH:03.0f} AH, SOC:{SOC:3d}%, I:{I:3.0f}A, L:{L}, l:{l}, H:{H}, B:{B:03d}, '
                'PT:{PT:03d}C, BT:{BT:03d}C, PV:{PV:6d}, M:{M}'.format(
                    AH=trunc(BinaryTools.unpack('uint32', x, 0x06) / 1000000.0),
                    B=BinaryTools.unpack('uint16', x, 0x02) - BinaryTools.unpack('uint16', x, 0x0),
                    I=trunc(BinaryTools.unpack('int32', x, 0x10) / 1000000.0),
                    L=BinaryTools.unpack('uint16', x, 0x0),
                    H=BinaryTools.unpack('uint16', x, 0x02),
                    PT=BinaryTools.unpack('uint8', x, 0x04),
                    BT=BinaryTools.unpack('uint8', x, 0x05),
                    PV=BinaryTools.unpack('uint32', x, 0x0b),
                    SOC=BinaryTools.unpack('uint8', x, 0x0a),
                    M={
                        0x01: 'Bike On',
                        0x02: 'Charge',
                        0x03: 'Idle'
                    }.get(BinaryTools.unpack('uint8', x, 0x0f), 'Unknown')
                )
        }

    # Additional format methods would go here...
    # (I'll include a few key ones for brevity)

    @classmethod
    def board_status_format(cls, x: bytearray) -> Dict[str, Any]:
        """Parse board status entry."""
        if not x:
            return {'event': 'Board Status', 'conditions': 'No additional data'}
        
        cause = BinaryTools.unpack('uint8', x, 0x00) if len(x) > 0 else 0
        return {
            'event': 'Board Status',
            'conditions': f'Reset cause: {cause}'
        }

    @classmethod
    def key_state_format(cls, x: bytearray) -> Dict[str, Any]:
        """Parse key state entry."""
        if not x:
            return {'event': 'Key State', 'conditions': 'No additional data'}
            
        state = BinaryTools.unpack('uint8', x, 0x00) if len(x) > 0 else 0
        state_text = 'On' if state else 'Off'
        return {
            'event': 'Key State',
            'conditions': state_text
        }

    MESSAGE_TYPES = {
        0x00: board_status_format,
        0x01: key_state_format,
        0x03: discharge_level_format,
        0x09: key_state_format,
        # Add more as needed...
    }

    @classmethod
    def decode_entry_segment(cls, data_block: bytearray, timezone_offset: float = 0) -> tuple:
        """Decode a single log entry segment."""
        length = BinaryTools.unpack('uint8', data_block, 0x01)
        message_type = BinaryTools.unpack('uint8', data_block, 0x02)
        
        if length < 7:
            return length, cls.unhandled_entry_format(message_type, bytearray()), 1
            
        message = data_block[7:length]
        
        try:
            if message_type in cls.MESSAGE_TYPES:
                entry = cls.MESSAGE_TYPES[message_type](message)
            else:
                entry = cls.unhandled_entry_format(message_type, message)
        except Exception:
            entry = cls.unhandled_entry_format(message_type, message)
            
        entry['time'] = cls.timestamp_from_event(data_block, timezone_offset=timezone_offset)
        
        # Apply improved message parsing and determine log level
        improved_event, improved_conditions, json_data, has_json_data = improve_message_parsing(
            entry.get('event', ''), entry.get('conditions', ''))
        entry['log_level'] = determine_log_level(improved_event, has_json_data)
        
        return length, entry, 0


class Gen3:
    """Parser for Generation 3 log format."""
    
    entry_data_fencepost = b'\x00\xb2'
    Entry = namedtuple('Gen3EntryType', ['event', 'time', 'conditions', 'uninterpreted', 'log_level'])
    min_timestamp = datetime.strptime('2019-01-01', '%Y-%M-%d')
    max_timestamp = datetime.now() + timedelta(days=365)
    
    @classmethod
    def timestamp_is_valid(cls, event_timestamp: datetime) -> bool:
        return cls.min_timestamp < event_timestamp < cls.max_timestamp
    
    @classmethod
    def payload_to_entry(cls, entry_payload: bytearray, hex_on_error=False, logger=None) -> 'Gen3.Entry':
        """Convert payload to entry."""
        timestamp_bytes = list(entry_payload[0:4])
        timestamp_int = int.from_bytes(timestamp_bytes, byteorder='big', signed=False)
        event_timestamp = datetime.fromtimestamp(timestamp_int)
        
        if not cls.timestamp_is_valid(event_timestamp) and logger:
            logger.warning('Timestamp out of normal range: {}'.format(event_timestamp))
            
        # Rest of the implementation...
        return cls.Entry(
            event="Unknown",
            time=event_timestamp.strftime(ZERO_TIME_FORMAT),
            conditions="",
            uninterpreted="",
            log_level="INFO"
        )