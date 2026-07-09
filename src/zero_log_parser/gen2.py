"""Gen2 log entry parser."""

import string
from math import trunc
from time import gmtime, strftime

from .binary import (
    BinaryTools,
    convert_bit_to_on_off,
    convert_mv_to_v,
    convert_ratio_to_percent,
    display_bytes_hex,
    hex_of_value,
)
from .constants import ZERO_TIME_FORMAT
from .parsing import determine_log_level, improve_message_parsing


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
                            logger.debug('Interpolated timestamp for entry %d: %s (was: %s)',
                                       entry['entry_num'] + 1, interpolated_time_str, entry['original_time_str'])
                    except:
                        # If interpolation fails, keep original
                        pass

        # Return in the original format
        return [(entry['sort_timestamp'], entry['entry_payload'], entry['entry_num'])
                for entry in improved_entries]

    @classmethod
    def bms_discharge_level(cls, x):
        bike_states = {
            0x01: 'Bike On',
            0x02: 'Charge',
            0x03: 'Idle'
        }

        # Extract raw data once from binary
        structured_data = {
            'amp_hours': trunc(BinaryTools.unpack('uint32', x, 0x06) / 1000000.0),
            'state_of_charge_percent': BinaryTools.unpack('uint8', x, 0x0a),
            'current_amps': trunc(BinaryTools.unpack('int32', x, 0x10) / 1000000.0),
            'voltage_low_cell_volts': BinaryTools.unpack('uint16', x, 0x0) / 1000.0,
            'voltage_unloaded_cell_volts': BinaryTools.unpack('uint16', x, 0x14) / 1000.0,
            'voltage_high_cell_volts': BinaryTools.unpack('uint16', x, 0x02) / 1000.0,
            'voltage_balance_mv': BinaryTools.unpack('uint16', x, 0x02) - BinaryTools.unpack('uint16', x, 0x0),
            'pack_temp_celsius': BinaryTools.unpack('uint8', x, 0x04),
            'bms_temp_celsius': BinaryTools.unpack('uint8', x, 0x05),
            'pack_voltage_volts': BinaryTools.unpack('uint32', x, 0x0b) / 1000.0,
            'pack_voltage_mv': BinaryTools.unpack('uint32', x, 0x0b),  # For backward compatibility
            'mode': bike_states.get(BinaryTools.unpack('uint8', x, 0x0f))
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = (
            '{AH:03.0f} AH, SOC:{SOC:3d}%, I:{I:3.0f}A, L:{L:4.2f}V, l:{l:4.2f}V, H:{H:4.2f}V, B:{B:03d}mV, '
            'PT:{PT:03d}C, BT:{BT:03d}C, PV:{PV:6.1f}V, M:{M}').format(
                AH=structured_data['amp_hours'],
                SOC=structured_data['state_of_charge_percent'],
                I=structured_data['current_amps'],
                L=structured_data['voltage_low_cell_volts'],
                l=structured_data['voltage_unloaded_cell_volts'],
                H=structured_data['voltage_high_cell_volts'],
                B=structured_data['voltage_balance_mv'],
                PT=structured_data['pack_temp_celsius'],
                BT=structured_data['bms_temp_celsius'],
                PV=structured_data['pack_voltage_volts'],
                M=structured_data['mode']
            )

        return {
            'event': 'Discharge level',
            'structured_data': structured_data,  # NEW: Direct structured data
            'conditions': legacy_conditions  # LEGACY: For backward compatibility
        }

    @classmethod
    def bms_charge_event_fields(cls, x):
        return {
            'AH': trunc(BinaryTools.unpack('uint32', x, 0x06) / 1000000.0),
            'B': BinaryTools.unpack('uint16', x, 0x02) - BinaryTools.unpack('uint16', x, 0x0),
            'L': BinaryTools.unpack('uint16', x, 0x00) / 1000.0,  # Convert to volts
            'H': BinaryTools.unpack('uint16', x, 0x02) / 1000.0,  # Convert to volts
            'PT': BinaryTools.unpack('uint8', x, 0x04),
            'BT': BinaryTools.unpack('uint8', x, 0x05),
            'SOC': BinaryTools.unpack('uint8', x, 0x0a),
            'PV': BinaryTools.unpack('uint32', x, 0x0b) / 1000.0  # Convert to volts
        }

    @classmethod
    def bms_charge_full(cls, x):
        # Extract raw binary data once into structured format
        fields = cls.bms_charge_event_fields(x)

        # Build structured data dictionary with descriptive names
        structured_data = {
            'amp_hours': fields['AH'],
            'state_of_charge_percent': fields['SOC'],
            'voltage_low_cell_volts': fields['L'],
            'voltage_high_cell_volts': fields['H'],
            'voltage_balance_mv': fields['B'],
            'pack_temp_celsius': fields['PT'],
            'bms_temp_celsius': fields['BT'],
            'pack_voltage_volts': fields['PV'],
            'event_type': 'charge_complete'
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = (
            '{AH:03.0f} AH, SOC: {SOC}%,         L:{L:4.2f}V,         H:{H:4.2f}V, B:{B:03d}mV, '
            'PT:{PT:03d}C, BT:{BT:03d}C, PV:{PV:6.1f}V').format_map(fields)

        return {
            'event': 'Charged To Full',
            'structured_data': structured_data,  # NEW: Direct structured data
            'conditions': legacy_conditions  # LEGACY: For backward compatibility
        }

    @classmethod
    def bms_discharge_low(cls, x):
        # Extract raw binary data once into structured format
        fields = cls.bms_charge_event_fields(x)

        # Build structured data dictionary with descriptive names
        structured_data = {
            'amp_hours': fields['AH'],
            'state_of_charge_percent': fields['SOC'],
            'voltage_low_cell_volts': fields['L'],
            'voltage_high_cell_volts': fields['H'],
            'voltage_balance_mv': fields['B'],
            'pack_temp_celsius': fields['PT'],
            'bms_temp_celsius': fields['BT'],
            'pack_voltage_volts': fields['PV'],
            'event_type': 'discharge_low'
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = (
            '{AH:03.0f} AH, SOC:{SOC:3d}%,         L:{L:4.2f}V,         H:{H:4.2f}V, B:{B:03d}mV, '
            'PT:{PT:03d}C, BT:{BT:03d}C, PV:{PV:6.1f}V').format_map(fields)

        return {
            'event': 'Discharged To Low',
            'structured_data': structured_data,  # NEW: Direct structured data
            'conditions': legacy_conditions  # LEGACY: For backward compatibility
        }

    @classmethod
    def bms_system_state(cls, x):
        return {
            'event': 'System Turned ' + convert_bit_to_on_off(BinaryTools.unpack('bool', x, 0x0))
        }

    @classmethod
    def bms_soc_adj_voltage(cls, x):
        # Extract binary data once
        old_uah = BinaryTools.unpack('uint32', x, 0x00)
        old_soc = BinaryTools.unpack('uint8', x, 0x04)
        new_uah = BinaryTools.unpack('uint32', x, 0x05)
        new_soc = BinaryTools.unpack('uint8', x, 0x09)
        low_cell_mv = BinaryTools.unpack('uint16', x, 0x0a)

        # Build structured data
        structured_data = {
            'old_capacity_microamp_hours': old_uah,
            'old_state_of_charge_percent': old_soc,
            'new_capacity_microamp_hours': new_uah,
            'new_state_of_charge_percent': new_soc,
            'low_cell_voltage_millivolts': low_cell_mv,
            'capacity_change_microamp_hours': new_uah - old_uah,
            'soc_change_percent': new_soc - old_soc
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = ('old:   {old}uAH (soc:{old_soc}%), '
                           'new:   {new}uAH (soc:{new_soc}%), '
                           'low cell: {low} mV').format(
                              old=old_uah, old_soc=old_soc,
                              new=new_uah, new_soc=new_soc,
                              low=low_cell_mv)

        return {
            'event': 'SOC adjusted for voltage',
            'structured_data': structured_data,
            'conditions': legacy_conditions
        }

    @classmethod
    def bms_curr_sens_zero(cls, x):
        # Extract binary data once
        old_mv = BinaryTools.unpack('uint16', x, 0x00)
        new_mv = BinaryTools.unpack('uint16', x, 0x02)
        corrfact = BinaryTools.unpack('uint8', x, 0x04)

        # Build structured data
        structured_data = {
            'old_value_millivolts': old_mv,
            'new_value_millivolts': new_mv,
            'correction_factor': corrfact,
            'old_value_volts': old_mv / 1000.0,
            'new_value_volts': new_mv / 1000.0,
            'adjustment_millivolts': new_mv - old_mv
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = 'old: {old}mV, new: {new}mV, corrfact: {corrfact}'.format(
            old=old_mv, new=new_mv, corrfact=corrfact
        )

        return {
            'event': 'Current Sensor Zeroed',
            'structured_data': structured_data,
            'conditions': legacy_conditions
        }

    @classmethod
    def bms_state(cls, x):
        entering_hibernate = BinaryTools.unpack('bool', x, 0x0)
        return {
            'event': ('Entering' if entering_hibernate else 'Exiting') + ' Hibernate'
        }

    @classmethod
    def bms_isolation_fault(cls, x):
        # Extract binary data once
        resistance_ohms = BinaryTools.unpack('uint32', x, 0x00)
        cell_number = BinaryTools.unpack('uint8', x, 0x04)

        # Build structured data
        structured_data = {
            'resistance_ohms': resistance_ohms,
            'cell_number': cell_number,
            'resistance_kiloohms': resistance_ohms / 1000.0,
            'resistance_megaohms': resistance_ohms / 1000000.0,
            'is_low_resistance': resistance_ohms < 1000000,  # Less than 1 megaohm is concerning
            'fault_severity': 'critical' if resistance_ohms < 100000 else 'warning' if resistance_ohms < 1000000 else 'info'
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = '{ohms} ohms to cell {cell}'.format(
            ohms=resistance_ohms, cell=cell_number
        )

        return {
            'event': 'Chassis Isolation Fault',
            'structured_data': structured_data,
            'conditions': legacy_conditions
        }

    @classmethod
    def bms_reflash(cls, x):
        # Extract binary data once
        revision = BinaryTools.unpack('uint8', x, 0x00)
        build_str = BinaryTools.unpack_str(x, 0x01, 20)

        # Build structured data
        structured_data = {
            'revision': revision,
            'build_string': build_str,
            'revision_hex': f'0x{revision:02X}',
            'is_printable_build': all(c.isprintable() for c in build_str),
            'build_length': len(build_str.rstrip('\x00'))
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = 'Revision {rev}, Built {build}'.format(
            rev=revision, build=build_str
        )

        return {
            'event': 'BMS Reflash',
            'structured_data': structured_data,
            'conditions': legacy_conditions
        }

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
        # Extract raw binary data once into structured format
        is_closed = BinaryTools.unpack('bool', x, 0x0)
        pack_voltage = BinaryTools.unpack('uint32', x, 0x01)
        switched_voltage = BinaryTools.unpack('uint32', x, 0x05)
        discharge_current = BinaryTools.unpack('int32', x, 0x09)

        # Convert to display units
        pack_voltage_v = pack_voltage / 1000.0
        switched_voltage_v = switched_voltage / 1000.0
        discharge_current_a = discharge_current / 1000.0
        precharge_percent = convert_ratio_to_percent(switched_voltage, pack_voltage)

        # Build structured data dictionary
        structured_data = {
            'contactor_state': 'closed' if is_closed else 'opened',
            'pack_voltage_volts': pack_voltage_v,
            'switched_voltage_volts': switched_voltage_v,
            'precharge_percent': precharge_percent,
            'discharge_current_amps': discharge_current_a,
            'pack_voltage_mv': pack_voltage,
            'switched_voltage_mv': switched_voltage,
            'discharge_current_ma': discharge_current
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = (
            'Pack V: {pv:6.1f}V, '
            'Switched V: {sv:6.1f}V, '
            'Prechg Pct: {pc:2.0f}%, '
            'Dischg Cur: {dc:4.0f}A').format(
                pv=pack_voltage_v,
                sv=switched_voltage_v,
                pc=precharge_percent,
                dc=discharge_current_a)

        return {
            'event': 'Contactor was ' + ('Closed' if is_closed else 'Opened'),
            'structured_data': structured_data,  # NEW: Direct structured data
            'conditions': legacy_conditions  # LEGACY: For backward compatibility
        }

    @classmethod
    def bms_discharge_cut(cls, x):
        # Extract binary data once
        cut_byte = BinaryTools.unpack('uint8', x, 0x00)
        cut_pct = round((cut_byte / 255) * 100)  # JS parser formula

        # Build structured data
        structured_data = {
            'cutback_percent': cut_pct,
            'cutback_raw_value': cut_byte,
            'cutback_ratio': cut_byte / 255.0
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = '{cut:2d}%'.format(cut=cut_pct)

        return {
            'event': 'Discharge Cutback',
            'structured_data': structured_data,
            'conditions': legacy_conditions
        }

    @classmethod
    def bms_contactor_drive(cls, x):
        # Extract binary data once
        pack_voltage_raw = BinaryTools.unpack('uint32', x, 0x01)
        switched_voltage_raw = BinaryTools.unpack('uint32', x, 0x05)
        duty_cycle = BinaryTools.unpack('uint8', x, 0x09)

        # Convert millivolts to volts for display
        pack_voltage_v = pack_voltage_raw / 1000.0
        switched_voltage_v = switched_voltage_raw / 1000.0

        # Build structured data
        structured_data = {
            'pack_voltage_volts': pack_voltage_v,
            'switched_voltage_volts': switched_voltage_v,
            'duty_cycle_percent': duty_cycle,
            'pack_voltage_millivolts': pack_voltage_raw,
            'switched_voltage_millivolts': switched_voltage_raw,
            'voltage_difference_volts': abs(pack_voltage_v - switched_voltage_v)
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = 'Pack V: {pv:6.1f}V, Switched V: {sv:6.1f}V, Duty Cycle: {dc}%'.format(
            pv=pack_voltage_v,
            sv=switched_voltage_v,
            dc=duty_cycle
        )

        return {
            'event': 'Contactor drive turned on',
            'structured_data': structured_data,
            'conditions': legacy_conditions
        }

    @classmethod
    def debug_message(cls, x):
        # Extract the debug message string
        message = BinaryTools.unpack_str(x, 0x0, count=len(x) - 1)

        log_level = None

        # Check if this is a SOC data message and optimize it directly
        if message.startswith('SOC:'):
            log_level = 'STATE'
            soc_data = message[4:]  # Remove 'SOC:' prefix
            if ',' in soc_data:
                values = [v.strip() for v in soc_data.split(',')]
                if len(values) >= 11:  # Extended format (11+ values)
                    # Parse numeric values safely
                    def safe_int(val):
                        try:
                            return int(val)
                        except ValueError:
                            return val

                    pack_voltage_mv = safe_int(values[3]) if len(values) > 3 else 0
                    voltage_max_mv = safe_int(values[8]) if len(values) > 8 else 0
                    voltage_min_1_mv = safe_int(values[9]) if len(values) > 9 else 0
                    voltage_min_2_mv = safe_int(values[10]) if len(values) > 10 else 0

                    # Build structured data dictionary directly
                    structured_data = {
                        'soc_raw_1': safe_int(values[0]) if len(values) > 0 else 0,
                        'soc_raw_2': safe_int(values[1]) if len(values) > 1 else 0,
                        'soc_raw_3': safe_int(values[2]) if len(values) > 2 else 0,
                        'pack_voltage_volts': round(pack_voltage_mv / 1000.0, 3) if isinstance(pack_voltage_mv, int) else pack_voltage_mv,
                        'pack_voltage_mv': pack_voltage_mv,  # Deprecated, use pack_voltage_volts
                        'soc_percent_1': safe_int(values[4]) if len(values) > 4 else 0,
                        'soc_percent_2': safe_int(values[5]) if len(values) > 5 else 0,
                        'soc_percent_3': safe_int(values[6]) if len(values) > 6 else 0,
                        'balance_count': safe_int(values[7]) if len(values) > 7 else 0,
                        'voltage_max_volts': round(voltage_max_mv / 1000.0, 3) if isinstance(voltage_max_mv, int) else voltage_max_mv,
                        'voltage_min_1_volts': round(voltage_min_1_mv / 1000.0, 3) if isinstance(voltage_min_1_mv, int) else voltage_min_1_mv,
                        'voltage_min_2_volts': round(voltage_min_2_mv / 1000.0, 3) if isinstance(voltage_min_2_mv, int) else voltage_min_2_mv,
                        'voltage_max': voltage_max_mv,  # Deprecated, use voltage_max_volts
                        'voltage_min_1': voltage_min_1_mv,  # Deprecated, use voltage_min_1_volts
                        'voltage_min_2': voltage_min_2_mv,  # Deprecated, use voltage_min_2_volts
                        'current_ma': safe_int(values[11]) if len(values) > 11 else None,
                        'current_amps': round(safe_int(values[11]) / 1000.0, 3) if len(values) > 11 and isinstance(safe_int(values[11]), int) else None
                    }

                    # Generate legacy conditions string for backward compatibility
                    legacy_conditions = message  # Keep original debug message

                    return {
                        'event': 'SOC Data',
                        'log_level': log_level,
                        'structured_data': structured_data,  # NEW: Direct structured data
                        'conditions': legacy_conditions  # LEGACY: For backward compatibility
                    }
                elif len(values) == 8:  # Compact format (8 values)
                    # Handle compact format similarly
                    def safe_int(val):
                        try:
                            return int(val)
                        except ValueError:
                            return val

                    pack_voltage_mv = safe_int(values[3])
                    structured_data = {
                        'soc_raw_1': safe_int(values[0]),
                        'soc_raw_2': safe_int(values[1]),
                        'soc_raw_3': safe_int(values[2]),
                        'pack_voltage_mv': pack_voltage_mv,
                        'pack_voltage_volts': pack_voltage_mv / 1000.0 if isinstance(pack_voltage_mv, int) else pack_voltage_mv,
                        'soc_percent_1': safe_int(values[4]),
                        'soc_percent_2': safe_int(values[5]),
                        'soc_percent_3': safe_int(values[6]),
                        'balance_or_current': safe_int(values[7])  # Could be balance count or current
                    }

                    return {
                        'event': 'SOC Data',
                        'log_level': log_level,
                        'structured_data': structured_data,  # NEW: Direct structured data
                        'conditions': message  # LEGACY: For backward compatibility
                    }

        if message.startswith('DEBUG:'):
            message = message[7:].strip()
            log_level = 'DEBUG'
        elif message.startswith('INFO:'):
            message = message[6:].strip()
            log_level = 'INFO'
        elif message.startswith('ERROR:'):
            message = message[7:].strip()
            log_level = 'ERROR'
        elif message.startswith('WARNING:'):
            message = message[9:].strip()
            log_level = 'WARNING'

        # Check if this is a charger debug message and optimize it directly
        if message.startswith('Charger ') and 'SN:' in message and 'SW:' in message:
            import re

            # Pattern to match various charger message formats:
            # "Charger 6 Stopped SN:2329104 SW:209 236Vac  50Hz EVSE  8A"
            # "Charger 6 Charging SN:2329104 SW:209 237Vac 50Hz EVSE 16A"
            # "Charger 6 Charging SN:2329104 SW:209 237Vac  50Hz EVSE 16A" (extra spaces)
            charger_pattern = r'Charger\s+(\d+)\s+(\w+)\s+SN:(\d+)\s+SW:(\d+)\s+(\d+)Vac\s+(\d+)Hz\s+EVSE\s+(\d+)A'
            match = re.search(charger_pattern, message)

            if match:
                charger_num, status, serial_num, sw_version, voltage_ac, frequency, evse_current = match.groups()

                # Build structured data dictionary
                structured_data = {
                    'charger_number': int(charger_num),
                    'status': status,
                    'serial_number': serial_num,
                    'software_version': int(sw_version),
                    'voltage_ac': int(voltage_ac),
                    'frequency_hz': int(frequency),
                    'evse_current_amps': int(evse_current)
                }

                return {
                    'event': 'Charger Status',
                    'log_level': log_level,
                    'structured_data': structured_data,  # NEW: Direct structured data
                    'conditions': message  # LEGACY: For backward compatibility
                }
            else:
                # If pattern doesn't match exactly, still try to extract some basic charger info
                basic_charger_pattern = r'Charger (\d+) (\w+)'
                basic_match = re.search(basic_charger_pattern, message)
                if basic_match:
                    charger_num, status = basic_match.groups()
                    return {
                        'event': 'Charger Status',
                        'log_level': log_level,
                        'structured_data': {
                            'charger_number': int(charger_num),
                            'status': status,
                            'raw_message': message  # Include full message for unknown formats
                        },
                        'conditions': message
                    }

        # For other debug messages, return as normal
        return {
            'event': message,
            'log_level': log_level
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
        # Extract binary data once
        key_on = BinaryTools.unpack('bool', x, 0x0)
        key_state_text = convert_bit_to_on_off(key_on)

        # Build structured data
        structured_data = {
            'key_on': key_on,
            'key_state': key_state_text,
            'is_key_on': key_on,
            'is_key_off': not key_on
        }

        # Generate legacy event string for backward compatibility
        event_name = 'Key ' + key_state_text + (' ' if key_on else '')

        return {
            'event': event_name,
            'structured_data': structured_data
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

        # Extract raw binary data once into structured format
        pack_temp_hi = BinaryTools.unpack('uint8', x, 0x0)
        pack_temp_low = BinaryTools.unpack('uint8', x, 0x1)
        soc = BinaryTools.unpack('uint16', x, 0x2)
        pack_voltage = convert_mv_to_v(BinaryTools.unpack('uint32', x, 0x4))
        motor_temp = BinaryTools.unpack('int16', x, 0x8)
        controller_temp = BinaryTools.unpack('int16', x, 0xa)
        rpm = BinaryTools.unpack('uint16', x, 0xc)
        battery_current = BinaryTools.unpack('int16', x, 0x10)
        mods_raw = BinaryTools.unpack('uint8', x, 0x12)
        mods = mod_translate.get(mods_raw, 'Unknown')
        motor_current = BinaryTools.unpack('int16', x, 0x13)
        ambient_temp = BinaryTools.unpack('int16', x, 0x15)
        odometer = BinaryTools.unpack('uint32', x, 0x17)

        # Build structured data dictionary
        structured_data = {
            'pack_temp_high_celsius': pack_temp_hi,
            'pack_temp_low_celsius': pack_temp_low,
            'state_of_charge_percent': soc,
            'pack_voltage_volts': pack_voltage,
            'motor_temp_celsius': motor_temp,
            'controller_temp_celsius': controller_temp,
            'motor_rpm': rpm,
            'battery_current_amps': battery_current,
            'motor_current_amps': motor_current,
            'ambient_temp_celsius': ambient_temp,
            'odometer_km': odometer,
            'mods': mods,
            'mods_raw': mods_raw
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = (
            'PackTemp: h {pack_temp_hi}C, l {pack_temp_low}C, '
            'PackSOC:{soc:3d}%, '
            'Vpack:{pack_voltage:7.3f}V, '
            'MotAmps:{motor_current:4d}, BattAmps:{battery_current:4d}, '
            'Mods: {mods}, '
            'MotTemp:{motor_temp:4d}C, CtrlTemp:{controller_temp:4d}C, '
            'AmbTemp:{ambient_temp:4d}C, '
            'MotRPM:{rpm:4d}, '
            'Odo:{odometer:5d}km').format(
                pack_temp_hi=pack_temp_hi,
                pack_temp_low=pack_temp_low,
                soc=soc,
                pack_voltage=pack_voltage,
                motor_temp=motor_temp,
                controller_temp=controller_temp,
                rpm=rpm,
                battery_current=battery_current,
                mods=mods,
                motor_current=motor_current,
                ambient_temp=ambient_temp,
                odometer=odometer)

        return {
            'event': 'Riding',
            'structured_data': structured_data,  # NEW: Direct structured data
            'conditions': legacy_conditions  # LEGACY: For backward compatibility
        }

    @classmethod
    def charging_status(cls, x):
        # Extract raw binary data once into structured format
        pack_temp_hi = BinaryTools.unpack('uint8', x, 0x00)
        pack_temp_low = BinaryTools.unpack('uint8', x, 0x01)
        soc = BinaryTools.unpack('uint16', x, 0x02)
        pack_voltage = convert_mv_to_v(BinaryTools.unpack('uint32', x, 0x4))
        battery_current = BinaryTools.unpack('int8', x, 0x08)
        mods = BinaryTools.unpack('uint8', x, 0x0c)
        ambient_temp = BinaryTools.unpack('int8', x, 0x0d)

        # Build structured data dictionary
        structured_data = {
            'pack_temp_high_celsius': pack_temp_hi,
            'pack_temp_low_celsius': pack_temp_low,
            'ambient_temp_celsius': ambient_temp,
            'state_of_charge_percent': soc,
            'pack_voltage_volts': pack_voltage,
            'battery_current_amps': battery_current,
            'mods': mods,
            'mbb_charge_enabled': True,  # Always true based on message format
            'bms_charge_enabled': False  # Always false based on message format
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = (
            'PackTemp: h {pack_temp_hi}C, l {pack_temp_low}C, AmbTemp: {ambient_temp}C, '
            'PackSOC:{soc:3d}%, Vpack:{pack_voltage:7.3f}V, BattAmps: {battery_current:3d}, '
            'Mods: {mods:02b}, MbbChgEn: Yes, BmsChgEn: No').format(
                pack_temp_hi=pack_temp_hi,
                pack_temp_low=pack_temp_low,
                ambient_temp=ambient_temp,
                soc=soc,
                pack_voltage=pack_voltage,
                battery_current=battery_current,
                mods=mods)

        return {
            'event': 'Charging',
            'structured_data': structured_data,  # NEW: Direct structured data
            'conditions': legacy_conditions  # LEGACY: For backward compatibility
        }

    @classmethod
    def sevcon_status(cls, x):
        # Sevcon Gen4 CANopen emergency (EMCY) fault codes.
        # Sourced from the BorgWarner/Sevcon Gen4 fault reference as compiled in the
        # Open Vehicle Monitoring System project (rt_sevcon_faults.cpp).
        cause = {
            0x4681: 'Preop',                        # Unit in preoperational state
            0x4884: 'Sequence Fault',               # Drive switch active at power up
            0x4981: 'Throttle Fault',               # Throttle exceeds 20% at power up
            0x45C9: 'Motor Low Voltage Cutback',    # Entered low voltage cutback region
            0x45CA: 'Motor High Voltage Cutback',   # Entered high voltage cutback region
            0x5041: 'Bad NVM Data',                 # EEPROM/flash config corrupted
            0x5042: 'VPDO Out Of Range',            # VPDO mapped to invalid object
            0x5043: 'Static Range Error',           # Config object out of range
            0x5044: 'Dynamic Range Error',          # Object range depends on another
            0x5045: 'Auto-configuration Fault',     # Unable to auto-configure I/O
            0x5081: 'Invalid Steer Switches',       # Steering switches invalid state
            0x5101: 'Line Contactor Open Circuit',  # Contactor did not close when energized
            0x5102: 'Line Contactor Welded',        # Contactor closed when de-energized
        }

        # Extract binary data once
        error_code = BinaryTools.unpack('uint16', x, 0x00)
        sevcon_code = BinaryTools.unpack('uint16', x, 0x02)
        error_reg = BinaryTools.unpack('uint8', x, 0x04)
        additional_data = x[5:] if len(x) > 5 else []

        # Build structured data
        structured_data = {
            'error_code': error_code,
            'error_code_hex': f'0x{error_code:04X}',
            'sevcon_error_code': sevcon_code,
            'sevcon_error_code_hex': f'0x{sevcon_code:04X}',
            'error_register': error_reg,
            'error_register_hex': f'0x{error_reg:02X}',
            'additional_data_hex': [f'{c:02X}' for c in additional_data],
            'additional_data_raw': list(additional_data),
            'cause': cause.get(sevcon_code, 'Unknown'),
            'is_known_error': sevcon_code in cause
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = ('Error Code: 0x{code:04X}, Error Reg: 0x{reg:02X}, '
                           'Sevcon Error Code: 0x{sevcon_code:04X}, Data: {data}, {cause}').format(
                              code=error_code,
                              reg=error_reg,
                              sevcon_code=sevcon_code,
                              data=' '.join([f'{c:02X}' for c in additional_data]),
                              cause=cause.get(sevcon_code, 'Unknown')
                          )

        return {
            'event': 'SEVCON CAN EMCY Frame',
            'structured_data': structured_data,
            'conditions': legacy_conditions
        }

    @classmethod
    def charger_info(cls, x):
        """REV4 telemetry charger info (type 0x48). The 10-byte ASCII name is
        reliable per the atomicdog-gen3 Kaitai spec; the remaining fields
        (version/serial/status) are not fully specified upstream, so they are
        preserved as raw hex rather than guessed."""
        name = ''
        try:
            name = BinaryTools.unpack_str(x, 0x0, count=min(10, len(x))).strip('\x00').strip()
        except Exception:
            pass
        hex_data = ' '.join('0x{:02x}'.format(b) for b in x)
        result = {
            'event': 'Charger Info' + (' ({})'.format(name) if name else ''),
            'conditions': ('Name: {}, Raw: {}'.format(name, hex_data) if name
                           else 'Raw: {}'.format(hex_data)),
        }
        if name:
            result['structured_data'] = {'charger_name': name}
        return result

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
            0x06: 'SMPC',            # Onboard charger on Cypher/DS 2022+ platforms
        }

        # Extract binary data once
        charger_state_code = BinaryTools.unpack('uint8', x, 0x1)
        charger_id = BinaryTools.unpack('uint8', x, 0x0)
        state_name = states.get(charger_state_code, 'Unknown')
        charger_name = name.get(charger_id, 'Unknown')

        # Build structured data
        structured_data = {
            'charger_id': charger_id,
            'charger_name': charger_name,
            'state_code': charger_state_code,
            'state': state_name,
            'is_connected': charger_state_code == 0x01,
            'is_calex_720w': charger_id == 0x00,
            'is_calex_1200w': charger_id == 0x01,
            'is_external_charger': charger_id in [0x02, 0x03],
            'is_smpc': charger_id == 0x06,
            'is_known_charger': charger_id in name
        }

        # Generate legacy event string for backward compatibility
        event_name = '{name} Charger {charger_id} {state:13s}'.format(
            charger_id=charger_id,
            state=state_name,
            name=charger_name
        )

        return {
            'event': event_name,
            'structured_data': structured_data
        }

    @classmethod
    def battery_status(cls, x):
        opening_contactor = 'Opening Contractor'
        closing_contactor = 'Closing Contractor'
        registered = 'Registered'
        events = {
            0x00: opening_contactor,
            0x01: closing_contactor,
            0x02: registered,
        }

        # Extract binary data once
        event = BinaryTools.unpack('uint8', x, 0x0)
        module_num = BinaryTools.unpack('uint8', x, 0x1)
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

        event_name = events.get(event, 'Unknown (0x{:02x})'.format(event))

        # Build structured data
        structured_data = {
            'module_number': module_num,
            'event_type': event_name,
            'event_code': event,
            'module_voltage_volts': mod_volt,
            'system_max_voltage_volts': sys_max,
            'system_min_voltage_volts': sys_min,
            'voltage_difference_volts': sys_max - sys_min,
            'capacitor_voltage_volts': capacitor_volt,
            'battery_current_amps': battery_current,
            'serial_number': printable_serial_no,
            'precharge_percent': convert_ratio_to_percent(capacitor_volt, mod_volt) if event == 1 else None
        }

        # Generate legacy conditions string for backward compatibility
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
                module=module_num,
                event=event_name
            ),
            'structured_data': structured_data,
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

        # Extract binary data once
        power_on_cause_code = BinaryTools.unpack('uint8', x, 0x1)
        power_on = BinaryTools.unpack('bool', x, 0x0)
        power_state_text = convert_bit_to_on_off(power_on)
        source_name = sources.get(power_on_cause_code, 'Unknown')

        # Build structured data
        structured_data = {
            'power_on': power_on,
            'power_state': power_state_text,
            'power_source_code': power_on_cause_code,
            'power_source': source_name,
            'is_key_switch': power_on_cause_code == 0x01,
            'is_external_charger': power_on_cause_code in [0x02, 0x03],
            'is_onboard_charger': power_on_cause_code == 0x04,
            'is_known_source': power_on_cause_code in sources
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = source_name

        return {
            'event': 'Power ' + power_state_text,
            'structured_data': structured_data,
            'conditions': legacy_conditions
        }

    @classmethod
    def sevcon_power_state(cls, x):
        # Extract binary data once
        power_on = BinaryTools.unpack('bool', x, 0x0)
        power_state_text = convert_bit_to_on_off(power_on)

        # Build structured data
        structured_data = {
            'sevcon_power_on': power_on,
            'power_state': power_state_text,
            'is_powered': power_on,
            'controller_type': 'sevcon'
        }

        return {
            'event': 'Sevcon Turned ' + power_state_text,
            'structured_data': structured_data,
            'conditions': None  # No legacy conditions needed for this simple event
        }

    @classmethod
    def show_bluetooth_state(cls, x):
        return {
            'event': 'BT RX buffer reset'
        }

    @classmethod
    def battery_discharge_current_limited(cls, x):
        # Extract binary data once
        limit = BinaryTools.unpack('uint16', x, 0x00)
        min_cell_mv = BinaryTools.unpack('uint16', x, 0x02)
        temp = BinaryTools.unpack('uint8', x, 0x04)
        max_amp = BinaryTools.unpack('uint16', x, 0x05)

        # Build structured data
        structured_data = {
            'limit_current_amps': limit,
            'max_current_amps': max_amp,
            'limit_percentage': convert_ratio_to_percent(limit, max_amp),
            'min_cell_voltage_mv': min_cell_mv,
            'min_cell_voltage_volts': round(min_cell_mv / 1000.0, 3),
            'max_pack_temp_celsius': temp,
            'is_current_limited': True,
            'current_reduction_amps': max_amp - limit
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = '{limit} A ({percent:.2f}%), MinCell: {min_cell}mV, MaxPackTemp: {temp}C'.format(
            limit=limit,
            min_cell=min_cell_mv,
            temp=temp,
            max_amp=max_amp,
            percent=convert_ratio_to_percent(limit, max_amp)
        )

        return {
            'event': 'Batt Dischg Cur Limited',
            'structured_data': structured_data,
            'conditions': legacy_conditions
        }

    @classmethod
    def low_chassis_isolation(cls, x):
        # Extract binary data once
        resistance_kohms = BinaryTools.unpack('uint32', x, 0x00)
        cell_number = BinaryTools.unpack('uint8', x, 0x04)

        # Build structured data
        structured_data = {
            'resistance_kohms': resistance_kohms,
            'resistance_ohms': resistance_kohms * 1000,
            'resistance_mohms': resistance_kohms * 1000000,
            'affected_cell_number': cell_number,
            'isolation_fault': True,
            'is_critical': resistance_kohms < 500  # Typical critical threshold
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = '{kohms} KOhms to cell {cell}'.format(
            kohms=resistance_kohms,
            cell=cell_number
        )

        return {
            'event': 'Low Chassis Isolation',
            'structured_data': structured_data,
            'conditions': legacy_conditions
        }

    @classmethod
    def precharge_decay_too_steep(cls, x):
        return {
            'event': 'Precharge Decay Too Steep. Restarting Sevcon.'
        }

    @classmethod
    def disarmed_status(cls, x):
        # Extract raw binary data once into structured format
        pack_temp_hi = BinaryTools.unpack('uint8', x, 0x0)
        pack_temp_low = BinaryTools.unpack('uint8', x, 0x1)
        soc = BinaryTools.unpack('uint16', x, 0x2)
        pack_voltage = convert_mv_to_v(BinaryTools.unpack('uint32', x, 0x4))
        motor_temp = BinaryTools.unpack('int16', x, 0x8)
        controller_temp = BinaryTools.unpack('int16', x, 0xa)
        rpm = BinaryTools.unpack('uint16', x, 0xc)
        battery_current = BinaryTools.unpack('uint8', x, 0x10)
        mods = BinaryTools.unpack('uint8', x, 0x12)
        motor_current = BinaryTools.unpack('int8', x, 0x13)
        ambient_temp = BinaryTools.unpack('int16', x, 0x15)
        odometer = BinaryTools.unpack('uint32', x, 0x17)

        # Build structured data dictionary
        structured_data = {
            'pack_temp_high_celsius': pack_temp_hi,
            'pack_temp_low_celsius': pack_temp_low,
            'state_of_charge_percent': soc,
            'pack_voltage_volts': pack_voltage,
            'motor_temp_celsius': motor_temp,
            'controller_temp_celsius': controller_temp,
            'motor_rpm': rpm,
            'battery_current_amps': battery_current,
            'motor_current_amps': motor_current,
            'ambient_temp_celsius': ambient_temp,
            'odometer_km': odometer,
            'modules_status': mods,
            'vehicle_state': 'disarmed'
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = (
            'PackTemp: h {pack_temp_hi}C, l {pack_temp_low}C, '
            'PackSOC:{soc:3d}%, '
            'Vpack:{pack_voltage:03.3f}V, '
            'MotAmps:{motor_current:4d}, BattAmps:{battery_current:4d}, '
            'Mods: {mods:02b}, '
            'MotTemp:{motor_temp:4d}C, CtrlTemp:{controller_temp:4d}C, '
            'AmbTemp:{ambient_temp:4d}C, '
            'MotRPM:{rpm:4d}, '
            'Odo:{odometer:5d}km').format(
                pack_temp_hi=pack_temp_hi,
                pack_temp_low=pack_temp_low,
                soc=soc,
                pack_voltage=pack_voltage,
                motor_temp=motor_temp,
                controller_temp=controller_temp,
                rpm=rpm,
                battery_current=battery_current,
                mods=mods,
                motor_current=motor_current,
                ambient_temp=ambient_temp,
                odometer=odometer)

        return {
            'event': 'Disarmed',
            'structured_data': structured_data,  # NEW: Direct structured data
            'conditions': legacy_conditions  # LEGACY: For backward compatibility
        }

    @classmethod
    def battery_contactor_closed(cls, x):
        # Extract binary data once
        module_number = BinaryTools.unpack('uint8', x, 0x0)

        # Build structured data
        structured_data = {
            'module_number': module_number,
            'contactor_state': 'closed',
            'contactor_closed': True,
            'event_type': 'contactor_closure',
            'module_id': f'module_{module_number:02d}'
        }

        return {
            'event': 'Battery module {module:02} contactor closed'.format(module=module_number),
            'structured_data': structured_data,
            'conditions': None  # No legacy conditions needed for this simple event
        }

    @classmethod
    def vehicle_state_telemetry(cls, x):
        """Parse Type 81 (0x51) - Vehicle State Telemetry (68 bytes)

        Optimized Gen2 parser - generates structured data directly from binary.
        Returns ProcessedLogEntry with both human-readable conditions and structured JSON data.
        """
        if len(x) < 68:
            return cls.unhandled_entry_format(0x51, x)

        # Extract vehicle state string (bytes 36-39)
        state_bytes = x[36:40]
        state = state_bytes.rstrip(b'\x00').decode('ascii', errors='ignore')

        # Decode key telemetry values using BinaryTools.unpack()
        odometer_m = BinaryTools.unpack('uint32', x, 0)      # Distance in meters
        soc_raw = BinaryTools.unpack('uint32', x, 4)         # State of charge raw
        ambient_temp_raw = BinaryTools.unpack('uint32', x, 8) # Ambient temperature raw

        # Temperature values are at bytes 48-63 as single bytes
        temp1 = BinaryTools.unpack('uint8', x, 48) if len(x) > 48 else 0  # Temperature 1 (°C)
        temp2 = BinaryTools.unpack('uint8', x, 49) if len(x) > 49 else 0  # Temperature 2 (°C)
        temp3 = BinaryTools.unpack('uint8', x, 50) if len(x) > 50 else 0  # Temperature 3 (°C)
        temp4 = BinaryTools.unpack('uint8', x, 51) if len(x) > 51 else 0  # Temperature 4 (°C)

        # Convert and calculate derived values
        odometer_km = odometer_m // 1000  # Convert meters to km
        soc_percent = max(0, min(100, int((soc_raw - 200) / 6.0)))  # Estimate SOC percentage
        ambient_temp_celsius = int(ambient_temp_raw / 1000) if ambient_temp_raw > 1000 else ambient_temp_raw

        # Create structured data
        structured_data = {
            'vehicle_state': state,
            'odometer_meters': odometer_m,
            'odometer_km': odometer_km,
            'soc_raw': soc_raw,
            'soc_percent': soc_percent,
            'ambient_temperature_raw': ambient_temp_raw,
            'ambient_temperature_celsius': ambient_temp_celsius,
            'temperature_1_celsius': temp1,
            'temperature_2_celsius': temp2,
            'temperature_3_celsius': temp3,
            'temperature_4_celsius': temp4
        }

        # Generate human-readable conditions with units
        conditions = (
            f"State: {state}, "
            f"PackSOC: {soc_percent}%, "
            f"Odo: {odometer_km}km, "
            f"AmbTemp: {ambient_temp_celsius}°C, "
            f"Temp1: {temp1}°C, Temp2: {temp2}°C, "
            f"Temp3: {temp3}°C, Temp4: {temp4}°C"
        )

        # Determine event name based on state - riding states show as "Riding" for plotting compatibility
        if state in ['RUN', 'IB', 'WSU', 'UN']:  # Active states that should show as "Riding"
            event_name = 'Riding'
        else:
            event_name = f'Vehicle State ({state})'

        return {
            'event': event_name,
            'structured_data': structured_data,  # NEW: Direct structured data
            'conditions': conditions  # Human-readable conditions with units
        }

    @classmethod
    def sensor_data(cls, x):
        """Parse Type 84 (0x54) - Sensor Data (22 bytes)

        Optimized Gen2 parser - generates structured data directly from binary.
        Returns structured sensor telemetry with both human-readable conditions and structured JSON data.
        """
        if len(x) < 22:
            return cls.unhandled_entry_format(0x54, x)

        # Extract binary data using BinaryTools.unpack()
        odometer_m = BinaryTools.unpack('uint32', x, 0)      # Distance in meters
        sensor1 = BinaryTools.unpack('uint32', x, 4)         # Sensor value 1
        sensor2 = BinaryTools.unpack('uint32', x, 8)         # Sensor value 2
        sensor3 = BinaryTools.unpack('uint32', x, 12)        # Sensor value 3
        sensor4 = BinaryTools.unpack('uint32', x, 16)        # Sensor value 4
        status = BinaryTools.unpack('uint16', x, 20)         # Status flags

        # Convert derived values
        odometer_km = odometer_m // 1000  # Convert meters to kilometers

        # Create structured data
        structured_data = {
            'odometer_meters': odometer_m,
            'odometer_km': odometer_km,
            'sensor_1_value': sensor1,
            'sensor_2_value': sensor2,
            'sensor_3_value': sensor3,
            'sensor_4_value': sensor4,
            'status_flags': status,
            'status_hex': f'0x{status:04x}'
        }

        # Generate human-readable conditions with units
        conditions = (
            f"Odometer: {odometer_km}km, "
            f"Sensor1: {sensor1}, Sensor2: {sensor2}, "
            f"Sensor3: {sensor3}, Sensor4: {sensor4}, "
            f"Status: 0x{status:04x}"
        )

        return {
            'event': 'Sensor Data',
            'structured_data': structured_data,  # NEW: Direct structured data
            'conditions': conditions  # Human-readable conditions with units
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
            0x51: "Vehicle State Telemetry",  # Type 81 (0x51)
            0x52: "Unknown Type 82",          # Type 82 (0x52) - appears in new format
            0x54: "Sensor Data",              # Type 84 (0x54)
            0xfb: "System Information",       # Type 251 (0xfb)
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
    def parse_entry(cls, log_data, address, unhandled, logger, timezone_offset=None, verbosity_level=1,
                    payload_offset=0x05):
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
        # payload_offset skips type + timestamp (0x05) plus, for the REV4 telemetry
        # format, the extra 4-byte field and 2-byte sequence counter (-> 0x0b).
        message = unescaped_block[payload_offset:]

        parsers = {
            # Unknown entry types to be added when defined: type, length, source, example
            0x01: cls.board_status,
            # 0x02: unknown, 2, 6350_MBB_2016-04-12, 0x02 0x2e 0x11 ???
            0x03: cls.bms_discharge_level,
            0x04: cls.bms_charge_full,
            0x05: BinaryTools.bms_unknown_type_5,
            0x06: cls.bms_discharge_low,
            0x08: cls.bms_system_state,
            0x09: cls.key_state,
            0x0b: cls.bms_soc_adj_voltage,
            0x0d: cls.bms_curr_sens_zero,
            0x0e: BinaryTools.bms_unknown_type_14,
            0x10: cls.bms_state,
            0x11: cls.bms_isolation_fault,
            0x12: cls.bms_reflash,
            0x13: cls.bms_change_can_id,
            0x15: cls.bms_contactor_state,
            0x16: cls.bms_discharge_cut,
            0x18: cls.bms_contactor_drive,
            0x1c: BinaryTools.mbb_unknown_type_28,
            # 0x1e: unknown, 4, 6472_MBB_2016-12-12, 0x1e 0x32 0x00 0x06 0x23 ???
            # 0x1f: unknown, 4, 5078_MBB_2017-01-20, 0x1f 0x00 0x00 0x08 0x43 ???
            # 0x20: unknown, 3, 6472_MBB_2016-12-12, 0x20 0x02 0x32 0x00 ???
            0x26: BinaryTools.mbb_unknown_type_38,
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
            0x37: BinaryTools.mbb_bt_rx_buffer_overflow,
            # 0x37: unknown, 0, 3558_MBB_2016-12-25, 0x37  ???
            0x38: cls.show_bluetooth_state,
            0x39: cls.battery_discharge_current_limited,
            0x3a: cls.low_chassis_isolation,
            0x3b: cls.precharge_decay_too_steep,
            0x3c: cls.disarmed_status,
            0x3d: cls.battery_contactor_closed,
            0x48: cls.charger_info,             # Type 72 (REV4 telemetry charger info)
            0x51: cls.vehicle_state_telemetry,  # Type 81
            0x54: cls.sensor_data,              # Type 84
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
        # Store original raw timestamp for comparison purposes
        entry['original_timestamp'] = BinaryTools.unpack('uint32', unescaped_block, 0x01)

        # Check if Gen2 parser already provided structured data (optimized path)
        has_structured_data = 'structured_data' in entry
        if not has_structured_data:
            # Apply improved message parsing for unoptimized entries
            improved_event, improved_conditions, json_data, has_json_data, was_modified, modification_type = improve_message_parsing(
                entry.get('event', ''), entry.get('conditions', ''), verbosity_level=verbosity_level, logger=logger)
            entry['event'] = improved_event  # Store the improved event
            entry['conditions'] = improved_conditions  # Store the improved conditions

        if not entry.get('log_level'):
            entry['log_level'] = determine_log_level(entry.get('event', ''))

        # Always store the numeric message type ID
        entry['message_type'] = f"0x{message_type:X}"

        return length, entry, unhandled
