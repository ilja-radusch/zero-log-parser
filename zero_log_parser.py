#!/usr/bin/env python3

"""
Little decoder utility to parse Zero Motorcycle main bike board (MBB) and
battery management system (BMS) logs. These may be extracted from the bike
using the Zero mobile app. Once paired over bluetooth, select 'Support' >
'Email bike logs' and send the logs to yourself rather than / in addition to
zero support.

Usage:

   $ python zero_log_parser.py <*.bin file> [-o output_file]

"""

import codecs
import json
import logging
import os
import re
import string
import struct
from collections import OrderedDict, namedtuple
from datetime import datetime, timedelta, timezone
from math import trunc
from time import gmtime, localtime, strftime
from typing import Dict, List, Union

# Localized time format - use system locale preference
ZERO_TIME_FORMAT = '%Y-%m-%d %H:%M:%S'  # ISO format is more universal
# The output from the MBB (via serial port) lists time as GMT-7
MBB_TIMESTAMP_GMT_OFFSET = -7 * 60 * 60

def get_local_timezone_offset():
    """Get the local system timezone offset in seconds from UTC"""
    local_now = datetime.now()
    utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Calculate offset in seconds
    offset = (local_now - utc_now).total_seconds()
    return int(offset)


def improve_message_parsing(event_text: str, conditions_text: str = None) -> tuple:
    """
    Improve message parsing by removing redundant prefixes and converting structured data to JSON.
    
    Returns tuple: (improved_event, improved_conditions, json_data)
    """
    if not event_text:
        return event_text, conditions_text, None
    
    improved_event = event_text
    improved_conditions = conditions_text
    json_data = None
    
    # Remove redundant DEBUG: prefix since we have log_level
    if improved_event.startswith('DEBUG: '):
        improved_event = improved_event[7:]
    elif improved_event.startswith('INFO: '):
        improved_event = improved_event[6:]
    elif improved_event.startswith('ERROR: '):
        improved_event = improved_event[7:]
    elif improved_event.startswith('WARNING: '):
        improved_event = improved_event[9:]
    
    # Parse structured data patterns and convert to JSON
    try:
        # Handle Discharge level messages
        if improved_event == 'Discharge level' and improved_conditions:
            discharge_match = re.match(
                r'(\d+) AH, SOC:\s*(\d+)%, I:\s*(-?\d+)A, L:(\d+), l:(\d+), H:(\d+), B:(\d+), PT:(\d+)C, BT:(\d+)C, PV:\s*(\d+), M:(.+)',
                improved_conditions
            )
            if discharge_match:
                json_data = {
                    'amp_hours': int(discharge_match.group(1)),
                    'state_of_charge_percent': int(discharge_match.group(2)),
                    'current_amps': int(discharge_match.group(3)),
                    'voltage_low': int(discharge_match.group(4)),
                    'voltage_low_cell': int(discharge_match.group(5)),
                    'voltage_high': int(discharge_match.group(6)),
                    'voltage_balance': int(discharge_match.group(7)),
                    'pack_temp_celsius': int(discharge_match.group(8)),
                    'bms_temp_celsius': int(discharge_match.group(9)),
                    'pack_voltage_mv': int(discharge_match.group(10)),
                    'mode': discharge_match.group(11).strip()
                }
                improved_conditions = json.dumps(json_data)
        
        # Handle SOC messages (comma-separated values)
        elif improved_event.startswith('SOC:'):
            soc_data = improved_event[4:]  # Remove 'SOC:' prefix
            if ',' in soc_data:
                values = [v.strip() for v in soc_data.split(',')]
                if len(values) >= 11:  # Extended format (11+ values)
                    json_data = {
                        'soc_raw_1': int(values[0]) if values[0].isdigit() else values[0],
                        'soc_raw_2': int(values[1]) if values[1].isdigit() else values[1],
                        'soc_raw_3': int(values[2]) if values[2].isdigit() else values[2],
                        'pack_voltage_mv': int(values[3]) if values[3].isdigit() else values[3],
                        'soc_percent_1': int(values[4]) if values[4].isdigit() else values[4],
                        'soc_percent_2': int(values[5]) if values[5].isdigit() else values[5],
                        'soc_percent_3': int(values[6]) if values[6].isdigit() else values[6],
                        'balance_count': int(values[7]) if values[7].isdigit() else values[7],
                        'voltage_max': int(values[8]) if values[8].isdigit() else values[8],
                        'voltage_min_1': int(values[9]) if values[9].isdigit() else values[9],
                        'voltage_min_2': int(values[10]) if values[10].isdigit() else values[10],
                        'current_ma': int(values[11]) if len(values) > 11 and values[11].isdigit() else (values[11] if len(values) > 11 else None)
                    }
                    improved_event = 'SOC Data'
                    improved_conditions = json.dumps(json_data)
                elif len(values) == 8:  # Compact format (8 values)
                    # Handle integer conversion with support for negative values
                    def safe_int(val):
                        try:
                            return int(val)
                        except ValueError:
                            return val
                    
                    json_data = {
                        'soc_raw_1': safe_int(values[0]),
                        'soc_raw_2': safe_int(values[1]),
                        'soc_raw_3': safe_int(values[2]),
                        'pack_voltage_mv': safe_int(values[3]),
                        'soc_percent_1': safe_int(values[4]),
                        'soc_percent_2': safe_int(values[5]),
                        'soc_percent_3': safe_int(values[6]),
                        'balance_or_current': safe_int(values[7])  # Could be balance count or current
                    }
                    improved_event = 'SOC Data'
                    improved_conditions = json.dumps(json_data)
        
        # Handle Riding status messages
        elif improved_event == 'Riding' and improved_conditions:
            riding_match = re.match(
                r'PackTemp: h (\d+)C, l (\d+)C, PackSOC:\s*(\d+)%, Vpack:([0-9.]+)V, MotAmps:\s*(-?\d+), BattAmps:\s*(-?\d+), Mods:\s*(\d+), MotTemp:\s*(-?\d+)C, CtrlTemp:\s*(-?\d+)C, AmbTemp:\s*(-?\d+)C, MotRPM:\s*(-?\d+), Odo:\s*(\d+)km',
                improved_conditions
            )
            if riding_match:
                json_data = {
                    'pack_temp_high_celsius': int(riding_match.group(1)),
                    'pack_temp_low_celsius': int(riding_match.group(2)),
                    'state_of_charge_percent': int(riding_match.group(3)),
                    'pack_voltage_volts': float(riding_match.group(4)),
                    'motor_current_amps': int(riding_match.group(5)),
                    'battery_current_amps': int(riding_match.group(6)),
                    'modules_status': int(riding_match.group(7)),
                    'motor_temp_celsius': int(riding_match.group(8)),
                    'controller_temp_celsius': int(riding_match.group(9)),
                    'ambient_temp_celsius': int(riding_match.group(10)),
                    'motor_rpm': int(riding_match.group(11)),
                    'odometer_km': int(riding_match.group(12))
                }
                improved_conditions = json.dumps(json_data)
        
        # Handle Charging status messages
        elif improved_event == 'Charging' and improved_conditions:
            charging_match = re.match(
                r'PackTemp: h (\d+)C, l (\d+)C, AmbTemp: (-?\d+)C, PackSOC:\s*(\d+)%, Vpack:([0-9.]+)V, BattAmps:\s*(-?\d+), Mods:\s*(\d+), MbbChgEn: (\w+), BmsChgEn: (\w+)',
                improved_conditions
            )
            if charging_match:
                json_data = {
                    'pack_temp_high_celsius': int(charging_match.group(1)),
                    'pack_temp_low_celsius': int(charging_match.group(2)),
                    'ambient_temp_celsius': int(charging_match.group(3)),
                    'state_of_charge_percent': int(charging_match.group(4)),
                    'pack_voltage_volts': float(charging_match.group(5)),
                    'battery_current_amps': int(charging_match.group(6)),
                    'modules_status': int(charging_match.group(7)),
                    'mbb_charge_enable': charging_match.group(8),
                    'bms_charge_enable': charging_match.group(9)
                }
                improved_conditions = json.dumps(json_data)
        
        # Handle Disarmed status messages  
        elif improved_event == 'Disarmed' and improved_conditions:
            disarmed_match = re.match(
                r'PackTemp: h (\d+)C, l (\d+)C, PackSOC:\s*(\d+)%, Vpack:([0-9.]+)V, MotAmps:\s*(-?\d+), BattAmps:\s*(-?\d+), Mods:\s*(\d+), MotTemp:\s*(-?\d+)C, CtrlTemp:\s*(-?\d+)C, AmbTemp:\s*(-?\d+)C, MotRPM:\s*(-?\d+), Odo:\s*(\d+)km',
                improved_conditions
            )
            if disarmed_match:
                json_data = {
                    'pack_temp_high_celsius': int(disarmed_match.group(1)),
                    'pack_temp_low_celsius': int(disarmed_match.group(2)),
                    'state_of_charge_percent': int(disarmed_match.group(3)),
                    'pack_voltage_volts': float(disarmed_match.group(4)),
                    'motor_current_amps': int(disarmed_match.group(5)),
                    'battery_current_amps': int(disarmed_match.group(6)),
                    'modules_status': int(disarmed_match.group(7)),
                    'motor_temp_celsius': int(disarmed_match.group(8)),
                    'controller_temp_celsius': int(disarmed_match.group(9)),
                    'ambient_temp_celsius': int(disarmed_match.group(10)),
                    'motor_rpm': int(disarmed_match.group(11)),
                    'odometer_km': int(disarmed_match.group(12))
                }
                improved_conditions = json.dumps(json_data)
        
        # Handle Contactor messages
        elif 'Contactor' in improved_event and improved_conditions:
            if 'Closing Contactor' in improved_event:
                contactor_match = re.match(
                    r'vmod: ([0-9.]+)V, maxsys: ([0-9.]+)V, minsys: ([0-9.]+)V, diff: ([0-9.]+)V, vcap: ([0-9.]+)V, prechg: (\d+)%',
                    improved_conditions
                )
                if contactor_match:
                    json_data = {
                        'module_voltage_volts': float(contactor_match.group(1)),
                        'max_system_voltage_volts': float(contactor_match.group(2)),
                        'min_system_voltage_volts': float(contactor_match.group(3)),
                        'voltage_difference_volts': float(contactor_match.group(4)),
                        'capacitor_voltage_volts': float(contactor_match.group(5)),
                        'precharge_percent': int(contactor_match.group(6))
                    }
                    improved_conditions = json.dumps(json_data)
            elif 'Opening Contactor' in improved_event:
                contactor_match = re.match(
                    r'vmod:\s*([0-9.]+)V, batt curr:\s*(-?\d+)A',
                    improved_conditions
                )
                if contactor_match:
                    json_data = {
                        'module_voltage_volts': float(contactor_match.group(1)),
                        'battery_current_amps': int(contactor_match.group(2))
                    }
                    improved_conditions = json.dumps(json_data)
            # Handle Pack V: patterns for contactor states
            elif 'Pack V:' in improved_conditions:
                if 'was Closed' in improved_event:
                    pack_v_match = re.match(
                        r'Pack V: (\d+)mV, Switched V: (\d+)mV, Prechg Pct: (\d+)%, Dischg Cur: (\d+)mA',
                        improved_conditions
                    )
                    if pack_v_match:
                        json_data = {
                            'pack_voltage_mv': int(pack_v_match.group(1)),
                            'switched_voltage_mv': int(pack_v_match.group(2)),
                            'precharge_percent': int(pack_v_match.group(3)),
                            'discharge_current_ma': int(pack_v_match.group(4))
                        }
                        improved_conditions = json.dumps(json_data)
                elif 'was Opened' in improved_event:
                    pack_v_match = re.match(
                        r'Pack V: (\d+)mV, Switched V: (\d+)mV, Prechg Pct: (\d+)%, Dischg Cur: (\d+)mA',
                        improved_conditions
                    )
                    if pack_v_match:
                        json_data = {
                            'pack_voltage_mv': int(pack_v_match.group(1)),
                            'switched_voltage_mv': int(pack_v_match.group(2)),
                            'precharge_percent': int(pack_v_match.group(3)),
                            'discharge_current_ma': int(pack_v_match.group(4))
                        }
                        improved_conditions = json.dumps(json_data)
                elif 'drive turned on' in improved_event:
                    pack_v_match = re.match(
                        r'Pack V: (\d+)mV, Switched V: (\d+)mV, Duty Cycle: (\d+)%',
                        improved_conditions
                    )
                    if pack_v_match:
                        json_data = {
                            'pack_voltage_mv': int(pack_v_match.group(1)),
                            'switched_voltage_mv': int(pack_v_match.group(2)),
                            'duty_cycle_percent': int(pack_v_match.group(3))
                        }
                        improved_conditions = json.dumps(json_data)
        
        # Handle Current Sensor Zeroed messages
        elif improved_event == 'Current Sensor Zeroed' and improved_conditions:
            sensor_match = re.match(
                r'old: (\d+)mV, new: (\d+)mV, corrfact: (\d+)',
                improved_conditions
            )
            if sensor_match:
                json_data = {
                    'old_voltage_mv': int(sensor_match.group(1)),
                    'new_voltage_mv': int(sensor_match.group(2)),
                    'correction_factor': int(sensor_match.group(3))
                }
                improved_conditions = json.dumps(json_data)
        
        # Handle Module Registered messages
        elif 'Registered' in improved_event and improved_conditions:
            module_match = re.match(
                r'serial: ([^,]+),\s*vmod: ([0-9.]+)V',
                improved_conditions
            )
            if module_match:
                json_data = {
                    'serial_number': module_match.group(1).strip(),
                    'module_voltage_volts': float(module_match.group(2))
                }
                improved_conditions = json.dumps(json_data)
        
        # Handle Charger messages (Charging/Stopped) - these are in the event field
        elif 'Charger' in improved_event and 'SN:' in improved_event:
            charger_match = re.search(
                r'SN:(\d+) SW:(\d+) (\d+)Vac\s*(\d+)Hz EVSE\s*(\d+)A',
                improved_event
            )
            if charger_match:
                # Extract the base charger message without the parameters
                base_event = re.sub(r' SN:.*$', '', improved_event)
                json_data = {
                    'serial_number': charger_match.group(1),
                    'software_version': int(charger_match.group(2)),
                    'voltage_ac': int(charger_match.group(3)),
                    'frequency_hz': int(charger_match.group(4)),
                    'evse_current_amps': int(charger_match.group(5))
                }
                improved_event = base_event
                improved_conditions = json.dumps(json_data)
        
        # Handle SEVCON CAN EMCY Frame messages  
        elif improved_event == 'SEVCON CAN EMCY Frame' and improved_conditions:
            sevcon_match = re.match(
                r'Error Code: 0x([0-9A-F]+), Error Reg: 0x([0-9A-F]+), Sevcon Error Code: 0x([0-9A-F]+), Data: ([0-9A-F\s]+), (.+)',
                improved_conditions
            )
            if sevcon_match:
                json_data = {
                    'error_code': '0x' + sevcon_match.group(1),
                    'error_register': '0x' + sevcon_match.group(2),
                    'sevcon_error_code': '0x' + sevcon_match.group(3),
                    'data': sevcon_match.group(4).strip(),
                    'cause': sevcon_match.group(5).strip()
                }
                improved_conditions = json.dumps(json_data)
        
        # Handle Voltage Across Contactor messages
        elif improved_event.startswith('Voltage Across Contactor:'):
            contactor_match = re.match(
                r'Voltage Across Contactor: (\d+)mV \(([^)]+)\)',
                improved_event
            )
            if contactor_match:
                json_data = {
                    'voltage_mv': int(contactor_match.group(1)),
                    'voltage_v': round(int(contactor_match.group(1)) / 1000.0, 3),
                    'status': contactor_match.group(2)
                }
                improved_event = 'Voltage Across Contactor'
                improved_conditions = json.dumps(json_data)
        
        # Handle Disabling Due to lack of CAN control messages
        elif 'Disabling Due to lack of CAN control messages' in improved_event and 'Dischg Cur:' in improved_event:
            can_disable_match = re.search(
                r'Dischg Cur: (\d+)mA',
                improved_event
            )
            if can_disable_match:
                json_data = {
                    'discharge_current_ma': int(can_disable_match.group(1)),
                    'discharge_current_a': round(int(can_disable_match.group(1)) / 1000.0, 3)
                }
                improved_event = 'Disabling Due to lack of CAN control messages'
                improved_conditions = json.dumps(json_data)
        
        # Handle State Machine Charge Fault messages
        elif 'State Machine Charge Fault' in improved_event:
            charge_fault_match = re.search(
                r'State Machine Charge Fault\. Mode:(\d+), I:(\d+)mA, dV/dt:(\d+)mV/m\((\d+)mV/m\), TSSC:(\d+)ms, TSCO:(\d+)ms, TIPS:(\d+)ms',
                improved_event
            )
            if charge_fault_match:
                json_data = {
                    'mode': int(charge_fault_match.group(1)),
                    'current_ma': int(charge_fault_match.group(2)),
                    'current_a': round(int(charge_fault_match.group(2)) / 1000.0, 3),
                    'voltage_rate_mv_per_min': int(charge_fault_match.group(3)),
                    'voltage_rate_mv_per_min_alt': int(charge_fault_match.group(4)),
                    'tssc_ms': int(charge_fault_match.group(5)),
                    'tsco_ms': int(charge_fault_match.group(6)),
                    'tips_ms': int(charge_fault_match.group(7)),
                    'tssc_seconds': round(int(charge_fault_match.group(5)) / 1000.0, 3),
                    'tsco_seconds': round(int(charge_fault_match.group(6)) / 1000.0, 3),
                    'tips_seconds': round(int(charge_fault_match.group(7)) / 1000.0, 3)
                }
                improved_event = 'State Machine Charge Fault'
                improved_conditions = json.dumps(json_data)
        
        # Handle Rev: firmware version messages
        elif improved_event.startswith('Rev:'):
            rev_match = re.match(
                r'Rev:(\d+),Build:(\d{4}-\d{2}-\d{2})_(\d{6})\s+(\d+)\s+(\w+)',
                improved_event
            )
            if rev_match:
                build_date = rev_match.group(2)
                build_time_raw = rev_match.group(3)
                # Parse time as HHMMSS
                build_time = f"{build_time_raw[:2]}:{build_time_raw[2:4]}:{build_time_raw[4:6]}"
                
                json_data = {
                    'revision': int(rev_match.group(1)),
                    'build_date': build_date,
                    'build_time': build_time,
                    'build_datetime': f"{build_date} {build_time}",
                    'build_number': int(rev_match.group(4)),
                    'branch': rev_match.group(5)
                }
                improved_event = 'Firmware Version'
                improved_conditions = json.dumps(json_data)
    
    except (ValueError, AttributeError, IndexError) as e:
        # If parsing fails, keep original format
        pass
    
    # Handle hex+A pattern entries (likely voltage readings)
    if re.match(r'^[0-9A-Fa-f]{3,4}A$', improved_event) and not improved_conditions:
        hex_value = improved_event[:-1]  # Remove 'A' suffix
        try:
            decimal_value = int(hex_value, 16)
            # These appear to be voltage readings in millivolts
            json_data = {
                'hex_value': f'0x{hex_value.upper()}',
                'voltage_mv': decimal_value,
                'voltage_v': round(decimal_value / 1000.0, 3)
            }
            improved_event = 'Voltage Reading'
            improved_conditions = json.dumps(json_data)
        except ValueError:
            # Keep original if hex parsing fails
            pass
    
    # Determine if this entry contains JSON data
    has_json_data = json_data is not None
    
    return improved_event, improved_conditions, json_data, has_json_data


