"""Low-level binary parsing utilities and value formatting helpers."""

import re
import string
import struct
from math import trunc
from typing import List, Union

from .constants import EMPTY_CSV_VALUE


# noinspection PyMissingOrEmptyDocstring
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
        return all(c in string.printable for c in bytes_or_str)

    @classmethod
    def bms_discharge_level_binary(cls, x):
        # Enhanced BMS discharge decoder with state names and better formatting
        state_names = {
            0x01: 'Bike On',
            0x02: 'Charge',
            0x03: 'Idle'
        }
        return {
            'event': 'BMS Discharge Level',
            'conditions':
                '{AH:03.0f}Ah, SOC:{SOC:3d}%, I:{I:+4.0f}A, State:{STATE}, '
                'LowCell:{L}mV, HighCell:{H}mV, Balance:{B:+4d}mV, UnloadedCell:{l}mV, '
                'PackTemp:{PT:3d}°C, BMSTemp:{BT:3d}°C, PackV:{PV:6.1f}V'.format(
                    AH=trunc(BinaryTools.unpack('uint32', x, 0x06) / 1000000.0),
                    SOC=BinaryTools.unpack('uint8', x, 0x0a),
                    I=trunc(BinaryTools.unpack('int32', x, 0x10) / 1000000.0),
                    STATE=state_names.get(BinaryTools.unpack('uint8', x, 0x0f), f'Unknown({BinaryTools.unpack("uint8", x, 0x0f)})'),
                    L=BinaryTools.unpack('uint16', x, 0x0),
                    H=BinaryTools.unpack('uint16', x, 0x02),
                    B=BinaryTools.unpack('uint16', x, 0x02) - BinaryTools.unpack('uint16', x, 0x0),
                    l=BinaryTools.unpack('uint16', x, 0x14),
                    PT=BinaryTools.unpack('uint8', x, 0x04),
                    BT=BinaryTools.unpack('uint8', x, 0x05),
                    PV=BinaryTools.unpack('uint32', x, 0x0b) / 1000.0)
        }

    @classmethod
    def bms_unknown_type_5(cls, x):
        """BMS Unknown Type 5 - raw hex display"""
        hex_data = ' '.join(f'{b:02x}' for b in x[:min(16, len(x))])
        return {
            'event': 'BMS Unknown Type 5',
            'conditions': f'Unknown: {hex_data}'
        }

    @classmethod
    def bms_unknown_type_14(cls, x):
        """BMS Unknown Type 14 - raw hex display"""
        hex_data = ' '.join(f'{b:02x}' for b in x[:min(16, len(x))])
        return {
            'event': 'BMS Unknown Type 14',
            'conditions': f'Unknown: {hex_data}'
        }

    @classmethod
    def mbb_unknown_type_28(cls, x):
        """MBB Unknown Type 28 - raw hex display"""
        hex_data = ' '.join(f'{b:02x}' for b in x[:min(16, len(x))])
        return {
            'event': 'MBB Unknown Type 28',
            'conditions': f'Unknown: {hex_data}'
        }

    @classmethod
    def mbb_unknown_type_38(cls, x):
        """MBB Unknown Type 38 - raw hex display"""
        hex_data = ' '.join(f'{b:02x}' for b in x[:min(16, len(x))])
        return {
            'event': 'MBB Unknown Type 38',
            'conditions': f'Unknown: {hex_data}'
        }

    @classmethod
    def mbb_bt_rx_buffer_overflow(cls, x):
        """MBB BT RX Buffer Overflow"""
        hex_data = ' '.join(f'{b:02x}' for b in x[:min(16, len(x))])
        return {
            'event': 'MBB BT RX Buffer Overflow',
            'conditions': f'Data: {hex_data}'
        }


vin_length = 17
vin_guaranteed_prefix = '538'


def is_vin(vin: str):
    """Whether the string matches a Zero VIN."""
    return (BinaryTools.is_printable(vin)
            and len(vin) == vin_length
            and vin.startswith(vin_guaranteed_prefix))


def convert_mv_to_v(milli_volts: int) -> float:
    return milli_volts / 1000.0


def convert_ratio_to_percent(numerator: Union[int, float], denominator: Union[int, float]) -> float:
    return numerator * 100 / denominator if denominator != 0 else 0


def convert_bit_to_on_off(bit: int) -> str:
    return 'On' if bit else 'Off'


def hex_of_value(value):
    if isinstance(value, str):
        return value
    if isinstance(value, float):
        return value.hex()
    if isinstance(value, bytearray):
        return value.hex()
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def display_bytes_hex(x: Union[List[int], bytearray, bytes, str]):
    byte_values = bytearray(x, 'utf8') if isinstance(x, str) else x
    return ' '.join(['0x{:02x}'.format(c) for c in byte_values])


def print_value_tabular(value, omit_units=False):
    """Stringify the value for CSV/TSV; treat None as empty text."""
    if value is None:
        return EMPTY_CSV_VALUE
    if isinstance(value, str) and not BinaryTools.is_printable(value):
        return display_bytes_hex(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, bytearray):
        return display_bytes_hex(value)
    if isinstance(value, float):
        return '{0:.2f}'.format(value)
    if omit_units and value is str:
        matches = re.match(r"^([0-9.]+)\s*([A-Za-z]+)$", value)
        if matches:
            return matches.group(1)

    # Clean up string values to be CSV-safe
    result = str(value)
    if isinstance(value, str):
        # Replace problematic characters that break CSV parsing
        result = result.replace('\t', ' ').replace('\n', ' ').replace('\r', ' ')
        result = result.replace(';', ':')  # Replace CSV delimiter
        # Remove non-printable characters except spaces
        result = ''.join(c for c in result if c.isprintable() or c == ' ')
        result = result.strip()

    return result
