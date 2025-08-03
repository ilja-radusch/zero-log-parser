"""Core parsing logic for Zero log files."""

import codecs
import json
import logging
import os
import re
import struct
from collections import OrderedDict, namedtuple
from datetime import datetime, timedelta
from math import trunc
from time import gmtime, localtime, strftime
from typing import List, Dict, Any, Optional, Union

from .parser import BinaryTools
from .message_parser import improve_message_parsing, determine_log_level
from .utils import get_local_timezone_offset, logger_for_input, ZERO_TIME_FORMAT, display_bytes_hex, hex_of_value, is_vin


# Constants from original
REV0 = 0
REV1 = 1 
REV2 = 2
REV3 = 3


class LogFile:
    """
    :type file_path: str
    :type raw_data: bytearray
    """
    log_type_mbb = 'MBB'
    log_type_bms = 'BMS'

    def __init__(self, file_path):
        self.file_path = file_path
        with open(file_path, 'rb') as f:
            self.raw_data = bytearray(f.read())
        
        log_type = None
        if self.log_type_mbb.lower() in self.file_path.lower():
            log_type = self.log_type_mbb
        elif self.log_type_bms.lower() in self.file_path.lower():
            log_type = self.log_type_bms
        if log_type not in [self.log_type_mbb, self.log_type_bms]:
            log_type = None
        
        self.log_type = log_type

    def raw(self):
        return self.raw_data

    def is_mbb(self):
        return self.log_type == self.log_type_mbb

    def is_bms(self):
        return self.log_type == self.log_type_bms

    def is_unknown(self):
        return self.log_type is None

    def unpack_str(self, address, count=1, offset=0, encoding='utf-8'):
        return BinaryTools.unpack_str(self.raw_data, address, count, offset, encoding)

    def index_of_sequence(self, sequence):
        try:
            return self.raw_data.index(sequence)
        except ValueError:
            return None

    def get_filename_vin(self):
        """Extract VIN from filename"""
        filename = os.path.basename(self.file_path)
        # Look for 17-character VIN starting with 538
        if filename.startswith('538') and len(filename) >= 17:
            potential_vin = filename[:17]
            if is_vin(potential_vin):
                return potential_vin
        return None