def determine_log_level(message: str, is_json_data: bool = False) -> str:
    """Determine log level based on message content patterns"""
    if not message:
        return 'UNKNOWN'
    
    # JSON data entries get special DATA log level
    if is_json_data:
        return 'DATA'
    
    message_upper = message.upper()
    
    # Explicit level indicators (check for redundant prefixes)
    if message.startswith('DEBUG:'):
        return 'DEBUG'
    elif message.startswith('INFO:'):
        return 'INFO'
    elif message.startswith('ERROR:') or message.startswith('FAULT:'):
        return 'ERROR'
    elif message.startswith('WARNING:') or message.startswith('WARN:'):
        return 'WARNING'
    
    # Error patterns
    if any(pattern in message_upper for pattern in [
        'ERROR', 'FAULT', 'FAILED', 'FAILURE', 'CRITICAL', 'ALARM',
        'ABORT', 'EXCEPTION', 'TIMEOUT'
    ]):
        return 'ERROR'
    
    # Warning patterns
    if any(pattern in message_upper for pattern in [
        'WARNING', 'WARN', 'CAUTION', 'OVERTEMP', 'UNDERVOLT', 'OVERVOLT'
    ]):
        return 'WARNING'
    
    # State change patterns (important operational states)
    if any(pattern in message_upper for pattern in [
        'RIDING', 'DISARMED', 'CHARGING', 'ARMED', 'STANDBY',
        'POWER ON', 'POWER OFF', 'SLEEP', 'WAKE', 'BOOT',
        'STARTUP', 'SHUTDOWN', 'CONNECTED', 'DISCONNECTED'
    ]):
        return 'STATE'
    
    # System/informational patterns
    if any(pattern in message_upper for pattern in [
        'MODULE', 'SEVCON', 'CONTACTOR', 'TEMPERATURE', 'VOLTAGE',
        'CURRENT', 'BATTERY', 'MOTOR', 'CONFIG', 'SETTING'
    ]):
        return 'INFO'
    
    # Debug patterns (verbose/detailed info)
    if any(pattern in message_upper for pattern in [
        'DEBUG', 'TRACE', 'VERBOSE', 'DETAIL'
    ]):
        return 'DEBUG'
    
    # Default to INFO for unmatched messages
    return 'INFO'


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


vin_length = 17
vin_guaranteed_prefix = '538'


def is_vin(vin: str):
    """Whether the string matches a Zero VIN."""
    return (BinaryTools.is_printable(vin)
            and len(vin) == vin_length
            and vin.startswith(vin_guaranteed_prefix))


