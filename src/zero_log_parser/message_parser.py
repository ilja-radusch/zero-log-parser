"""Message parsing and improvement functions for Zero log entries."""

import json
import re
from typing import Optional, Tuple, Dict, Any


def improve_message_parsing(event_text: str, conditions_text: str = None) -> Tuple[str, Optional[str], Optional[Dict[str, Any]], bool]:
    """
    Improve message parsing by removing redundant prefixes and converting structured data to JSON.
    
    Returns tuple: (improved_event, improved_conditions, json_data, has_json_data)
    """
    if not event_text:
        return event_text, conditions_text, None, False
    
    improved_event = event_text
    improved_conditions = conditions_text
    json_data = None
    
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
        
        # Handle Contactor messages (was Opened/Closed)
        elif improved_event and ('was Opened' in improved_event or 'was Closed' in improved_event):
            if 'was Opened' in improved_event:
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
                    'serial_number': module_match.group(1),
                    'module_voltage_volts': float(module_match.group(2))
                }
                improved_conditions = json.dumps(json_data)
        
        # Handle Charger messages
        elif 'Charger' in improved_event and improved_conditions and 'SN:' in improved_conditions:
            charger_match = re.match(
                r'.*SN: ([^,]+), SW: (\d+), VAC: (\d+), Freq: (\d+), EVSE: (\d+)',
                improved_conditions
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
        
        # Handle Pack: battery pack configuration messages
        elif improved_event.startswith('Pack:'):
            pack_match = re.match(
                r'Pack:([^,]+),Numbricks:(\d+)',
                improved_event
            )
            if pack_match:
                pack_type = pack_match.group(1)
                num_bricks = int(pack_match.group(2))
                
                # Parse pack type for additional details
                pack_details = {}
                if '_' in pack_type:
                    parts = pack_type.split('_')
                    pack_details['year'] = parts[0] if parts[0].isdigit() else None
                    pack_details['type'] = parts[1] if len(parts) > 1 else pack_type
                else:
                    pack_details['type'] = pack_type
                
                json_data = {
                    'pack_type': pack_type,
                    'pack_year': pack_details.get('year'),
                    'pack_design': pack_details.get('type'),
                    'number_of_bricks': num_bricks
                }
                improved_event = 'Battery Pack Configuration'
                improved_conditions = json.dumps(json_data)
        
        # Handle Tipover Detected messages
        elif 'Tipover Detected!' in improved_event:
            # Pattern 1: Min/Max values
            tipover_minmax_match = re.search(
                r'RawRoll\(min,max\): (-?\d+),(-?\d+) - FiltRoll\(min,max\): (-?\d+),(-?\d+) - RawPit\(min,max\): (-?\d+),(-?\d+) - FiltPit\(min,max\): (-?\d+),(-?\d+)',
                improved_event
            )
            # Pattern 2: Current values
            tipover_curr_match = re.search(
                r'RawRoll\(curr\): (-?\d+) - FiltRoll\(curr\): (-?\d+) - RawPit\(curr\): (-?\d+) - FiltPit\(curr\): (-?\d+)',
                improved_event
            )
            
            if tipover_minmax_match:
                json_data = {
                    'measurement_type': 'min_max',
                    'raw_roll_min': int(tipover_minmax_match.group(1)),
                    'raw_roll_max': int(tipover_minmax_match.group(2)),
                    'filtered_roll_min': int(tipover_minmax_match.group(3)),
                    'filtered_roll_max': int(tipover_minmax_match.group(4)),
                    'raw_pitch_min': int(tipover_minmax_match.group(5)),
                    'raw_pitch_max': int(tipover_minmax_match.group(6)),
                    'filtered_pitch_min': int(tipover_minmax_match.group(7)),
                    'filtered_pitch_max': int(tipover_minmax_match.group(8))
                }
                improved_event = 'Tipover Detected'
                improved_conditions = json.dumps(json_data)
            elif tipover_curr_match:
                json_data = {
                    'measurement_type': 'current',
                    'raw_roll_current': int(tipover_curr_match.group(1)),
                    'filtered_roll_current': int(tipover_curr_match.group(2)),
                    'raw_pitch_current': int(tipover_curr_match.group(3)),
                    'filtered_pitch_current': int(tipover_curr_match.group(4))
                }
                improved_event = 'Tipover Detected'
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
        return 'INFO'
    
    message_lower = message.lower()
    
    # JSON/structured data entries are typically telemetry
    if is_json_data:
        return 'DATA'
    
    # Error patterns
    if any(keyword in message_lower for keyword in [
        'error', 'fault', 'fail', 'exception', 'crash', 'abort', 'panic',
        'corrupt', 'invalid', 'timeout', 'overload', 'overheat', 'undervolt',
        'overvolt', 'overcurrent', 'emergency', 'critical', 'alert'
    ]):
        return 'ERROR'
    
    # Warning patterns
    if any(keyword in message_lower for keyword in [
        'warn', 'caution', 'high', 'low', 'limit', 'threshold', 'abnormal',
        'unusual', 'unexpected', 'retry', 'reset', 'restart', 'recovery',
        'cutback', 'disable', 'suspend'
    ]):
        return 'WARNING'
    
    # State change patterns
    if any(keyword in message_lower for keyword in [
        'enter', 'exit', 'state', 'mode', 'turn on', 'turn off', 'start',
        'stop', 'begin', 'end', 'open', 'close', 'connect', 'disconnect',
        'enable', 'disable', 'activate', 'deactivate'
    ]):
        return 'STATE'
    
    # Debug/diagnostic patterns
    if any(keyword in message_lower for keyword in [
        'debug', 'trace', 'dump', 'raw', 'register', 'memory', 'buffer',
        'can ', 'sync', 'frame', 'packet', 'message', 'signal'
    ]):
        return 'DEBUG'
    
    # Default to INFO for general information
    return 'INFO'