class LogData(object):
    """
    :type log_version: int
    :type header_info: Dict[str, str]
    :type entries_count: Optional[int]
    :type entries: List[str]
    :type timezone_offset: float
    """

    def __init__(self, log_input: Union[str, bytes, bytearray], timezone_offset=None):
        if isinstance(log_input, str):
            # It's a file path
            self.log_file = LogFile(log_input)
        else:
            # Create temporary file for raw data
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as f:
                f.write(log_input)
                temp_path = f.name
            self.log_file = LogFile(temp_path)
            
        if timezone_offset is None:
            self.timezone_offset = get_local_timezone_offset() / 3600.0
        else:
            self.timezone_offset = timezone_offset
            
        self.log_version, self.header_info = self.get_version_and_header(self.log_file)
        self.entries_count, self.entries = self.get_entries_and_counts(self.log_file)

    def get_version_and_header(self, log: LogFile):
        logger = logger_for_input(self.log_file.file_path) 
        sys_info = OrderedDict()
        log_version = REV0
        
        # BMS log detection and parsing
        if log.is_bms():
            # Determine BMS log version based on version code
            log_version_code = BinaryTools.unpack('uint8', log.raw(), 0x4)
            if log_version_code == 0xb6:
                log_version = REV0
            elif log_version_code == 0xde:
                log_version = REV1
            elif log_version_code == 0x79:
                log_version = REV2
            else:
                logger.warning(f"Unknown BMS Log Format: 0x{log_version_code:02x}")
                log_version = REV2  # Default fallback
            
            # Try to extract VIN from filename
            filename_vin = log.get_filename_vin()
            if filename_vin:
                sys_info['VIN'] = filename_vin
            else:
                sys_info['VIN'] = 'Unknown'
            
            # Extract BMS-specific information based on version
            try:
                # Initial date is consistent across versions
                sys_info['Initial date'] = log.unpack_str(0x12, count=20).strip('\x00')
                
                if log_version == REV0:
                    sys_info['BMS serial number'] = log.unpack_str(0x300, count=21).strip('\x00')
                    sys_info['Pack serial number'] = log.unpack_str(0x320, count=8).strip('\x00')
                elif log_version == REV1:
                    # REV1 format - pack serial at different offset
                    sys_info['Pack serial number'] = log.unpack_str(0x331, count=8).strip('\x00')
                elif log_version == REV2:
                    sys_info['BMS serial number'] = log.unpack_str(0x038, count=13).strip('\x00')
                    sys_info['Pack serial number'] = log.unpack_str(0x06c, count=7).strip('\x00')
                    
            except Exception as e:
                logger.debug(f"Error extracting BMS header info: {e}")
                sys_info['Pack serial number'] = 'Unknown'
                sys_info['Initial date'] = 'Unknown'
                
        elif log.is_mbb() or log.is_unknown():
            # MBB log or unknown format
            # Check for ring buffer format (2024+ firmware)
            if len(log.raw()) > 0 and (log.raw()[0] == 0xb2 or (len(log.raw()) == 0x40000 and log.index_of_sequence(b'\xa1\xa1\xa1\xa1'))):
                # Ring buffer format detected
                log_version = REV3
                filename_vin = log.get_filename_vin()
                sys_info['VIN'] = filename_vin if filename_vin else 'Unknown'
                
                # Look for serial number
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
                
                if not serial_found:
                    sys_info['Serial number'] = 'Unknown'
                    
                sys_info['Initial date'] = 'Unknown'
                sys_info['Model'] = 'Unknown'
                sys_info['Firmware rev'] = 'Unknown'
                sys_info['Board rev'] = 'Unknown'
            else:
                # Legacy format
                log_version = REV2
                try:
                    sys_info['VIN'] = log.unpack_str(0x240, count=17).strip('\x00')
                    sys_info['Serial number'] = log.unpack_str(0x200, count=21).strip('\x00')
                except:
                    sys_info['VIN'] = 'Unknown'
                    sys_info['Serial number'] = 'Unknown'
                    
        return log_version, sys_info

    def get_entries_and_counts(self, log_file: LogFile):
        """Parse log entries and return count and entries list"""
        entries = []
        
        if log_file.is_bms():
            # BMS parsing logic based on log version
            entries_start = None
            entries_end = None
            entries_count = 0
            
            if self.log_version < REV2:
                # REV0/REV1: Look for entries header
                entries_header_idx = log_file.index_of_sequence(b'\xa2\xa2\xa2\xa2')
                if entries_header_idx is not None:
                    entries_end = BinaryTools.unpack('uint32', log_file.raw(), 0x4, offset=entries_header_idx)
                    entries_start = BinaryTools.unpack('uint32', log_file.raw(), 0x8, offset=entries_header_idx)
                    entries_count = BinaryTools.unpack('uint32', log_file.raw(), 0xc, offset=entries_header_idx)
                    entries_data_begin = entries_header_idx + 0x10
                else:
                    # Fallback if no header found
                    entries_start = 0x710
                    entries_end = len(log_file.raw())
                    entries_count = 0
            elif self.log_version == REV2:
                # REV2: Different parsing logic (implement if needed)
                entries_start = 0x710
                entries_end = len(log_file.raw())
                entries_count = 0
                
            # Extract event log data handling ring buffer wrap-around
            if entries_start is not None and entries_end is not None:
                if entries_start >= entries_end:
                    # Ring buffer wrap-around
                    event_log_data = log_file.raw()[entries_start:] + log_file.raw()[entries_data_begin:entries_end]
                else:
                    event_log_data = log_file.raw()[entries_start:entries_end]
            else:
                # Fallback: scan from 0x710
                event_log_data = log_file.raw()[0x710:]
                
            # Parse BMS entries from the event log data
            offset = 0
            entry_number = 1
            
            while offset < len(event_log_data) - 7:
                if event_log_data[offset] != 0xb2:
                    offset += 1
                    continue
                    
                try:
                    length = event_log_data[offset + 1]
                    if length < 7 or offset + length > len(event_log_data):
                        offset += 1
                        continue
                        
                    # Extract the entry block and unescape it
                    entry_block = bytearray(event_log_data[offset:offset + length])
                    unescaped_block = BinaryTools.unescape_block(entry_block)
                    
                    if len(unescaped_block) < 7:
                        offset += length
                        continue
                        
                    # Parse BMS entry
                    entry_data = self.parse_bms_entry(unescaped_block, entry_number)
                    if entry_data:
                        entries.append(entry_data)
                        entry_number += 1
                        
                    offset += length
                    
                except Exception as e:
                    offset += 1
                    continue
                    
        else:
            # MBB parsing logic
            offset = 0x10 if self.log_version != REV3 else 0
            entry_number = 1
            
            while offset < len(log_file.raw()) - 7:
                if log_file.raw()[offset] != 0xb2:
                    offset += 1
                    continue
                    
                try:
                    length = log_file.raw()[offset + 1]
                    if length < 7 or offset + length > len(log_file.raw()):
                        offset += 1
                        continue
                        
                    # Extract the entry block and unescape it (skip header and length bytes)
                    entry_block = bytearray(log_file.raw()[offset + 2:offset + length])
                    unescaped_block = BinaryTools.unescape_block(entry_block)
                    
                    if len(unescaped_block) < 7:
                        offset += length
                        continue
                        
                    # Parse MBB entry 
                    entry_data = self.parse_mbb_entry(unescaped_block, entry_number)
                    if entry_data:
                        entries.append(entry_data)
                        entry_number += 1
                        
                    offset += length
                    
                except Exception:
                    offset += 1
                    continue
        
        # Apply interpolation and sorting
        self.interpolate_missing_timestamps(entries)
        
        # Sort by timestamp (newest first) while preserving entry numbers
        entries.sort(key=lambda x: x.get('sort_timestamp', 0), reverse=True)
        
        return len(entries), entries

    def parse_bms_entry(self, unescaped_block: bytearray, entry_number: int) -> Optional[Dict]:
        """Parse a single BMS entry"""
        try:
            message_type = unescaped_block[2]
            
            # Extract timestamp
            timestamp_bytes = unescaped_block[3:7]
            timestamp_int = struct.unpack('<I', timestamp_bytes)[0]
            
            # Skip invalid timestamps
            if timestamp_int <= 0xfff or timestamp_int > 1893456000:
                return None
                
            # Apply timezone offset
            adjusted_timestamp = timestamp_int + (self.timezone_offset * 3600)
            timestamp_str = datetime.fromtimestamp(adjusted_timestamp).strftime(ZERO_TIME_FORMAT)
            
            # Extract message data
            message_data = unescaped_block[7:] if len(unescaped_block) > 7 else bytearray()
            
            # Parse based on message type
            entry = self.parse_bms_message_type(message_type, message_data)
            entry['entry_number'] = entry_number
            entry['time'] = timestamp_str
            entry['sort_timestamp'] = adjusted_timestamp
            
            # Apply message parsing improvements
            improved_event, improved_conditions, json_data, has_json_data = improve_message_parsing(
                entry['event'], entry.get('conditions', ''))
            
            if improved_event != entry['event']:
                entry['event'] = improved_event
            if improved_conditions != entry.get('conditions'):
                entry['conditions'] = improved_conditions
                
            entry['log_level'] = determine_log_level(entry['event'], has_json_data)
            
            return entry
            
        except Exception as e:
            return None

    def parse_bms_message_type(self, message_type: int, message_data: bytearray) -> Dict:
        """Parse BMS message based on type"""
        if message_type == 0x03:  # Discharge level
            return self.bms_discharge_level(message_data)
        elif message_type == 0x04:  # Charge full
            return self.bms_charge_full(message_data)
        elif message_type == 0x06:  # Discharge low
            return self.bms_discharge_low(message_data)
        elif message_type == 0x08:  # System status
            return self.bms_system_status(message_data)
        elif message_type == 0x0b:  # SOC adjusted
            return self.bms_soc_adjusted(message_data)
        elif message_type == 0x0d:  # Current sensor zeroed
            return self.bms_current_sensor_zeroed(message_data)
        elif message_type == 0x10:  # Hibernate
            return self.bms_hibernate(message_data)
        elif message_type == 0x12:  # Reflash
            return self.bms_reflash(message_data)
        elif message_type == 0x15:  # Contactor
            return self.bms_contactor(message_data)
        elif message_type == 0x16:  # Discharge cutback
            return self.bms_discharge_cutback(message_data)
        elif message_type == 0x18:  # Contactor drive
            return self.bms_contactor_drive(message_data)
        elif message_type == 0xfd:  # SOC data in ASCII format
            return self.bms_soc_data_ascii(message_data)
        else:
            # Unknown message type
            return {
                'event': f'Unknown Message Type 0x{message_type:02X}',
                'conditions': f'Raw data: {display_bytes_hex(message_data)}' if message_data else 'No additional data'
            }

    def bms_discharge_level(self, x: bytearray) -> Dict:
        """Parse BMS discharge level message"""
        if len(x) < 0x17:
            return {'event': 'Discharge level', 'conditions': 'Insufficient data'}
            
        return {
            'event': 'Discharge level',
            'conditions': '{AH:03.0f} AH, SOC:{SOC:3d}%, I:{I:3.0f}A, L:{L}, l:{l}, H:{H}, B:{B:03d}, PT:{PT:03d}C, BT:{BT:03d}C, PV:{PV:6d}, M:{M}'.format(
                AH=trunc(BinaryTools.unpack('uint32', x, 0x06) / 1000000.0),
                SOC=BinaryTools.unpack('uint8', x, 0x0a),
                I=trunc(BinaryTools.unpack('int32', x, 0x10) / 1000000.0),
                L=BinaryTools.unpack('uint16', x, 0x00),
                l=BinaryTools.unpack('uint16', x, 0x14),
                H=BinaryTools.unpack('uint16', x, 0x02),
                B=BinaryTools.unpack('uint16', x, 0x02) - BinaryTools.unpack('uint16', x, 0x00),
                PT=BinaryTools.unpack('uint8', x, 0x04),
                BT=BinaryTools.unpack('uint8', x, 0x05),
                PV=BinaryTools.unpack('uint32', x, 0x0b),
                M={0x01: 'Bike On', 0x02: 'Charge', 0x03: 'Idle'}.get(BinaryTools.unpack('uint8', x, 0x0f), 'Unknown')
            )
        }

    def bms_charge_full(self, x: bytearray) -> Dict:
        """Parse BMS charge full message"""
        if len(x) < 0x10:
            return {'event': 'Charged To Full', 'conditions': 'Insufficient data'}
            
        return {
            'event': 'Charged To Full',
            'conditions': '{AH:03.0f} AH, SOC:{SOC:3d}%,         L:{L},         H:{H}, B:{B:03d}, PT:{PT:03d}C, BT:{BT:03d}C, PV:{PV}'.format(
                AH=trunc(BinaryTools.unpack('uint32', x, 0x06) / 1000000.0),
                SOC=BinaryTools.unpack('uint8', x, 0x0a),
                L=BinaryTools.unpack('uint16', x, 0x00),
                H=BinaryTools.unpack('uint16', x, 0x02),
                B=BinaryTools.unpack('uint16', x, 0x02) - BinaryTools.unpack('uint16', x, 0x00),
                PT=BinaryTools.unpack('uint8', x, 0x04),
                BT=BinaryTools.unpack('uint8', x, 0x05),
                PV=BinaryTools.unpack('uint32', x, 0x0b)
            )
        }

    def bms_discharge_low(self, x: bytearray) -> Dict:
        """Parse BMS discharge low message"""
        if len(x) < 0x10:
            return {'event': 'Discharge Low', 'conditions': 'Insufficient data'}
            
        return {
            'event': 'Discharge Low',
            'conditions': 'L:{L}, H:{H}, B:{B:03d}, PT:{PT:03d}C, BT:{BT:03d}C, PV:{PV}'.format(
                L=BinaryTools.unpack('uint16', x, 0x00),
                H=BinaryTools.unpack('uint16', x, 0x02),
                B=BinaryTools.unpack('uint16', x, 0x02) - BinaryTools.unpack('uint16', x, 0x00),
                PT=BinaryTools.unpack('uint8', x, 0x04),
                BT=BinaryTools.unpack('uint8', x, 0x05),
                PV=BinaryTools.unpack('uint32', x, 0x0b)
            )
        }

    def bms_system_status(self, x: bytearray) -> Dict:
        """Parse BMS system status message"""
        if len(x) < 1:
            return {'event': 'System Status', 'conditions': 'No data'}
            
        state = BinaryTools.unpack('uint8', x, 0x00)
        return {
            'event': 'System Turned On' if state else 'System Turned Off',
            'conditions': None
        }

    def bms_soc_adjusted(self, x: bytearray) -> Dict:
        """Parse BMS SOC adjusted message"""
        if len(x) < 7:
            return {'event': 'SOC adjusted for voltage', 'conditions': 'Insufficient data'}
            
        return {
            'event': 'SOC adjusted for voltage',
            'conditions': 'old:{old_uAH:8.0f}uAH (soc:{old_soc:3d}%), new:{new_uAH:8.0f}uAH (soc:{new_soc:3d}%), low cell: {low_cell} mV'.format(
                old_uAH=BinaryTools.unpack('uint32', x, 0x00),
                old_soc=BinaryTools.unpack('uint8', x, 0x04),
                new_uAH=BinaryTools.unpack('uint32', x, 0x05),  # Note: offset might overlap, original code has this
                new_soc=BinaryTools.unpack('uint8', x, 0x09),   # Adjusted offset
                low_cell=BinaryTools.unpack('uint16', x, 0x0a) if len(x) > 0x0a else 0
            )
        }

    def bms_current_sensor_zeroed(self, x: bytearray) -> Dict:
        """Parse BMS current sensor zeroed message"""
        if len(x) < 5:
            return {'event': 'Current Sensor Zeroed', 'conditions': 'Insufficient data'}
            
        return {
            'event': 'Current Sensor Zeroed',
            'conditions': 'old: {old}mV, new: {new}mV, corrfact: {corrfact}'.format(
                old=BinaryTools.unpack('uint16', x, 0x00),
                new=BinaryTools.unpack('uint16', x, 0x02),
                corrfact=BinaryTools.unpack('uint8', x, 0x04)
            )
        }

    def bms_hibernate(self, x: bytearray) -> Dict:
        """Parse BMS hibernate message"""
        if len(x) < 1:
            return {'event': 'Hibernate', 'conditions': 'No data'}
            
        state = BinaryTools.unpack('uint8', x, 0x00)
        return {
            'event': 'Exiting Hibernate' if state == 0 else 'Entering Hibernate',
            'conditions': None
        }

    def bms_reflash(self, x: bytearray) -> Dict:
        """Parse BMS reflash message"""
        if len(x) < 22:
            return {'event': 'BMS Reflash', 'conditions': 'Insufficient data'}
            
        rev = BinaryTools.unpack('uint8', x, 0x00)
        build_date = BinaryTools.decode_str(x[2:22]).strip('\x00')
        
        return {
            'event': 'BMS Reflash',
            'conditions': f'Revision {rev}, Built {build_date}'
        }

    def bms_contactor(self, x: bytearray) -> Dict:
        """Parse BMS contactor message"""
        if len(x) < 13:
            return {'event': 'Contactor', 'conditions': 'Insufficient data'}
            
        state = BinaryTools.unpack('uint8', x, 0x00)
        pack_mv = BinaryTools.unpack('uint32', x, 0x01)
        switched_mv = BinaryTools.unpack('uint32', x, 0x05)
        dischg_cur = BinaryTools.unpack('uint32', x, 0x09)
        
        event = 'Contactor was Closed' if state else 'Contactor was Opened'
        
        return {
            'event': event,
            'conditions': f'Pack V: {pack_mv}mV, Switched V: {switched_mv}mV, Prechg Pct: {int((switched_mv/pack_mv)*100) if pack_mv > 0 else 0}%, Dischg Cur: {dischg_cur}mA'
        }

    def bms_discharge_cutback(self, x: bytearray) -> Dict:
        """Parse BMS discharge cutback message"""
        if len(x) < 1:
            return {'event': 'Discharge cutback', 'conditions': 'No data'}
            
        cut_percent = int((BinaryTools.unpack('uint8', x, 0x00) / 255.0) * 100)
        return {
            'event': 'Discharge cutback',
            'conditions': f'{cut_percent}%'
        }

    def bms_contactor_drive(self, x: bytearray) -> Dict:
        """Parse BMS contactor drive message"""
        if len(x) < 10:
            return {'event': 'Contactor drive turned on', 'conditions': 'Insufficient data'}
            
        pack_mv = BinaryTools.unpack('uint32', x, 0x01)
        switched_mv = BinaryTools.unpack('uint32', x, 0x05)
        duty_cycle = BinaryTools.unpack('uint8', x, 0x09)
        
        return {
            'event': 'Contactor drive turned on',
            'conditions': f'Pack V: {pack_mv}mV, Switched V: {switched_mv}mV, Duty Cycle: {duty_cycle}%'
        }

    def bms_soc_data_ascii(self, x: bytearray) -> Dict:
        """Parse BMS SOC data in ASCII format"""
        try:
            # Decode ASCII data and strip null terminator
            ascii_data = BinaryTools.decode_str(x).strip('\x00')
            
            if ascii_data.startswith('SOC:'):
                # Parse SOC data: "SOC:6804,98535,7734,114800,94,93,94,2,4057,4049,4050,1090"
                values = ascii_data[4:].split(',')  # Remove "SOC:" prefix
                if len(values) >= 12:
                    return {
                        'event': 'SOC Data',
                        'conditions': '{{"soc_raw_1": {}, "soc_raw_2": {}, "soc_raw_3": {}, "pack_voltage_mv": {}, "soc_percent_1": {}, "soc_percent_2": {}, "soc_percent_3": {}, "balance_count": {}, "voltage_max": {}, "voltage_min_1": {}, "voltage_min_2": {}, "current_ma": {}}}'.format(
                            values[0], values[1], values[2], values[3], values[4], values[5], 
                            values[6], values[7], values[8], values[9], values[10], values[11]
                        )
                    }
            elif ascii_data.startswith('DEBUG:'):
                # Handle debug messages
                debug_msg = ascii_data[6:].strip()  # Remove "DEBUG:" prefix
                return {
                    'event': debug_msg,
                    'conditions': None
                }
            else:
                # Generic ASCII message
                return {
                    'event': 'ASCII Message',
                    'conditions': ascii_data
                }
                
        except Exception:
            # Fallback for invalid ASCII
            return {
                'event': 'Unknown Message Type 0xFD',
                'conditions': f'Raw data: {display_bytes_hex(x)}' if x else 'No additional data'
            }

    def parse_mbb_entry(self, unescaped_block: bytearray, entry_number: int) -> Optional[Dict]:
        """Parse a single MBB entry"""
        try:
            if len(unescaped_block) < 5:
                return None
                
            # Extract message type from offset 0
            message_type = unescaped_block[0]
            
            # Extract timestamp from offset 1-4 (like original Gen2.timestamp_from_event)
            timestamp_bytes = unescaped_block[1:5]
            timestamp_int = struct.unpack('<I', timestamp_bytes)[0]
            
            # Skip invalid timestamps
            if timestamp_int <= 0xfff or timestamp_int > 1893456000:
                # Use incremental dummy timestamp for invalid entries
                timestamp_int = 1000000000 + entry_number
                
            # Apply timezone offset
            adjusted_timestamp = timestamp_int + (self.timezone_offset * 3600)
            timestamp_str = datetime.fromtimestamp(adjusted_timestamp).strftime(ZERO_TIME_FORMAT)
            
            # Extract message data (after type and timestamp)
            message_data = unescaped_block[5:] if len(unescaped_block) > 5 else bytearray()
            
            # Parse basic MBB message types (simplified)
            if message_type == 0x01:
                event = 'Board Status'
                conditions = 'No additional data'
            elif message_type == 0x09:
                event = 'Key State'
                conditions = self.parse_mbb_key_state(message_data)
            elif message_type == 0x2c:
                event = 'Run Status'
                conditions = self.parse_mbb_run_status(message_data)
            elif message_type == 0x2d:
                event = 'Charging Status'
                conditions = self.parse_mbb_charging_status(message_data)
            elif message_type == 0x33:
                event = 'Battery Status'
                conditions = self.parse_mbb_battery_status(message_data)
            elif message_type == 0x34:
                event = 'Power State'
                conditions = self.parse_mbb_power_state(message_data)
            elif message_type == 0xfd:
                # ASCII debug message
                try:
                    debug_msg = BinaryTools.decode_str(message_data).strip('\x00')
                    if debug_msg.startswith('DEBUG:'):
                        # Clean up DEBUG: prefix and set proper log level
                        event = debug_msg[6:].strip()  # Remove "DEBUG:" prefix
                        conditions = None
                        # Will be set to DEBUG log level later
                    elif debug_msg.startswith('INFO:'):
                        # Handle INFO: messages
                        event = debug_msg[5:].strip()  # Remove "INFO:" prefix
                        conditions = None
                        # Will be set to INFO log level later
                    elif debug_msg.startswith('ERROR:'):
                        # Handle ERROR: messages
                        event = debug_msg[6:].strip()  # Remove "ERROR:" prefix
                        conditions = None
                        # Will be set to ERROR log level later
                    elif debug_msg.startswith('OBD:'):
                        # Handle OBD: messages
                        event = debug_msg[4:].strip()  # Remove "OBD:" prefix
                        conditions = None
                        # Will be set to OBD log level later
                    else:
                        event = debug_msg if debug_msg else 'Debug Message'
                        conditions = None
                except:
                    event = f'Debug Message (0x{message_type:02X})'
                    conditions = f'Raw data: {display_bytes_hex(message_data)}'
            else:
                # Unknown message type
                event = f'Unknown Message Type 0x{message_type:02X}'
                conditions = f'Raw data: {display_bytes_hex(message_data)}' if message_data else 'No additional data'
            
            entry = {
                'entry_number': entry_number,
                'time': timestamp_str,
                'event': event,
                'conditions': conditions,
                'sort_timestamp': adjusted_timestamp
            }
            
            # Determine log level based on original message prefixes for 0xfd messages
            if message_type == 0xfd:
                try:
                    original_debug_msg = BinaryTools.decode_str(message_data).strip('\x00')
                    if original_debug_msg.startswith('DEBUG:'):
                        entry['log_level'] = 'DEBUG'
                    elif original_debug_msg.startswith('INFO:'):
                        entry['log_level'] = 'INFO'
                    elif original_debug_msg.startswith('ERROR:'):
                        entry['log_level'] = 'ERROR'
                    elif original_debug_msg.startswith('OBD:'):
                        entry['log_level'] = 'OBD'
                    else:
                        entry['log_level'] = 'DEBUG'  # Default for 0xfd messages
                except:
                    entry['log_level'] = 'DEBUG'
            else:
                # For non-0xfd messages, use standard determination
                improved_event, improved_conditions, json_data, has_json_data = improve_message_parsing(
                    entry['event'], entry.get('conditions', ''))
                
                if improved_event != entry['event']:
                    entry['event'] = improved_event
                if improved_conditions != entry.get('conditions'):
                    entry['conditions'] = improved_conditions
                    
                entry['log_level'] = determine_log_level(entry['event'], has_json_data)
            
            # Post-process for special structured data patterns
            entry = self.extract_charger_data(entry)
            
            return entry
            
        except Exception as e:
            return None

    def parse_mbb_key_state(self, data: bytearray) -> str:
        """Parse MBB key state message"""
        if len(data) < 1:
            return 'No data'
        state = data[0]
        return f'Key state: {state}'

    def parse_mbb_run_status(self, data: bytearray) -> str:
        """Parse MBB run status message"""
        if len(data) < 1:
            return 'No data'
        status = data[0]
        return f'Run status: {status}'

    def parse_mbb_charging_status(self, data: bytearray) -> str:
        """Parse MBB charging status message"""
        if len(data) < 1:
            return 'No data'
        status = data[0]
        return f'Charging status: {status}'

    def parse_mbb_battery_status(self, data: bytearray) -> str:
        """Parse MBB battery status message"""
        if len(data) < 1:
            return 'No data'
        status = data[0]
        return f'Battery status: {status}'

    def parse_mbb_power_state(self, data: bytearray) -> str:
        """Parse MBB power state message"""
        if len(data) < 1:
            return 'No data'
        state = data[0]
        return f'Power state: {state}'

    def extract_charger_data(self, entry: Dict) -> Dict:
        """Extract structured data from charger messages"""
        event = entry.get('event', '')
        
        # Handle "Charger X Charging" messages
        if event.startswith('Charger ') and 'Charging' in event:
            # Pattern: "Charger 6 Charging SN:2329104 SW:209 237Vac  50Hz EVSE 16A"
            import re
            charger_pattern = r'Charger (\d+) Charging SN:(\w+) SW:(\w+) (\d+)Vac\s+(\d+)Hz EVSE (\d+)A'
            match = re.match(charger_pattern, event)
            
            if match:
                charger_num, serial, software, voltage, frequency, current = match.groups()
                
                # Create structured data
                structured_data = {
                    "charger_number": int(charger_num),
                    "serial_number": serial,
                    "software_version": software,
                    "voltage_vac": int(voltage),
                    "frequency_hz": int(frequency),
                    "evse_current_amps": int(current)
                }
                
                # Update entry
                entry['event'] = f'Charger {charger_num} Charging'
                entry['conditions'] = json.dumps(structured_data)
                entry['log_level'] = 'DATA'  # Upgrade to DATA since it contains structured info
        
        return entry

    def interpolate_missing_timestamps(self, entries: List[Dict]):
        """Interpolate missing timestamps using neighboring entries."""
        for i, entry in enumerate(entries):
            if isinstance(entry.get('time'), str) and entry['time'].isdigit():
                # This is a missing timestamp, try to interpolate
                prev_entry = entries[i-1] if i > 0 else None
                next_entry = entries[i+1] if i < len(entries) - 1 else None
                
                if prev_entry and next_entry:
                    prev_ts = prev_entry.get('sort_timestamp', 0)
                    next_ts = next_entry.get('sort_timestamp', 0)
                    if prev_ts > 0 and next_ts > 0:
                        interpolated_ts = (prev_ts + next_ts) / 2
                        entry['time'] = datetime.fromtimestamp(interpolated_ts).strftime(ZERO_TIME_FORMAT)
                        entry['sort_timestamp'] = interpolated_ts

    def emit_text_decoding(self) -> str:
        """Generate text output format matching original."""
        output_lines = []
        
        # Header based on log type
        if self.log_file.is_bms():
            output_lines.append('﻿Zero BMS log')
            output_lines.append('')
            
            # Add header info
            for key, value in self.header_info.items():
                if key == 'VIN':
                    continue  # Skip VIN for BMS
                output_lines.append(f'{key:<18} {value}')
            
            # Add timezone info
            timezone_str = f"UTC{'+' if self.timezone_offset >= 0 else ''}{self.timezone_offset}"
            output_lines.append(f'{"Timezone":<18} {timezone_str}')
            output_lines.append('')
            
            # Add entry count
            output_lines.append(f'Printing {len(self.entries)} of {len(self.entries)} log entries..')
            output_lines.append('')
            
        else:
            # MBB format
            for key, value in self.header_info.items():
                output_lines.append(f"{key}: {value}")
            output_lines.append("")
        
        # Table header
        output_lines.append(' Entry    Time of Log            Level     Event                      Conditions')
        output_lines.append('+--------+----------------------+--------------------------+----------------------------------')
        
        # Entries
        for entry in self.entries:
            line = f" {entry.get('entry_number', 0):>5d}     {entry.get('time', '')}  {entry.get('log_level', 'INFO'):<8s}   {entry.get('event', '')}"
            if entry.get('conditions'):
                line += f"            {entry['conditions']}"
            output_lines.append(line)
            
        return '\n'.join(output_lines)

    def emit_csv_decoding(self) -> str:
        """Generate CSV output format matching original script."""
        # Use semicolon delimiter and headers to match original
        output_lines = ['entry;timestamp;log_level;message;conditions;uninterpreted']
        
        for entry in self.entries:
            conditions = entry.get('conditions') or ''
            # Don't quote or escape - original format doesn't use quotes
            line = f"{entry.get('entry_number', 0)};{entry.get('time', '')};{entry.get('log_level', 'INFO')};{entry.get('event', '')};{conditions};"
            output_lines.append(line)
            
        return '\n'.join(output_lines)

    def emit_tsv_decoding(self) -> str:
        """Generate TSV output format."""
        output_lines = ['Entry\tTimestamp\tLogLevel\tEvent\tConditions']
        
        for entry in self.entries:
            conditions = entry.get('conditions') or ''
            conditions = conditions.replace('\t', ' ')
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
                'source_file': os.path.basename(self.log_file.file_path),
                'log_type': 'BMS' if self.log_file.is_bms() else 'MBB' if self.log_file.is_mbb() else 'Unknown',
                'parser_version': 'zero-log-parser',
                'generated_at': datetime.now().isoformat(),
                'timezone': f"UTC{'+' if self.timezone_offset >= 0 else ''}{self.timezone_offset}",
                'total_entries': len(self.entries)
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