# noinspection PyMissingOrEmptyDocstring
class LogFile:
    """
    Wrapper for our raw log file
    """

    def __init__(self, file_path: str, logger=None):
        self.file_path = file_path
        self._data = bytearray()
        self.reload()
        self.log_type = self.get_log_type()

    def reload(self):
        with open(self.file_path, 'rb') as f:
            self._data = bytearray(f.read())

    def index_of_sequence(self, sequence, start=None):
        try:
            return self._data.index(sequence, start)
        except ValueError:
            return None

    def indexes_of_sequence(self, sequence, start=None):
        result = []
        last_index = self.index_of_sequence(sequence, start)
        while last_index is not None:
            result.append(last_index)
            last_index = self.index_of_sequence(sequence, last_index + 1)
        return result

    def unpack(self, type_name, address, count=1, offset=0):
        return BinaryTools.unpack(type_name, self._data, address + offset,
                                  count=count)

    def decode_str(self, address, count=1, offset=0, encoding='utf-8'):
        return BinaryTools.decode_str(BinaryTools.unpack('char', self._data, address + offset,
                                                         count=count), encoding=encoding)

    def unpack_str(self, address, count=1, offset=0, encoding='utf-8') -> str:
        """Unpacks and decodes UTF-8 strings from a test segment, ignoring any errors"""
        unpacked = self.unpack('char', address, count, offset)
        return BinaryTools.decode_str(unpacked.partition(b'\0')[0], encoding=encoding)

    def is_printable(self, address, count=1, offset=0) -> bool:
        unpacked = self.unpack('char', address, count, offset).decode('utf-8', 'ignore')
        return BinaryTools.is_printable(unpacked) and len(unpacked) == count

    def extract(self, start_address, length, offset=0):
        return self._data[start_address + offset:
                          start_address + length + offset]

    def raw(self):
        return bytearray(self._data)

    log_type_mbb = 'MBB'
    log_type_bms = 'BMS'
    log_type_unknown = 'Unknown Type'

    def get_log_type(self):
        log_type = None
        if self.is_printable(0x000, count=3):
            log_type = self.unpack_str(0x000, count=3)
        elif self.is_printable(0x00d, count=3):
            log_type = self.unpack_str(0x00d, count=3)
        elif self.log_type_mbb in self.file_path.upper():
            log_type = self.log_type_mbb
        elif self.log_type_bms in self.file_path.upper():
            log_type = self.log_type_bms
        if log_type not in [self.log_type_mbb, self.log_type_bms]:
            log_type = self.log_type_unknown
        return log_type

    def is_mbb(self):
        return self.log_type == self.log_type_mbb

    def is_bms(self):
        return self.log_type == self.log_type_bms

    def is_unknown(self):
        return self.log_type == self.log_type_unknown

    def get_filename_vin(self):
        basename = os.path.basename(self.file_path)
        if basename and len(basename) > vin_length and vin_guaranteed_prefix in basename:
            vin_index = basename.index(vin_guaranteed_prefix)
            return basename[vin_index:vin_index + vin_length]


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


EMPTY_CSV_VALUE = ''
CSV_DELIMITER = ';'


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
    return str(value)


class Gen2:
    @classmethod
    def timestamp_from_event(cls, unescaped_block, use_local_time=True, timezone_offset=None):
        timestamp = BinaryTools.unpack('uint32', unescaped_block, 0x01)
        if timestamp > 0xfff:
            # Apply timezone offset and use GMT to avoid double timezone conversion
            adjusted_timestamp = timestamp + (timezone_offset or 0)
            timestamp_corrected = gmtime(adjusted_timestamp)
            return strftime(ZERO_TIME_FORMAT, timestamp_corrected)
        else:
            return str(timestamp)
    
    @classmethod
    def interpolate_missing_timestamps(cls, entries_with_metadata, logger=None):
        """
        Improve missing timestamp detection by interpolating from neighboring entries.
        
        Args:
            entries_with_metadata: List of tuples (sort_timestamp, entry_payload, entry_num)
            logger: Optional logger for debugging
            
        Returns:
            List of tuples with improved timestamps
        """
        if not entries_with_metadata:
            return entries_with_metadata
            
        improved_entries = []
        
        # First pass: identify entries with valid and invalid timestamps
        for i, (sort_timestamp, entry_payload, entry_num) in enumerate(entries_with_metadata):
            time_str = entry_payload.get('time', '0')
            has_valid_timestamp = not time_str.isdigit() and sort_timestamp > 0
            
            improved_entries.append({
                'index': i,
                'sort_timestamp': sort_timestamp,
                'entry_payload': entry_payload,
                'entry_num': entry_num,
                'has_valid_timestamp': has_valid_timestamp,
                'original_time_str': time_str
            })
        
        # Second pass: interpolate missing timestamps
        for i, entry in enumerate(improved_entries):
            if not entry['has_valid_timestamp']:
                # Find nearest valid timestamps before and after
                before_entry = None
                after_entry = None
                
                # Look backwards for valid timestamp
                for j in range(i - 1, -1, -1):
                    if improved_entries[j]['has_valid_timestamp']:
                        before_entry = improved_entries[j]
                        break
                
                # Look forwards for valid timestamp  
                for j in range(i + 1, len(improved_entries)):
                    if improved_entries[j]['has_valid_timestamp']:
                        after_entry = improved_entries[j]
                        break
                
                # Interpolate timestamp if we have neighbors
                interpolated_timestamp = None
                interpolated_time_str = None
                
                if before_entry and after_entry:
                    # Interpolate between two valid timestamps
                    before_ts = before_entry['sort_timestamp']
                    after_ts = after_entry['sort_timestamp']
                    before_entry_num = before_entry['entry_num']
                    after_entry_num = after_entry['entry_num']
                    current_entry_num = entry['entry_num']
                    
                    # Calculate position ratio based on entry numbers
                    if after_entry_num != before_entry_num:
                        ratio = (current_entry_num - before_entry_num) / (after_entry_num - before_entry_num)
                        interpolated_timestamp = before_ts + ratio * (after_ts - before_ts)
                    else:
                        interpolated_timestamp = before_ts
                        
                elif before_entry:
                    # Extrapolate from previous entry (assume 1 second interval)
                    entry_gap = entry['entry_num'] - before_entry['entry_num']
                    interpolated_timestamp = before_entry['sort_timestamp'] + entry_gap
                    
                elif after_entry:
                    # Extrapolate from next entry (assume 1 second interval)  
                    entry_gap = after_entry['entry_num'] - entry['entry_num']
                    interpolated_timestamp = after_entry['sort_timestamp'] - entry_gap
                
                # Apply interpolated timestamp if we calculated one
                if interpolated_timestamp and interpolated_timestamp > 0:
                    try:
                        from datetime import datetime
                        interpolated_dt = datetime.fromtimestamp(interpolated_timestamp)
                        interpolated_time_str = interpolated_dt.strftime(ZERO_TIME_FORMAT)
                        
                        # Update the entry
                        entry['sort_timestamp'] = interpolated_timestamp
                        entry['entry_payload']['time'] = interpolated_time_str
                        entry['has_valid_timestamp'] = True
                        
                        if logger:
                            logger.info('Interpolated timestamp for entry %d: %s (was: %s)', 
                                       entry['entry_num'] + 1, interpolated_time_str, entry['original_time_str'])
                    except:
                        # If interpolation fails, keep original
                        pass
        
        # Return in the original format
        return [(entry['sort_timestamp'], entry['entry_payload'], entry['entry_num']) 
                for entry in improved_entries]

    @classmethod
    def bms_discharge_level(cls, x):
        bike = {
            0x01: 'Bike On',
            0x02: 'Charge',
            0x03: 'Idle'
        }
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
                    SOC=BinaryTools.unpack('uint8', x, 0x0a),
                    PV=BinaryTools.unpack('uint32', x, 0x0b),
                    l=BinaryTools.unpack('uint16', x, 0x14),
                    M=bike.get(BinaryTools.unpack('uint8', x, 0x0f)),
                    X=BinaryTools.unpack('uint16', x, 0x16))
        }

    @classmethod
    def bms_charge_event_fields(cls, x):
        return {
            'AH': trunc(BinaryTools.unpack('uint32', x, 0x06) / 1000000.0),
            'B': BinaryTools.unpack('uint16', x, 0x02) - BinaryTools.unpack('uint16', x, 0x0),
            'L': BinaryTools.unpack('uint16', x, 0x00),
            'H': BinaryTools.unpack('uint16', x, 0x02),
            'PT': BinaryTools.unpack('uint8', x, 0x04),
            'BT': BinaryTools.unpack('uint8', x, 0x05),
            'SOC': BinaryTools.unpack('uint8', x, 0x0a),
            'PV': BinaryTools.unpack('uint32', x, 0x0b)
        }

    @classmethod
    def bms_charge_full(cls, x):
        fields = cls.bms_charge_event_fields(x)
        return {
            'event': 'Charged To Full',
            'conditions':
                ('{AH:03.0f} AH, SOC: {SOC}%,         L:{L},         H:{H}, B:{B:03d}, '
                 'PT:{PT:03d}C, BT:{BT:03d}C, PV:{PV:6d}').format_map(fields)
        }

    @classmethod
    def bms_discharge_low(cls, x):
        fields = cls.bms_charge_event_fields(x)

        return {
            'event': 'Discharged To Low',
            'conditions':
                ('{AH:03.0f} AH, SOC:{SOC:3d}%,         L:{L},         H:{H}, B:{B:03d}, '
                 'PT:{PT:03d}C, BT:{BT:03d}C, PV:{PV:6d}').format_map(fields)
        }

    @classmethod
    def bms_system_state(cls, x):
        return {
            'event': 'System Turned ' + convert_bit_to_on_off(BinaryTools.unpack('bool', x, 0x0))
        }

    @classmethod
    def bms_soc_adj_voltage(cls, x):
        return {
            'event': 'SOC adjusted for voltage',
            'conditions':
                ('old:   {old}uAH (soc:{old_soc}%), '
                 'new:   {new}uAH (soc:{new_soc}%), '
                 'low cell: {low} mV').format(
                    old=BinaryTools.unpack('uint32', x, 0x00),
                    old_soc=BinaryTools.unpack('uint8', x, 0x04),
                    new=BinaryTools.unpack('uint32', x, 0x05),
                    new_soc=BinaryTools.unpack('uint8', x, 0x09),
                    low=BinaryTools.unpack('uint16', x, 0x0a))
        }

    @classmethod
    def bms_curr_sens_zero(cls, x):
        return {
            'event': 'Current Sensor Zeroed',
            'conditions': 'old: {old}mV, new: {new}mV, corrfact: {corrfact}'.format(
                old=BinaryTools.unpack('uint16', x, 0x00),
                new=BinaryTools.unpack('uint16', x, 0x02),
                corrfact=BinaryTools.unpack('uint8', x, 0x04))
        }

    @classmethod
    def bms_state(cls, x):
        entering_hibernate = BinaryTools.unpack('bool', x, 0x0)
        return {
            'event': ('Entering' if entering_hibernate else 'Exiting') + ' Hibernate'
        }

    @classmethod
    def bms_isolation_fault(cls, x):
        return {
            'event': 'Chassis Isolation Fault',
            'conditions': '{ohms} ohms to cell {cell}'.format(
                ohms=BinaryTools.unpack('uint32', x, 0x00),
                cell=BinaryTools.unpack('uint8', x, 0x04))
        }

    @classmethod
    def bms_reflash(cls, x):
        return dict(event='BMS Reflash', conditions='Revision {rev}, ' 'Built {build}'.format(
            rev=BinaryTools.unpack('uint8', x, 0x00),
            build=BinaryTools.unpack_str(x, 0x01, 20)))

    @classmethod
    def bms_change_can_id(cls, x):
        return {
            'event': 'Changed CAN Node ID',
            'conditions': 'old: {old:02d}, new: {new:02d}'.format(
                old=BinaryTools.unpack('uint8', x, 0x00),
                new=BinaryTools.unpack('uint8', x, 0x01))
        }

    @classmethod
    def bms_contactor_state(cls, x):
        pack_voltage = BinaryTools.unpack('uint32', x, 0x01)
        switched_voltage = BinaryTools.unpack('uint32', x, 0x05)
        return {
            'event': '{state}'.format(
                state='Contactor was ' +
                      ('Closed' if BinaryTools.unpack('bool', x, 0x0) else 'Opened')),
            'conditions':
                ('Pack V: {pv}mV, '
                 'Switched V: {sv}mV, '
                 'Prechg Pct: {pc:2.0f}%, '
                 'Dischg Cur: {dc}mA').format(
                    pv=pack_voltage,
                    sv=switched_voltage,
                    pc=convert_ratio_to_percent(switched_voltage, pack_voltage),
                    dc=BinaryTools.unpack('int32', x, 0x09))
        }

    @classmethod
    def bms_discharge_cut(cls, x):
        return {
            'event': 'Discharge cutback',
            'conditions': '{cut:2.0f}%'.format(
                cut=convert_ratio_to_percent(BinaryTools.unpack('uint8', x, 0x00), 255.0)
            )
        }

    @classmethod
    def bms_contactor_drive(cls, x):
        return {
            'event': 'Contactor drive turned on',
            'conditions': 'Pack V: {pv}mV, Switched V: {sv}mV, Duty Cycle: {dc}%'.format(
                pv=BinaryTools.unpack('uint32', x, 0x01),
                sv=BinaryTools.unpack('uint32', x, 0x05),
                dc=BinaryTools.unpack('uint8', x, 0x09))
        }

    @classmethod
    def debug_message(cls, x):
        return {
            'event': BinaryTools.unpack_str(x, 0x0, count=len(x) - 1)
        }

    @classmethod
    def board_status(cls, x):
        causes = {
            0x04: 'Software',
        }

        return {
            'event': 'BMS Reset',
            'conditions': causes.get(BinaryTools.unpack('uint8', x, 0x00),
                                     'Unknown')
        }

    @classmethod
    def key_state(cls, x):
        key_on = BinaryTools.unpack('bool', x, 0x0)

        return {
            'event': 'Key ' + convert_bit_to_on_off(key_on) + (' ' if key_on else '')
        }

    @classmethod
    def battery_can_link_up(cls, x):
        return {
            'event': 'Module {module:02} CAN Link Up'.format(
                module=BinaryTools.unpack('uint8', x, 0x0)
            )
        }

    @classmethod
    def battery_can_link_down(cls, x):
        return {
            'event': 'Module {module:02} CAN Link Down'.format(
                module=BinaryTools.unpack('uint8', x, 0x0)
            )
        }

    @classmethod
    def sevcon_can_link_up(cls, _):
        return {
            'event': 'Sevcon CAN Link Up'
        }

    @classmethod
    def sevcon_can_link_down(cls, x):
        return {
            'event': 'Sevcon CAN Link Down'
        }

    @classmethod
    def run_status(cls, x):
        mod_translate = {
            0x00: '00',
            0x01: '10',
            0x02: '01',
            0x03: '11',
        }

        return {
            'event': 'Riding',
            'conditions':
                ('PackTemp: h {pack_temp_hi}C, l {pack_temp_low}C, '
                 'PackSOC:{soc:3d}%, '
                 'Vpack:{pack_voltage:7.3f}V, '
                 'MotAmps:{motor_current:4d}, BattAmps:{battery_current:4d}, '
                 'Mods: {mods}, '
                 'MotTemp:{motor_temp:4d}C, CtrlTemp:{controller_temp:4d}C, '
                 'AmbTemp:{ambient_temp:4d}C, '
                 'MotRPM:{rpm:4d}, '
                 'Odo:{odometer:5d}km').format(
                    pack_temp_hi=BinaryTools.unpack('uint8', x, 0x0),
                    pack_temp_low=BinaryTools.unpack('uint8', x, 0x1),
                    soc=BinaryTools.unpack('uint16', x, 0x2),
                    pack_voltage=convert_mv_to_v(BinaryTools.unpack('uint32', x, 0x4)),
                    motor_temp=BinaryTools.unpack('int16', x, 0x8),
                    controller_temp=BinaryTools.unpack('int16', x, 0xa),
                    rpm=BinaryTools.unpack('uint16', x, 0xc),
                    battery_current=BinaryTools.unpack('int16', x, 0x10),
                    mods=mod_translate.get(BinaryTools.unpack('uint8', x, 0x12),
                                           'Unknown'),
                    motor_current=BinaryTools.unpack('int16', x, 0x13),
                    ambient_temp=BinaryTools.unpack('int16', x, 0x15),
                    odometer=BinaryTools.unpack('uint32', x, 0x17))
        }

    @classmethod
    def charging_status(cls, x):
        return {
            'event': 'Charging',
            'conditions':
                'PackTemp: h {pack_temp_hi}C, l {pack_temp_low}C, AmbTemp: {ambient_temp}C, '
                'PackSOC:{soc:3d}%, Vpack:{pack_voltage:7.3f}V, BattAmps: {battery_current:3d}, '
                'Mods: {mods:02b}, MbbChgEn: Yes, BmsChgEn: No'.format(
                    pack_temp_hi=BinaryTools.unpack('uint8', x, 0x00),
                    pack_temp_low=BinaryTools.unpack('uint8', x, 0x01),
                    soc=BinaryTools.unpack('uint16', x, 0x02),
                    pack_voltage=convert_mv_to_v(BinaryTools.unpack('uint32', x, 0x4)),
                    battery_current=BinaryTools.unpack('int8', x, 0x08),
                    mods=BinaryTools.unpack('uint8', x, 0x0c),
                    ambient_temp=BinaryTools.unpack('int8', x, 0x0d))
        }

    @classmethod
    def sevcon_status(cls, x):
        cause = {
            0x4681: 'Preop',
            0x4884: 'Sequence Fault',
            0x4981: 'Throttle Fault',
        }

        return {
            'event': 'SEVCON CAN EMCY Frame',
            'conditions':
                ('Error Code: 0x{code:04X}, Error Reg: 0x{reg:02X}, '
                 'Sevcon Error Code: 0x{sevcon_code:04X}, Data: {data}, {cause}').format(
                    code=BinaryTools.unpack('uint16', x, 0x00),
                    reg=BinaryTools.unpack('uint8', x, 0x04),
                    sevcon_code=BinaryTools.unpack('uint16', x, 0x02),
                    data=' '.join(['{:02X}'.format(c) for c in x[5:]]),
                    cause=cause.get(BinaryTools.unpack('uint16', x, 0x02), 'Unknown')
                )
        }

    @classmethod
    def charger_status(cls, x):
        states = {
            0x00: 'Disconnected',
            0x01: 'Connected',
        }

        name = {
            0x00: 'Calex 720W',
            0x01: 'Calex 1200W',
            0x02: 'External Chg 0',
            0x03: 'External Chg 1',
        }

        charger_state = BinaryTools.unpack('uint8', x, 0x1)
        charger_id = BinaryTools.unpack('uint8', x, 0x0)
        return {
            'event': '{name} Charger {charger_id} {state:13s}'.format(
                charger_id=charger_id,
                state=states.get(charger_state),
                name=name.get(charger_id, 'Unknown')
            )
        }

    @classmethod
    def battery_status(cls, x):
        opening_contactor = 'Opening Contactor'
        closing_contactor = 'Closing Contactor'
        registered = 'Registered'
        events = {
            0x00: opening_contactor,
            0x01: closing_contactor,
            0x02: registered,
        }

        event = BinaryTools.unpack('uint8', x, 0x0)
        event_name = events.get(event, 'Unknown (0x{:02x})'.format(event))

        mod_volt = convert_mv_to_v(BinaryTools.unpack('uint32', x, 0x2))
        sys_max = convert_mv_to_v(BinaryTools.unpack('uint32', x, 0x6))
        sys_min = convert_mv_to_v(BinaryTools.unpack('uint32', x, 0xa))
        capacitor_volt = convert_mv_to_v(BinaryTools.unpack('uint32', x, 0x0e))
        battery_current = BinaryTools.unpack('int16', x, 0x12)
        serial_no = BinaryTools.unpack_str(x, 0x14, count=len(x[0x14:]))
        # Ensure the serial is printable
        printable_serial_no = ''.join(c for c in serial_no
                                      if c not in string.printable)
        if not printable_serial_no:
            printable_serial_no = hex_of_value(serial_no)
        if event_name == opening_contactor:
            conditions_msg = 'vmod: {modvolt:7.3f}V, batt curr: {batcurr:3.0f}A'.format(
                modvolt=mod_volt,
                batcurr=battery_current
            )
        elif event_name == closing_contactor:
            conditions_msg = ('vmod: {modvolt:7.3f}V, maxsys: {sysmax:7.3f}V, '
                              'minsys: {sysmin:7.3f}V, diff: {diff:0.03f}V, vcap: {vcap:6.3f}V, '
                              'prechg: {prechg:2.0f}%').format(
                modvolt=mod_volt,
                sysmax=sys_max,
                sysmin=sys_min,
                vcap=capacitor_volt,
                batcurr=battery_current,
                serial=printable_serial_no,
                diff=sys_max - sys_min,
                prechg=convert_ratio_to_percent(capacitor_volt, mod_volt))
        elif event_name == registered:
            conditions_msg = 'serial: {serial},  vmod: {modvolt:3.3f}V'.format(
                serial=printable_serial_no,
                modvolt=mod_volt
            )
        else:
            conditions_msg = ''

        return {
            'event': 'Module {module:02} {event}'.format(
                module=BinaryTools.unpack('uint8', x, 0x1),
                event=event_name
            ),
            'conditions': conditions_msg
        }

    @classmethod
    def power_state(cls, x):
        sources = {
            0x01: 'Key Switch',
            0x02: 'Ext Charger 0',
            0x03: 'Ext Charger 1',
            0x04: 'Onboard Charger',
        }

        power_on_cause = BinaryTools.unpack('uint8', x, 0x1)
        power_on = BinaryTools.unpack('bool', x, 0x0)

        return {
            'event': 'Power ' + convert_bit_to_on_off(power_on),
            'conditions': sources.get(power_on_cause, 'Unknown')
        }

    @classmethod
    def sevcon_power_state(cls, x):
        return {
            'event': 'Sevcon Turned ' + convert_bit_to_on_off(BinaryTools.unpack('bool', x, 0x0))
        }

    @classmethod
    def show_bluetooth_state(cls, x):
        return {
            'event': 'BT RX buffer reset'
        }

    @classmethod
    def battery_discharge_current_limited(cls, x):
        limit = BinaryTools.unpack('uint16', x, 0x00)
        max_amp = BinaryTools.unpack('uint16', x, 0x05)

        return {
            'event': 'Batt Dischg Cur Limited',
            'conditions':
                '{limit} A ({percent:.2f}%), MinCell: {min_cell}mV, MaxPackTemp: {temp}C'.format(
                    limit=limit,
                    min_cell=BinaryTools.unpack('uint16', x, 0x02),
                    temp=BinaryTools.unpack('uint8', x, 0x04),
                    max_amp=max_amp,
                    percent=convert_ratio_to_percent(limit, max_amp)
                )
        }

    @classmethod
    def low_chassis_isolation(cls, x):
        return {
            'event': 'Low Chassis Isolation',
            'conditions': '{kohms} KOhms to cell {cell}'.format(
                kohms=BinaryTools.unpack('uint32', x, 0x00),
                cell=BinaryTools.unpack('uint8', x, 0x04)
            )
        }

    @classmethod
    def precharge_decay_too_steep(cls, x):
        return {
            'event': 'Precharge Decay Too Steep. Restarting Sevcon.'
        }

    @classmethod
    def disarmed_status(cls, x):
        return {
            'event': 'Disarmed',
            'conditions':
                ('PackTemp: h {pack_temp_hi}C, l {pack_temp_low}C, '
                 'PackSOC:{soc:3d}%, '
                 'Vpack:{pack_voltage:03.3f}V, '
                 'MotAmps:{motor_current:4d}, BattAmps:{battery_current:4d}, '
                 'Mods: {mods:02b}, '
                 'MotTemp:{motor_temp:4d}C, CtrlTemp:{controller_temp:4d}C, '
                 'AmbTemp:{ambient_temp:4d}C, '
                 'MotRPM:{rpm:4d}, '
                 'Odo:{odometer:5d}km').format(
                    pack_temp_hi=BinaryTools.unpack('uint8', x, 0x0),
                    pack_temp_low=BinaryTools.unpack('uint8', x, 0x1),
                    soc=BinaryTools.unpack('uint16', x, 0x2),
                    pack_voltage=convert_mv_to_v(BinaryTools.unpack('uint32', x, 0x4)),
                    motor_temp=BinaryTools.unpack('int16', x, 0x8),
                    controller_temp=BinaryTools.unpack('int16', x, 0xa),
                    rpm=BinaryTools.unpack('uint16', x, 0xc),
                    battery_current=BinaryTools.unpack('uint8', x, 0x10),
                    mods=BinaryTools.unpack('uint8', x, 0x12),
                    motor_current=BinaryTools.unpack('int8', x, 0x13),
                    ambient_temp=BinaryTools.unpack('int16', x, 0x15),
                    odometer=BinaryTools.unpack('uint32', x, 0x17))
        }

    @classmethod
    def battery_contactor_closed(cls, x):
        return {
            'event': 'Battery module {module:02} contactor closed'.format(
                module=BinaryTools.unpack('uint8', x, 0x0))
        }


    @classmethod
    def type_from_block(cls, unescaped_block):
        return BinaryTools.unpack('uint8', unescaped_block, 0x00)

    @classmethod
    def get_message_type_description(cls, message_type):
        """Get descriptive name for log entry message types based on log_structure.md"""
        descriptions = {
            0x00: "Board Status",
            0x01: "Board Status", 
            0x02: "High Throttle Disable",
            0x03: "BMS Discharge Level",
            0x04: "BMS Charge Full",
            0x05: "BMS Unknown Type 5",
            0x06: "BMS Discharge Low", 
            0x08: "BMS System State",
            0x09: "Key State",
            0x0b: "BMS SOC Adjusted for Voltage",
            0x0d: "BMS Current Sensor Zeroed",
            0x0e: "BMS Unknown Type 14",
            0x10: "BMS Hibernate State",
            0x11: "BMS Chassis Isolation Fault",
            0x12: "BMS Reflash",
            0x13: "BMS CAN Node ID Changed",
            0x15: "BMS Contactor State",
            0x16: "BMS Discharge Cutback",
            0x18: "BMS Contactor Drive",
            0x1c: "MBB Unknown Type 28",
            0x1e: "MBB Unknown Type 30",
            0x1f: "MBB Unknown Type 31",
            0x20: "MBB Unknown Type 32",
            0x26: "MBB Unknown Type 38",
            0x28: "Battery CAN Link Up",
            0x29: "Battery CAN Link Down",
            0x2a: "Sevcon CAN Link Up",
            0x2b: "Sevcon CAN Link Down",
            0x2c: "Riding Status",
            0x2d: "Charging Status", 
            0x2f: "Sevcon Status",
            0x30: "Charger Status",
            0x31: "MBB BMS Isolation Fault",
            0x33: "Battery Module Status",
            0x34: "Power State",
            0x35: "MBB Unknown Type 53",
            0x36: "Sevcon Power State",
            0x37: "MBB BT RX Buffer Overflow",
            0x38: "Bluetooth State",
            0x39: "Battery Discharge Current Limited",
            0x3a: "Low Chassis Isolation",
            0x3b: "Precharge Decay Too Steep",
            0x3c: "Disarmed Status",
            0x3d: "Battery Module Contactor Closed",
            0x3e: "Cell Voltages",
            0xfd: "Debug String"
        }
        return descriptions.get(message_type, f"Unknown Type {message_type}")

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
            ascii_text = x.decode('ascii').rstrip('\x00')
            if ascii_text.isprintable() and len(ascii_text) > 1:
                conditions += f' [ASCII: "{ascii_text}"]'
        except:
            pass
        
        return {
            'event': description,
            'conditions': conditions
        }

    @classmethod
    def parse_entry(cls, log_data, address, unhandled, logger, timezone_offset=None):
        """
        Parse an individual entry from a LogFile into a human readable form
        """
        try:
            header = log_data[address]
        # IndexError: bytearray index out of range
        except IndexError:
            logger.warn("IndexError log_data[%r]: forcing header_bad", address)
            header = 0
        # correct header offset as needed to prevent errors
        header_bad = header != 0xb2
        while header_bad:
            address += 1
            try:
                header = log_data[address]
            except IndexError:
                # IndexError: bytearray index out of range
                logger.warn("IndexError log_data[%r]: forcing header_bad", address)
                header = 0
                header_bad = True
                break
            header_bad = header != 0xb2
        try:
            length = log_data[address + 1]
        # IndexError: bytearray index out of range
        except IndexError:
            length = 0

        unescaped_block = BinaryTools.unescape_block(log_data[address + 0x2:address + length])

        message_type = cls.type_from_block(unescaped_block)
        message = unescaped_block[0x05:]

        parsers = {
            # Unknown entry types to be added when defined: type, length, source, example
            0x01: cls.board_status,
            # 0x02: unknown, 2, 6350_MBB_2016-04-12, 0x02 0x2e 0x11 ???
            0x03: cls.bms_discharge_level,
            0x04: cls.bms_charge_full,
            # 0x05: unknown, 17, 6890_BMS0_2016-07-03, 0x05 0x34 0x0b 0xe0 0x0c 0x35 0x2a 0x89 0x71
            # 0xb5 0x01 0x00 0xa5 0x62 0x01 0x00 0x20 0x90 ???
            0x06: cls.bms_discharge_low,
            0x08: cls.bms_system_state,
            0x09: cls.key_state,
            0x0b: cls.bms_soc_adj_voltage,
            0x0d: cls.bms_curr_sens_zero,
            # 0x0e: unknown, 3, 6350_BMS0_2017-01-30 0x0e 0x05 0x00 0xff ???
            0x10: cls.bms_state,
            0x11: cls.bms_isolation_fault,
            0x12: cls.bms_reflash,
            0x13: cls.bms_change_can_id,
            0x15: cls.bms_contactor_state,
            0x16: cls.bms_discharge_cut,
            0x18: cls.bms_contactor_drive,
            # 0x1c: unknown, 8, 3455_MBB_2016-09-11, 0x1c 0xdf 0x56 0x01 0x00 0x00 0x00 0x30 0x02
            # ???
            # 0x1e: unknown, 4, 6472_MBB_2016-12-12, 0x1e 0x32 0x00 0x06 0x23 ???
            # 0x1f: unknown, 4, 5078_MBB_2017-01-20, 0x1f 0x00 0x00 0x08 0x43 ???
            # 0x20: unknown, 3, 6472_MBB_2016-12-12, 0x20 0x02 0x32 0x00 ???
            # 0x26: unknown, 6, 3455_MBB_2016-09-11, 0x26 0x72 0x00 0x40 0x00 0x80 0x00 ???
            0x28: cls.battery_can_link_up,
            0x29: cls.battery_can_link_down,
            0x2a: cls.sevcon_can_link_up,
            0x2b: cls.sevcon_can_link_down,
            0x2c: cls.run_status,
            0x2d: cls.charging_status,
            0x2f: cls.sevcon_status,
            0x30: cls.charger_status,
            # 0x31: unknown, 1, 6350_MBB_2016-04-12, 0x31 0x00 ???
            0x33: cls.battery_status,
            0x34: cls.power_state,
            # 0x35: unknown, 5, 6472_MBB_2016-12-12, 0x35 0x00 0x46 0x01 0xcb 0xff ???
            0x36: cls.sevcon_power_state,
            # 0x37: unknown, 0, 3558_MBB_2016-12-25, 0x37  ???
            0x38: cls.show_bluetooth_state,
            0x39: cls.battery_discharge_current_limited,
            0x3a: cls.low_chassis_isolation,
            0x3b: cls.precharge_decay_too_steep,
            0x3c: cls.disarmed_status,
            0x3d: cls.battery_contactor_closed,
            0xfd: cls.debug_message
        }
        entry_parser = parsers.get(message_type)
        try:
            if entry_parser:
                entry = entry_parser(message)
            else:
                entry = cls.unhandled_entry_format(message_type, message)
        except Exception as e:
            entry = cls.unhandled_entry_format(message_type, message)
            entry['event'] = 'Exception caught: ' + entry['event']
            unhandled += 1

        entry['time'] = cls.timestamp_from_event(unescaped_block, timezone_offset=timezone_offset)
        
        # Apply improved message parsing and determine log level
        improved_event, improved_conditions, json_data, has_json_data = improve_message_parsing(
            entry.get('event', ''), entry.get('conditions', ''))
        entry['log_level'] = determine_log_level(improved_event, has_json_data)

        return length, entry, unhandled


class Gen3:
    entry_data_fencepost = b'\x00\xb2'
    Entry = namedtuple('Gen3EntryType', ['event', 'time', 'conditions', 'uninterpreted', 'log_level'])
    min_timestamp = datetime.strptime('2019-01-01', '%Y-%M-%d')
    max_timestamp = datetime.now() + timedelta(days=365)

    @classmethod
    def timestamp_is_valid(cls, event_timestamp: datetime):
        return cls.min_timestamp < event_timestamp < cls.max_timestamp

    @classmethod
    def payload_to_entry(cls, entry_payload: bytearray, hex_on_error=False, logger=None) -> Entry:
        timestamp_bytes = list(entry_payload[0:4])
        timestamp_int = int.from_bytes(timestamp_bytes, byteorder='big', signed=False)
        event_timestamp = datetime.fromtimestamp(timestamp_int)
        if not cls.timestamp_is_valid(event_timestamp) and logger:
            logger.warning('Timestamp out of normal range: {}'.format(
                event_timestamp.strftime(ZERO_TIME_FORMAT)))
        # event_counter = BinaryTools.unpack('int16', entry_payload, 4)
        payload_string = BinaryTools.unpack_str(entry_payload, 7, len(entry_payload) - 7).strip()
        event_message = payload_string
        event_conditions = ''
        conditions = OrderedDict()
        conditions_str = ''
        data_payload = None
        try:
            data_fencepost = entry_payload.index(cls.entry_data_fencepost)
            data_payload = entry_payload[data_fencepost + 2:]
        except ValueError:
            pass
        if len(payload_string) < 2:
            if hex_on_error:
                event_message = display_bytes_hex(entry_payload)
            else:
                pass
                # conditions_str = 'Payload: ' + display_bytes_hex(entry_payload)
        elif '. ' in payload_string:
            sentences = payload_string.split(sep='. ')
            event_conditions = sentences[-1]
            event_message = '. '.join(sentences[:-1]) if len(sentences) > 2 else sentences[0]
        elif payload_string.startswith('I_('):
            match = re.match(r'(I_)\((.*)\)(.*)', payload_string)
            if match:
                event_message = 'Current'
                key_prefix = match.group(1)
                event_conditions = match.group(2)
                value_suffix = match.group(3)
                kv_parts = event_conditions.split(', ')
                for list_part in kv_parts:
                    if ': ' in list_part:
                        [k, v] = list_part.split(': ', maxsplit=1)
                        conditions[key_prefix + k] = v + value_suffix
                event_conditions = ''
        elif ': ' in payload_string:
            [event_message, event_conditions] = payload_string.split(': ', maxsplit=1)
        elif ' = ' in payload_string:
            [event_message, event_conditions] = payload_string.split(' = ', maxsplit=1)
        elif ' from ' in payload_string and ' to ' in payload_string:
            [event_message, event_conditions] = payload_string.split(' from ', maxsplit=1)
            match = re.match(r'(.*) to (.*)', event_conditions)
            if match:
                conditions['from'] = match.group(1)
                conditions['to'] = match.group(2)
                event_conditions = ''
        else:
            match = re.match(r'([^()]+) \(([^()]+)\)', payload_string)
            if match:
                event_message = match.group(1)
                event_conditions = match.group(2)
        if event_conditions.startswith('V_('):
            match = re.match(r'(V_)\((.*)\)(.*)', event_conditions)
            if match:
                key_prefix = match.group(1)
                event_conditions = match.group(2)
                value_suffix = match.group(3).rstrip(',')
                kv_parts = event_conditions.split(', ')
                for list_part in kv_parts:
                    if ': ' in list_part:
                        [k, v] = list_part.split(': ', maxsplit=1)
                        conditions[key_prefix + k] = v + value_suffix
        elif 'Old: ' in event_conditions and 'New: ' in event_conditions:
            matches = re.search('Old: (0x[0-9a-fA-F]+) New: (0x[0-9a-fA-F]+)', event_conditions)
            if matches:
                old = matches.group(1)
                old_int = int(old, 16)
                old_bits = '{0:b}'.format(old_int)
                new = matches.group(2)
                new_int = int(new, 16)
                new_bits = '{0:b}'.format(new_int)
                if len(new_bits) != len(old_bits):
                    max_len = max(len(new_bits), len(old_bits))
                    bitwise_format = '{0:0' + str(max_len) + 'b}'
                    new_bits = bitwise_format.format(new_int)
                    old_bits = bitwise_format.format(old_int)
                conditions['old'] = old_bits
                conditions['new'] = new_bits
        elif ', ' in event_conditions:
            list_parts = [x.strip() for x in event_conditions.split(', ')]
            for list_part in list_parts:
                if ': ' in list_part:
                    [k, v] = list_part.split(': ', maxsplit=1)
                else:
                    list_part_words = list_part.split(' ')
                    if len(list_part_words) == 2:
                        [k, v] = list_part_words
                    else:
                        k = list_part
                        v = ''
                conditions[k] = v
        if len(conditions) > 0:
            for k, v in conditions.items():
                if conditions_str:
                    conditions_str += ', '
                conditions_str += (k + ': ' + v) if k and v else k or v
        elif event_conditions:
            conditions_str = event_conditions
        # Apply improved message parsing and determine log level
        improved_event, improved_conditions, json_data, has_json_data = improve_message_parsing(event_message, conditions_str)
        log_level = determine_log_level(improved_event, has_json_data)
        
        return cls.Entry(event_message, event_timestamp, conditions_str,
                         display_bytes_hex(data_payload) if data_payload else '', log_level)


REV0 = 0
REV1 = 1
REV2 = 2
REV3 = 3  # Ring buffer format (2024+ firmware)


class LogData(object):
    """
    :type log_version: int
    :type header_info: Dict[str, str]
    :type entries_count: Optional[int]
    :type entries: List[str]
    :type timezone_offset: int
    """

    def __init__(self, log_file: LogFile, timezone_offset=None):
        self.log_file = log_file
        self.timezone_offset = timezone_offset
        self.log_version, self.header_info = self.get_version_and_header(log_file)
        self.entries_count, self.entries = self.get_entries_and_counts(log_file)

    def get_version_and_header(self, log: LogFile):
        logger = logger_for_input(self.log_file.file_path)
        sys_info = OrderedDict()
        log_version = REV0
        if len(sys_info) == 0 and (self.log_file.is_mbb() or self.log_file.is_unknown()):
            # Check for ring buffer format (2024+ firmware) - starts with log entries
            if log.raw()[0] == 0xb2 or (len(log.raw()) == 0x40000 and log.index_of_sequence(b'\xa1\xa1\xa1\xa1')):
                # Ring buffer format detected
                log_version = REV3  # New revision for ring buffer format
                filename_vin = self.log_file.get_filename_vin()
                sys_info['VIN'] = filename_vin if filename_vin else 'Unknown'
                
                # Look for serial number - it's located 0x302 bytes after first run date header
                serial_found = False
                first_run_idx = log.index_of_sequence(b'\xa1\xa1\xa1\xa1')
                if first_run_idx:
                    serial_offset = first_run_idx + 0x302
                    try:
                        if serial_offset + 15 < len(log.raw()):
                            potential_serial = log.unpack_str(serial_offset, count=15).strip('\x00')
                            if potential_serial and len(potential_serial) >= 8 and potential_serial.isalnum():
                                sys_info['Serial number'] = potential_serial
                                serial_found = True
                    except:
                        pass
                
                # Fallback: search near section headers if pattern-based approach didn't work  
                if not serial_found:
                    for search_offset in [0x3bd10, 0x3bd00, 0x3bd20]:
                        try:
                            if search_offset + 15 < len(log.raw()):
                                potential_serial = log.unpack_str(search_offset, count=15).strip('\x00')
                                if potential_serial and len(potential_serial) >= 8 and potential_serial.isalnum():
                                    sys_info['Serial number'] = potential_serial
                                    serial_found = True
                                    break
                        except:
                            pass
                        
                if not serial_found:
                    sys_info['Serial number'] = 'Unknown'
                
                # Look for first run date
                first_run_idx = log.index_of_sequence(b'\xa1\xa1\xa1\xa1')
                if first_run_idx:
                    try:
                        sys_info['Initial date'] = log.unpack_str(first_run_idx + 4, count=20).strip('\x00')
                    except:
                        sys_info['Initial date'] = 'Unknown'
                else:
                    sys_info['Initial date'] = 'Unknown'
                    
                sys_info['Model'] = 'Unknown'  # Model not found in ring buffer format
                sys_info['Firmware rev.'] = 'Unknown'
                sys_info['Board rev.'] = 'Unknown'
                
            else:
                # Legacy format - use original logic
                vin_v0 = log.unpack_str(0x240, count=17)  # v0 (Gen2)
                vin_v1 = log.unpack_str(0x252, count=17)  # v1 (Gen2 2019+)
                vin_v2 = log.unpack_str(0x029, count=17, encoding='latin_1')  # v2 (Gen3)
                if is_vin(vin_v0):
                    log_version = REV0
                    sys_info['Serial number'] = log.unpack_str(0x200, count=21)
                    sys_info['VIN'] = vin_v0
                    sys_info['Firmware rev.'] = log.unpack('uint16', 0x27b)
                    sys_info['Board rev.'] = log.unpack('uint16', 0x27d)
                    model_offset = 0x27f
                elif is_vin(vin_v1):
                    log_version = REV1
                    sys_info['Serial number'] = log.unpack_str(0x210, count=13)
                    sys_info['VIN'] = vin_v1
                    sys_info['Firmware rev.'] = log.unpack('uint16', 0x266)
                    sys_info['Board rev.'] = log.unpack('uint16', 0x268)  # TODO confirm Board rev.
                    model_offset = 0x26B
                elif is_vin(vin_v2):
                    log_version = REV2
                    sys_info['Serial number'] = log.unpack_str(0x03C, count=13)
                    sys_info['VIN'] = vin_v2
                    sys_info['Firmware rev.'] = log.unpack_str(0x06b, count=7)
                    sys_info['Board rev.'] = log.unpack_str(0x05C, count=8)
                    model_offset = 0x019
                else:
                    logger.warning("Unknown Log Format")
                    sys_info['VIN'] = vin_v0
                    model_offset = 0x27f
                    
                filename_vin = self.log_file.get_filename_vin()
                if 'VIN' not in sys_info or not BinaryTools.is_printable(sys_info['VIN']):
                    logger.warning("VIN unreadable: %s", sys_info['VIN'])
                elif sys_info['VIN'] != filename_vin:
                    logger.warning("VIN mismatch: header:%s filename:%s", sys_info['VIN'],
                                   filename_vin)
                sys_info['Model'] = log.unpack_str(model_offset, count=3)
                sys_info['Initial date'] = log.unpack_str(0x2a, count=20)
        if len(sys_info) == 0 and (self.log_file.is_bms() or self.log_file.is_unknown()):
            # Check for two log formats:
            log_version_code = log.unpack('uint8', 0x4)
            if log_version_code == 0xb6:
                log_version = REV0
            elif log_version_code == 0xde:
                log_version = REV1
            elif log_version_code == 0x79:
                log_version = REV2
            else:
                logger.warning("Unknown Log Format: %s", log_version_code)
            sys_info['Initial date'] = log.unpack_str(0x12, count=20)
            if log_version == REV0:
                sys_info['BMS serial number'] = log.unpack_str(0x300, count=21)
                sys_info['Pack serial number'] = log.unpack_str(0x320, count=8)
            elif log_version == REV1:
                # TODO identify BMS serial number
                sys_info['Pack serial number'] = log.unpack_str(0x331, count=8)
            elif log_version == REV2:
                sys_info['BMS serial number'] = log.unpack_str(0x038, count=13)
                sys_info['Pack serial number'] = log.unpack_str(0x06c, count=7)
        elif self.log_file.is_unknown():
            sys_info['System info'] = 'unknown'
        return log_version, sys_info

    def get_entries_and_counts(self, log: LogFile):
        logger = logger_for_input(log.file_path)
        raw_log = log.raw()
        if self.log_version == REV3:
            # Ring buffer format - entries start at beginning of file
            entries_header_idx = log.index_of_sequence(b'\xa2\xa2\xa2\xa2')
            if entries_header_idx is not None:
                entries_end = log.unpack('uint32', 0x4, offset=entries_header_idx)
                entries_start = log.unpack('uint32', 0x8, offset=entries_header_idx)
                claimed_entries_count = log.unpack('uint32', 0xc, offset=entries_header_idx)
                logger.info('Event log header found: start=0x%x, end=0x%x, count=%d', 
                           entries_start, entries_end, claimed_entries_count)
                
                # For ring buffer, entries start at 0x0000 and wrap around
                if entries_start >= entries_end:
                    event_log = raw_log[entries_start:] + raw_log[0:entries_end]
                else:
                    event_log = raw_log[entries_start:entries_end]
            else:
                # Fallback: scan entire file for entries
                logger.warning("No event log header found, scanning entire file")
                event_log = raw_log
                claimed_entries_count = 0
                
            entries_count = event_log.count(b'\xb2')
            logger.info('%d entries found (%d claimed)', entries_count, claimed_entries_count)
            
        elif self.log_version < REV2:
            # handle missing header index
            entries_header_idx = log.index_of_sequence(b'\xa2\xa2\xa2\xa2')
            if entries_header_idx is not None:
                entries_end = log.unpack('uint32', 0x4, offset=entries_header_idx)
                entries_start = log.unpack('uint32', 0x8, offset=entries_header_idx)
                claimed_entries_count = log.unpack('uint32', 0xc, offset=entries_header_idx)
                entries_data_begin = entries_header_idx + 0x10
            else:
                entries_end = len(raw_log)
                entries_start = log.index_of_sequence(b'\xb2')
                entries_data_begin = entries_start
                claimed_entries_count = 0

            # Handle data wrapping across the upper bound of the ring buffer
            if entries_start >= entries_end:
                event_log = raw_log[entries_start:] + \
                            raw_log[entries_data_begin:entries_end]
            else:
                event_log = raw_log[entries_start:entries_end]

            # count entry headers
            entries_count = event_log.count(b'\xb2')

            logger.info('%d entries found (%d claimed)', entries_count, claimed_entries_count)
        elif self.log_version == REV2:
            entries_count, event_log = self.get_gen3_entries(log, raw_log)

        return entries_count, event_log

    def get_gen3_entries(self, log, raw_log):
        self.gen3_fencepost_byte0 = raw_log[0x0a]  # before log_type
        self.gen3_fencepost_byte2 = raw_log[0x0c]  # before log_type
        first_fencepost_re = re.compile(
            bytes([self.gen3_fencepost_byte0, ord('.'), self.gen3_fencepost_byte2]))
        match = re.search(first_fencepost_re, raw_log)
        if not match:
            raise ValueError()
        first_fencepost_value = match.group(0)[1]
        # chrg_fencepost = b'\xff\xff' + bytes('CHRG', encoding='utf8')
        # chrg_indexes = log.indexes_of_sequence(chrg_fencepost)
        # another_fencepost = b'\x80\x00\xa2\xa2\x01\x00'
        entries_end = len(raw_log)
        entries_count = 0
        event_log = []
        event_start = match.start(0)
        current_fencepost_value = first_fencepost_value
        current_fencepost = self.event_fencepost(current_fencepost_value)
        while event_start is not None and event_start < entries_end:
            next_event_start = log.index_of_sequence(current_fencepost, start=event_start + 1)
            if next_event_start is not None:
                event_start = next_event_start
            next_fencepost = self.next_event_fencepost(current_fencepost)
            event_end = log.index_of_sequence(next_fencepost, start=event_start + 1)
            while event_end is None or event_end - event_start > 256:
                next_fencepost = self.next_event_fencepost(next_fencepost)
                event_end = log.index_of_sequence(next_fencepost, start=event_start + 1)
                if next_fencepost == current_fencepost:
                    break
            event_payload = raw_log[event_start - 4:event_end - 4 if event_end else event_end]
            event_log.append(event_payload)
            entries_count += 1
            current_fencepost = next_fencepost
            if event_end is None or event_end >= entries_end:
                break
        return entries_count, event_log

    def event_fencepost(self, value):
        return bytes([self.gen3_fencepost_byte0, value, self.gen3_fencepost_byte2])

    def next_event_fencepost(self, previous_event_fencepost):
        previous_value = 0
        if isinstance(previous_event_fencepost, int):
            previous_value = previous_event_fencepost
        elif isinstance(previous_event_fencepost, bytes):
            previous_value = previous_event_fencepost[1]
        next_value = previous_value + 1
        # Wraparound that avoids certain other common byte sequences:
        if next_value == 0xfe:
            next_value = 0xff
        elif next_value == 0x00:
            next_value = 0x01
        if next_value > 0xff:
            next_value = 0x01
        return self.event_fencepost(next_value)

    header_divider = \
        '+--------+----------------------+--------------------------+----------------------------------\n'

    def has_official_output_reference(self):
        return self.log_version < REV2 or self.log_version == REV3

    def emit_tabular_decoding(self, output_file: str, out_format='tsv', logger=None):
        file_suffix = '.tsv' if out_format == 'tsv' else '.csv'
        tabular_output_file = output_file.replace('.txt', file_suffix, 1)
        field_sep = '\t' if out_format == 'tsv' else CSV_DELIMITER 
        record_sep = os.linesep
        headers = ['entry', 'timestamp', 'log_level', 'message', 'conditions', 'uninterpreted']
        
        if not logger:
            logger = logger_for_input(self.log_file.file_path)
            
        with open(tabular_output_file, 'w', encoding='utf-8') as output:
            def write_row(values):
                output.write(field_sep.join(values) + record_sep)

            write_row(headers)
            
            if self.log_version < REV2 or self.log_version == REV3:
                # Handle REV0/REV1/REV3 formats - collect and sort entries
                collected_entries = []
                read_pos = 0
                for entry_num in range(self.entries_count):
                    (length, entry_payload, unhandled) = Gen2.parse_entry(self.entries, read_pos,
                                                                          0,  # unhandled counter
                                                                          timezone_offset=self.timezone_offset,
                                                                          logger=logger)
                    
                    # Extract timestamp for sorting
                    time_str = entry_payload.get('time', '0')
                    if time_str.isdigit():
                        sort_timestamp = int(time_str)
                    else:
                        try:
                            from datetime import datetime
                            try:
                                parsed_time = datetime.strptime(time_str, ZERO_TIME_FORMAT)
                            except ValueError:
                                parsed_time = datetime.strptime(time_str, '%m/%d/%Y %H:%M:%S')
                            if parsed_time.year > 2030:
                                sort_timestamp = 0
                            else:
                                sort_timestamp = parsed_time.timestamp()
                        except:
                            sort_timestamp = 0
                    
                    collected_entries.append((sort_timestamp, entry_payload, entry_num))
                    read_pos += length
                
                # Apply timestamp interpolation before sorting
                collected_entries = Gen2.interpolate_missing_timestamps(collected_entries, logger)
                
                # Sort by timestamp (newest first)
                collected_entries.sort(key=lambda x: x[0], reverse=True)
                
                # Write sorted entries
                for line_num, (sort_timestamp, entry_payload, original_entry_num) in enumerate(collected_entries):
                    message = entry_payload.get('event', '')
                    conditions = entry_payload.get('conditions', '')
                    log_level = entry_payload.get('log_level', 'INFO')  # Use log_level from entry data
                    
                    # Apply improved message parsing
                    improved_message, improved_conditions, json_data, has_json_data = improve_message_parsing(message, conditions)
                    
                    row_values = [
                        str(original_entry_num + 1),  # Keep original entry number
                        entry_payload.get('time', ''),
                        log_level,
                        improved_message,
                        improved_conditions,
                        ''  # uninterpreted field for compatibility
                    ]
                    write_row([print_value_tabular(x) for x in row_values])
            else:
                # Handle REV2 (Gen3) format
                for line, entry_payload in enumerate(self.entries):
                    entry = Gen3.payload_to_entry(entry_payload, logger=logger)
                    
                    # Apply improved message parsing
                    improved_event, improved_conditions, json_data, has_json_data = improve_message_parsing(entry.event, entry.conditions)
                    log_level = entry.log_level  # Use log_level from entry data
                    
                    row_values = [line + 1, entry.time.isoformat(), log_level,
                                  improved_event, improved_conditions, entry.uninterpreted]
                    write_row([print_value_tabular(x) for x in row_values])
                    
        logger_for_input(self.log_file.file_path).info('Saved to %s', tabular_output_file)

    def emit_json_decoding(self, output_file: str, logger=None):
        """Generate JSON output with structured log entries"""
        json_output_file = output_file.replace('.txt', '.json', 1)
        
        if not logger:
            logger = logger_for_input(self.log_file.file_path)
        
        # Prepare the JSON structure
        json_output = {
            'metadata': {
                'source_file': self.log_file.file_path,
                'log_type': 'MBB' if 'MBB' in self.log_file.file_path or 'Mbb' in self.log_file.file_path else 'BMS',
                'parser_version': 'zero-log-parser',
                'generated_at': datetime.now().isoformat(),
                'timezone': f'UTC{self.timezone_offset/3600:+.1f}' if self.timezone_offset else 'UTC+0.0',
                'total_entries': len(self.entries) if hasattr(self, 'entries') else 0
            },
            'log_info': {
                'vin': getattr(self, 'vin', 'Unknown'),
                'serial_number': getattr(self, 'serial_number', 'Unknown'),
                'initial_date': getattr(self, 'initial_date', 'Unknown'),
                'model': getattr(self, 'model', 'Unknown'),
                'firmware_rev': getattr(self, 'firmware_rev', 'Unknown'),
                'board_rev': getattr(self, 'board_rev', 'Unknown')
            },
            'entries': []
        }
        
        # Process entries based on log version
        if self.log_version < REV2 or self.log_version == REV3:
            # Handle REV0/REV1/REV3 formats - collect and sort entries
            collected_entries = []
            read_pos = 0
            
            if hasattr(self, 'entries_count'):
                for entry_num in range(self.entries_count):
                    try:
                        (length, entry_payload, unhandled) = Gen2.parse_entry(self.entries, read_pos,
                                                                              0,  # unhandled counter
                                                                              timezone_offset=self.timezone_offset,
                                                                              logger=logger)
                        
                        # Extract timestamp for sorting
                        time_str = entry_payload.get('time', '0')
                        if time_str.isdigit():
                            sort_timestamp = int(time_str)
                        else:
                            try:
                                try:
                                    parsed_time = datetime.strptime(time_str, ZERO_TIME_FORMAT)
                                except ValueError:
                                    parsed_time = datetime.strptime(time_str, '%m/%d/%Y %H:%M:%S')
                                if parsed_time.year > 2030:
                                    sort_timestamp = 0
                                else:
                                    sort_timestamp = parsed_time.timestamp()
                            except:
                                sort_timestamp = 0
                        
                        collected_entries.append((sort_timestamp, entry_payload, entry_num))
                        read_pos += length
                    except Exception as e:
                        logger.warning(f'Error parsing entry {entry_num}: {e}')
                        break
                
                # Apply timestamp interpolation before sorting
                collected_entries = Gen2.interpolate_missing_timestamps(collected_entries, logger)
                
                # Sort by timestamp (newest first)
                collected_entries.sort(key=lambda x: x[0], reverse=True)
                
                # Process sorted entries
                for line_num, (sort_timestamp, entry_payload, original_entry_num) in enumerate(collected_entries):
                    message = entry_payload.get('event', '')
                    conditions = entry_payload.get('conditions', '')
                    log_level = entry_payload.get('log_level', 'INFO')
                    
                    # Apply improved message parsing
                    improved_message, improved_conditions, json_data, has_json_data = improve_message_parsing(message, conditions)
                    
                    # Create JSON entry
                    json_entry = {
                        'entry_number': original_entry_num + 1,
                        'timestamp': entry_payload.get('time', ''),
                        'sort_timestamp': sort_timestamp if sort_timestamp > 0 else None,
                        'log_level': log_level,
                        'event': improved_message,
                        'conditions': improved_conditions if improved_conditions else None,
                        'is_structured_data': has_json_data
                    }
                    
                    # If conditions contain JSON, parse it for structured access
                    if improved_conditions and improved_conditions.startswith('{'):
                        try:
                            json_entry['structured_data'] = json.loads(improved_conditions)
                            json_entry['conditions'] = None  # Remove redundant text version
                        except json.JSONDecodeError:
                            pass  # Keep as text if JSON parsing fails
                    
                    json_output['entries'].append(json_entry)
        else:
            # Handle REV2 (Gen3) format
            for line, entry_payload in enumerate(self.entries):
                try:
                    entry = Gen3.payload_to_entry(entry_payload, logger=logger)
                    
                    # Apply improved message parsing
                    improved_event, improved_conditions, json_data, has_json_data = improve_message_parsing(entry.event, entry.conditions)
                    log_level = entry.log_level
                    
                    # Create JSON entry
                    json_entry = {
                        'entry_number': line + 1,
                        'timestamp': entry.time.strftime(ZERO_TIME_FORMAT),
                        'sort_timestamp': entry.time.timestamp(),
                        'log_level': log_level,
                        'event': improved_event,
                        'conditions': improved_conditions if improved_conditions else None,
                        'uninterpreted': entry.uninterpreted if entry.uninterpreted else None,
                        'is_structured_data': has_json_data
                    }
                    
                    # If conditions contain JSON, parse it for structured access
                    if improved_conditions and improved_conditions.startswith('{'):
                        try:
                            json_entry['structured_data'] = json.loads(improved_conditions)
                            json_entry['conditions'] = None  # Remove redundant text version
                        except json.JSONDecodeError:
                            pass  # Keep as text if JSON parsing fails
                    
                    json_output['entries'].append(json_entry)
                except Exception as e:
                    logger.warning(f'Error processing entry {line}: {e}')
        
        # Update metadata with actual entry count
        json_output['metadata']['total_entries'] = len(json_output['entries'])
        
        # Write JSON output
        try:
            with open(json_output_file, 'w', encoding='utf-8') as f:
                json.dump(json_output, f, indent=2, ensure_ascii=False)
            logger.info('Saved to %s', json_output_file)
        except Exception as e:
            logger.error(f'Error writing JSON file {json_output_file}: {e}')
            raise

    @classmethod
    def output_line_number_field(cls, line: int):
        return ' {line:05d}'.format(line=line)

    @classmethod
    def output_time_field(cls, time: str):
        return '     {time:>19s}'.format(time=time)

    def emit_zero_compatible_decoding(self, output_file: str, logger=None):
        with codecs.open(output_file, 'wb', 'utf-8-sig') as f:
            logger = logger_for_input(self.log_file.file_path)

            def write_line(text=None):
                f.write(text + '\n' if text else '\n')

            write_line('Zero ' + self.log_file.log_type + ' log')
            write_line()

            for k, v in self.header_info.items():
                write_line('{0:18} {1}'.format(k, v))
            
            # Add timezone information
            tz_hours = self.timezone_offset / 3600
            if tz_hours >= 0:
                tz_str = f'UTC+{tz_hours:.1f}'
            else:
                tz_str = f'UTC{tz_hours:.1f}'
            write_line('{0:18} {1}'.format('Timezone', tz_str))
            write_line()

            write_line('Printing {0} of {0} log entries..'.format(self.entries_count))
            write_line()
            write_line(' Entry    Time of Log            Level     Event                      Conditions')
            f.write(self.header_divider)

            unhandled = 0
            unknown_entries = 0
            unknown = []
            if self.log_version < REV2 or self.log_version == REV3:
                # First pass: collect all entries with their timestamps for sorting
                collected_entries = []
                read_pos = 0
                for entry_num in range(self.entries_count):
                    (length, entry_payload, unhandled) = Gen2.parse_entry(self.entries, read_pos,
                                                                          unhandled,
                                                                          timezone_offset=self.timezone_offset,
                                                                          logger=logger)
                    
                    # Extract timestamp for sorting (handle both string and numeric timestamps)
                    time_str = entry_payload.get('time', '0')
                    if time_str.isdigit():
                        # Numeric timestamp (invalid/zero timestamps) - assign very low priority
                        sort_timestamp = int(time_str)
                    else:
                        # Parse timestamp string to get sortable value
                        try:
                            from datetime import datetime
                            # Try new format first, then fall back to old format
                            try:
                                parsed_time = datetime.strptime(time_str, ZERO_TIME_FORMAT)
                            except ValueError:
                                # Fall back to old format for compatibility
                                parsed_time = datetime.strptime(time_str, '%m/%d/%Y %H:%M:%S')
                            # Filter out obviously invalid future dates (beyond 2030)
                            if parsed_time.year > 2030:
                                sort_timestamp = 0  # Treat as invalid
                            else:
                                sort_timestamp = parsed_time.timestamp()
                        except:
                            sort_timestamp = 0
                    
                    collected_entries.append((sort_timestamp, entry_payload, entry_num))
                    read_pos += length
                
                # Apply timestamp interpolation before sorting
                collected_entries = Gen2.interpolate_missing_timestamps(collected_entries, logger)
                
                # Sort entries by timestamp (newest first - descending order)
                collected_entries.sort(key=lambda x: x[0], reverse=True)
                
                # Second pass: output sorted entries
                for line_num, (sort_timestamp, entry_payload, original_entry_num) in enumerate(collected_entries):
                    entry_payload['line'] = original_entry_num + 1  # Keep original entry number

                    conditions = entry_payload.get('conditions')
                    message = entry_payload.get('event', '')
                    log_level = entry_payload.get('log_level', 'INFO')  # Use log_level from entry data
                    
                    # Apply improved message parsing
                    improved_message, improved_conditions, json_data, has_json_data = improve_message_parsing(message, conditions)
                    
                    line_prefix = (self.output_line_number_field(entry_payload['line'])
                                   + self.output_time_field(entry_payload['time'])
                                   + f'  {log_level:8}')
                    
                    if improved_conditions:
                        if '???' in improved_conditions:
                            u = improved_conditions[0]
                            unknown_entries += 1
                            if u not in unknown:
                                unknown.append(u)
                            improved_conditions = '???'
                            write_line(
                                line_prefix + '   {message} {conditions}'.format(
                                    message=improved_message, conditions=improved_conditions))
                        else:
                            write_line(
                                line_prefix + '   {message:25}  {conditions}'.format(
                                    message=improved_message, conditions=improved_conditions))
                    else:
                        write_line(line_prefix + '   {message}'.format(message=improved_message))
            else:
                # Gen3 format (REV2) - collect entries and sort by timestamp
                collected_gen3_entries = []
                for line, entry_payload in enumerate(self.entries):
                    entry = Gen3.payload_to_entry(entry_payload, logger=logger)
                    collected_gen3_entries.append((entry.time.timestamp(), entry, line))
                
                # Sort by timestamp (newest first)
                collected_gen3_entries.sort(key=lambda x: x[0], reverse=True)
                
                # Output sorted entries
                for line_num, (timestamp, entry, original_line) in enumerate(collected_gen3_entries):
                    conditions = entry.conditions
                    log_level = entry.log_level  # Use log_level from entry data
                    
                    # Apply improved message parsing
                    improved_event, improved_conditions, json_data, has_json_data = improve_message_parsing(entry.event, conditions)
                    
                    line_prefix = (self.output_line_number_field(original_line + 1)  # Keep original entry number
                                   + self.output_time_field(entry.time.strftime(ZERO_TIME_FORMAT))
                                   + f'  {log_level:8}')
                    if improved_conditions:
                        output_line = line_prefix + '   {event:25}  ({conditions}) [{uninterpreted}]'.format(
                            event=improved_event,
                            conditions=improved_conditions,
                            uninterpreted=entry.uninterpreted)
                    else:
                        output_line = line_prefix + '   {event} [{uninterpreted}]'.format(
                            event=improved_event,
                            uninterpreted=entry.uninterpreted)
                    if re.match(r'\s+\[', output_line):
                        raise ValueError()
                    write_line(output_line)
            write_line()
        if unhandled > 0:
            logger.info('%d exceptions in parser', unhandled)
        if unknown:
            logger.info('%d unknown entries of types %s',
                        unknown_entries,
                        ', '.join(hex(ord(x)) for x in unknown))

        logger.info('Saved to %s', output_file)


def parse_log(bin_file: str, output_file: str, utc_offset_hours=None, verbose=False, logger=None, output_format='txt'):
    """
    Parse a Zero binary log file into a human readable text file
    """
    if not logger:
        logger = console_logger(bin_file, verbose=verbose)
    logger.info('Parsing %s', bin_file)

    if isinstance(utc_offset_hours, int):
        timezone_offset = utc_offset_hours * 60 * 60
    elif utc_offset_hours is not None:
        try:
            timezone_offset = float(utc_offset_hours) * 60 * 60
        except (ValueError, TypeError):
            timezone_offset = get_local_timezone_offset()
    else:
        # Use local system timezone as default
        timezone_offset = get_local_timezone_offset()

    log = LogFile(bin_file)
    log_data = LogData(log, timezone_offset=timezone_offset)

    if output_format.lower() in ['csv', 'tsv']:
        # Generate CSV/TSV output
        log_data.emit_tabular_decoding(output_file, out_format=output_format.lower())
    elif output_format.lower() == 'json':
        # Generate JSON output
        log_data.emit_json_decoding(output_file)
    elif output_format.lower() == 'txt':
        # Generate standard text output
        if log_data.has_official_output_reference():
            log_data.emit_zero_compatible_decoding(output_file)
        else:
            log_data.emit_tabular_decoding(output_file)
            log_data.emit_zero_compatible_decoding(output_file)
    else:
        # Default to text format for unknown formats
        if log_data.has_official_output_reference():
            log_data.emit_zero_compatible_decoding(output_file)
        else:
            log_data.emit_tabular_decoding(output_file)
            log_data.emit_zero_compatible_decoding(output_file)


def default_parsed_output_for(bin_file_path: str):
    return os.path.splitext(bin_file_path)[0] + '.txt'


def is_log_file_path(file_path: str):
    return file_path.endswith('.bin')


def console_logger(name: str, verbose=False):
    log_level = logging.NOTSET if verbose else logging.INFO
    logger = logging.Logger(name, level=log_level)
    logger_formatter = logging.Formatter('%(asctime)s [%(name)s] [%(levelname)s] %(message)s')
    logger_handler = logging.StreamHandler()
    logger_handler.setFormatter(logger_formatter)
    logger.addHandler(logger_handler)
    return logger


def logger_for_input(bin_file):
    return logging.getLogger(bin_file)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('bin_file', help='Zero *.bin log to decode')
    parser.add_argument('--timezone', help='Timezone offset in hours from UTC (e.g., -8 for PST, +1 for CET). Defaults to local system timezone.')
    parser.add_argument('-o', '--output', help='decoded log filename')
    parser.add_argument('-f', '--format', choices=['txt', 'csv', 'tsv', 'json'], default='txt', 
                       help='Output format: txt (default), csv, tsv, or json')
    parser.add_argument('-v', '--verbose', help='additional logging')
    args = parser.parse_args()
    log_file = args.bin_file
    output_file = args.output or default_parsed_output_for(args.bin_file)
    tz_code = args.timezone
    output_format = args.format
    parse_log(log_file, output_file, utc_offset_hours=tz_code, verbose=args.verbose, output_format=output_format)


if __name__ == '__main__':
    main()
