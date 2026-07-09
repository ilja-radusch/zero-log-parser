#!/usr/bin/env python3

"""
Little decoder utility to parse Zero Motorcycle main bike board (MBB) and
battery management system (BMS) logs. These may be extracted from the bike
using the Zero mobile app. Once paired over bluetooth, select 'Support' >
'Email bike logs' and send the logs to yourself rather than / in addition to
zero support.

Usage:

   $ python zero_log_parser.py <*.bin file> [-o output_file]

Architecture:
This parser uses a hybrid approach combining Gen2/Gen3 binary parsing with
structured data extraction. Gen2 parsers have been optimized to generate
structured data directly from binary data, eliminating the "string formatting
→ regex re-parsing" overhead that existed previously.

Gen2 Parser Optimization - Completed (19 methods):
- Phase 1: disarmed_status(), bms_contactor_state(), bms_soc_adj_voltage()
- Phase 2: battery_status(), bms_discharge_cut(), bms_contactor_drive()
- Phase 3: bms_curr_sens_zero(), sevcon_status(), bms_isolation_fault(), power_state()
- Phase 4: charger_status(), bms_reflash(), key_state()
- Phase 5: battery_discharge_current_limited(), low_chassis_isolation(), sevcon_power_state(), battery_contactor_closed()
- Additional: vehicle_state_telemetry(), sensor_data()

All optimized parsers use direct binary extraction via BinaryTools.unpack() and
generate structured JSON data with human-readable conditions for all output formats.
This eliminates the previous "string formatting → regex re-parsing" overhead while
maintaining full backward compatibility. Redundant patterns removed from improve_message_parsing.

"""

import codecs
import json
import logging
import os
import re
import string
import struct
import sys
from collections import OrderedDict, namedtuple
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import trunc
from time import gmtime, localtime, strftime
from typing import Dict, List, Union, Optional

# Ensure the `zero_log_parser` package under src/ is importable when this file
# is run as a standalone script from any working directory (uninstalled). This
# lets the imports below resolve to the package rather than re-importing this
# root file. Inserted at position 0 so the package wins over the root module.
_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
# Force src/ to the FRONT: when run as a script the repo root (containing this
# file) is sys.path[0] and would otherwise re-import this root module as the
# `zero_log_parser` package. Move any pre-existing entry (e.g. from an editable
# install) ahead of the script directory.
if _SRC in sys.path:
    sys.path.remove(_SRC)
sys.path.insert(0, _SRC)

# Parser version - single-sourced from the package.
from zero_log_parser import __version__ as PARSER_VERSION

# Constants, binary tools, and value formatters now live in the package.
from zero_log_parser.constants import (
    ZERO_TIME_FORMAT, MBB_TIMESTAMP_GMT_OFFSET, EMPTY_CSV_VALUE, CSV_DELIMITER,
    REV0, REV1, REV2, REV3, REV4,
)
from zero_log_parser.binary import (
    BinaryTools, is_vin, vin_length, vin_guaranteed_prefix,
    convert_mv_to_v, convert_ratio_to_percent, convert_bit_to_on_off,
    hex_of_value, display_bytes_hex, print_value_tabular,
)
from zero_log_parser.parsing import improve_message_parsing, determine_log_level
from zero_log_parser.gen2 import Gen2


class MismatchingVinError(Exception):
    """Raised when attempting to merge LogData objects with different VINs"""
    def __init__(self, vin1, vin2):
        self.vin1 = vin1
        self.vin2 = vin2
        super().__init__(f"Cannot merge logs with different VINs: '{vin1}' != '{vin2}'")


def _load_utils():
    """Return the zero_log_parser.utils module.

    src/ is guaranteed on sys.path (see the bootstrap at the top of this file),
    so this is now a plain package import. Retained as a thin wrapper for the
    existing call sites in LogData and main().
    """
    import zero_log_parser.utils as _u
    return _u


from zero_log_parser.utils import get_timezone_offset


@dataclass
class ProcessedLogEntry:
    """Standardized log entry structure for all output formats"""
    entry_number: int
    timestamp: str
    sort_timestamp: float
    log_level: str
    event: str
    conditions: str
    uninterpreted: str = ""
    structured_data: Optional[dict] = None
    has_structured_data: bool = False
    message_type: str = "unknown"
    original_timestamp: Optional[str] = None  # Raw timestamp before interpolation

    def __eq__(self, other):
        """
        Compare ProcessedLogEntry objects based on content, not sequence.

        Compares: original_timestamp (or timestamp), event, message_type,
        and structured_data if available, otherwise conditions.
        Excludes: entry_number, log_level, sort_timestamp, uninterpreted, has_structured_data
        """
        if not isinstance(other, ProcessedLogEntry):
            return False

        # Use original_timestamp if available, fallback to timestamp
        self_ts = self.original_timestamp or self.timestamp
        other_ts = other.original_timestamp or other.timestamp

        # Use structured_data if available, otherwise use conditions
        self_content = self.structured_data if self.structured_data else self.conditions
        other_content = other.structured_data if other.structured_data else other.conditions

        return (
            self_ts == other_ts and
            self.event == other.event and
            self.message_type == other.message_type and
            self_content == other_content
        )

    def __hash__(self):
        """
        Generate hash based on content for set operations and deduplication.

        Uses same fields as __eq__: original_timestamp (or timestamp), event,
        message_type, and structured_data or conditions.
        """
        # Use original_timestamp if available, fallback to timestamp
        timestamp_val = self.original_timestamp or self.timestamp

        # Handle structured_data vs conditions with robust hashing for complex data
        if self.structured_data:
            # Convert to JSON string for consistent hashing of complex data structures
            import json
            content_val = json.dumps(self.structured_data, sort_keys=True, separators=(',', ':'))
        else:
            content_val = self.conditions

        return hash((
            timestamp_val,
            self.event,
            self.message_type,
            content_val
        ))


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


class Gen3:
    entry_data_fencepost = b'\x00\xb2'
    Entry = namedtuple('Gen3EntryType', ['event', 'time', 'conditions', 'uninterpreted', 'log_level', 'message_type'])
    min_timestamp = datetime.strptime('2019-01-01', '%Y-%M-%d')
    max_timestamp = datetime.now() + timedelta(days=365)

    @classmethod
    def timestamp_is_valid(cls, event_timestamp: datetime):
        return cls.min_timestamp < event_timestamp < cls.max_timestamp

    @classmethod
    def payload_to_entry(cls, entry_payload: bytearray, hex_on_error=False, logger=None, verbosity_level=1) -> Entry:
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
        # Clean up the payload string first to remove problematic characters for CSV
        if payload_string:
            payload_string = payload_string.replace('\t', ' ').replace('\n', ' ').replace('\r', ' ')
            payload_string = ''.join(c for c in payload_string if c.isprintable() or c.isspace()).strip()

        if len(payload_string) < 2:
            if hex_on_error:
                event_message = f"Unknown - {display_bytes_hex(entry_payload)}"
            else:
                event_message = "Unknown"
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
        improved_event, improved_conditions, json_data, has_json_data, was_modified, modification_type = improve_message_parsing(event_message, conditions_str, verbosity_level=verbosity_level, logger=logger)
        log_level = determine_log_level(improved_event)
        # Gen3 entries don't have numeric message types, so use "gen3" as identifier
        message_type = "gen3"

        return cls.Entry(improved_event, event_timestamp, improved_conditions,
                         display_bytes_hex(data_payload) if data_payload else '', log_level, message_type)


class LogData(object):
    """
    :type log_version: int
    :type header_info: Dict[str, str]
    :type entries_count: Optional[int]
    :type entries: List[str]
    :type timezone_offset: int
    """

    def __init__(self, log_file: LogFile, timezone_offset=None, verbosity_level=1):
        self.log_file = log_file
        self.timezone_offset = timezone_offset
        self.verbosity_level = verbosity_level
        self.log_version, self.header_info = self.get_version_and_header(log_file)
        self.entries_count, self.entries = self.get_entries_and_counts(log_file)

        # Eager processing: process entries immediately and cache results
        self._processed_entries = None
        self._processing_complete = False
        self._process_entries_eagerly()

    def _process_entries_eagerly(self):
        """Process all entries immediately after LogData creation and cache results."""
        if self._processing_complete:
            return  # Early return for subsequent calls

        try:
            logger = logger_for_input(self.log_file.file_path)
            self._processed_entries = self._collect_and_process_entries(
                logger=logger,
                verbosity_level=self.verbosity_level
            )
            self._processing_complete = True
        except Exception as e:
            # Fall back to lazy processing if eager processing fails
            logger_for_input(self.log_file.file_path).warning(f"Eager processing failed: {e}, falling back to lazy processing")
            self._processed_entries = None
            self._processing_complete = False

    def _get_processed_entries(self, start_time=None, end_time=None):
        """Get cached processed entries with optional time filtering."""
        # Ensure processing is complete
        if not self._processing_complete:
            self._process_entries_eagerly()

        if self._processed_entries is None:
            # Fallback to lazy processing
            logger = logger_for_input(self.log_file.file_path)
            return self._collect_and_process_entries(logger, start_time, end_time, self.verbosity_level)

        # Apply time filtering if needed
        if start_time or end_time:
            return self._filter_processed_entries(self._processed_entries, start_time, end_time)

        return self._processed_entries

    def _filter_processed_entries(self, entries, start_time, end_time):
        """Filter processed entries by time range."""
        if not start_time and not end_time:
            return entries

        filtered_entries = []
        for entry in entries:
            # Use sort_timestamp for filtering if available
            entry_time = entry.sort_timestamp if entry.sort_timestamp and entry.sort_timestamp > 0 else None

            # Skip entries with invalid timestamps
            if entry_time is None:
                continue

            # Apply start time filter
            if start_time and entry_time < start_time:
                continue

            # Apply end time filter
            if end_time and entry_time > end_time:
                continue

            filtered_entries.append(entry)

        return filtered_entries

    def _parse_entry_timestamp(self, time_str: str) -> datetime:
        """Parse entry timestamp string into datetime object."""
        if time_str.isdigit():
            # Invalid/zero timestamp - return epoch
            return datetime.fromtimestamp(0)

        try:
            # Try new ISO format first
            try:
                return datetime.strptime(time_str, ZERO_TIME_FORMAT)
            except ValueError:
                # Fall back to old format for compatibility
                return datetime.strptime(time_str, '%m/%d/%Y %H:%M:%S')
        except ValueError:
            # If all parsing fails, return epoch
            return datetime.fromtimestamp(0)

    def _collect_and_process_entries(self, logger=None, start_time=None, end_time=None, verbosity_level=None):
        """
        Centralized method to collect, parse, filter, and sort all log entries.
        Returns a list of ProcessedLogEntry objects ready for output formatting.
        """
        if not logger:
            logger = logger_for_input(self.log_file.file_path)

        # Use instance verbosity level if not provided
        if verbosity_level is None:
            verbosity_level = getattr(self, 'verbosity_level', 1)

        processed_entries = []

        if self.log_version < REV2 or self.log_version >= REV3:
            # Handle REV0/REV1/REV3/REV4 formats - collect and sort entries
            collected_entries = []
            read_pos = 0
            # REV4 entries carry a 4-byte field + 2-byte sequence counter between the
            # timestamp and the payload, so the message body starts at 0x0b.
            payload_offset = 0x0b if self.log_version == REV4 else 0x05

            if hasattr(self, 'entries_count'):
                for entry_num in range(self.entries_count):
                    try:
                        (length, entry_payload, unhandled) = Gen2.parse_entry(self.entries, read_pos,
                                                                              0,  # unhandled counter
                                                                              timezone_offset=self.timezone_offset,
                                                                              logger=logger, verbosity_level=verbosity_level,
                                                                              payload_offset=payload_offset)

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

                # Apply time filtering if specified
                if start_time or end_time:
                    collected_entries = self._filter_collected_entries(collected_entries, start_time, end_time)
                    logger.info(f"Filtered to {len(collected_entries)} entries based on time range")

                # Sort by timestamp (newest first)
                collected_entries.sort(key=lambda x: x[0], reverse=True)

                # Process sorted entries into ProcessedLogEntry objects
                for line_num, (sort_timestamp, entry_payload, original_entry_num) in enumerate(collected_entries):
                    message = entry_payload.get('event', '')
                    conditions = entry_payload.get('conditions', '')
                    log_level = entry_payload.get('log_level', 'INFO')

                    # Check if message has already been processed by Gen2/Gen3 parsers
                    existing_message_type = entry_payload.get('message_type')
                    structured_data = entry_payload.get('structured_data')
                    has_json_data = structured_data is not None

                    # Message was already processed by Gen2/Gen3 parser - use as-is
                    improved_message = message
                    improved_conditions = conditions
                    message_type = existing_message_type

                    processed_entry = ProcessedLogEntry(
                        entry_number=original_entry_num + 1,
                        timestamp=entry_payload.get('time', ''),
                        sort_timestamp=sort_timestamp if sort_timestamp > 0 else None,
                        log_level=log_level,
                        event=improved_message,
                        conditions=improved_conditions if improved_conditions else "",
                        uninterpreted="",
                        structured_data=structured_data,
                        has_structured_data=has_json_data,
                        message_type=message_type,
                        original_timestamp=str(entry_payload.get('original_timestamp', ''))
                    )
                    processed_entries.append(processed_entry)

        else:
            # Handle REV2 (Gen3) format
            for line, entry_payload in enumerate(self.entries):
                try:
                    entry = Gen3.payload_to_entry(entry_payload, logger=logger, verbosity_level=verbosity_level)

                    # Gen3 parser already processed the message - use the results directly
                    improved_event = entry.event
                    improved_conditions = entry.conditions
                    log_level = entry.log_level
                    message_type = entry.message_type if entry.message_type else "unchanged"

                    # Parse structured data if available
                    structured_data = None
                    has_json_data = False
                    if improved_conditions and improved_conditions.startswith('{'):
                        try:
                            structured_data = json.loads(improved_conditions)
                            improved_conditions = None  # Remove redundant text version
                            has_json_data = True
                        except json.JSONDecodeError:
                            pass  # Keep as text if JSON parsing fails

                    processed_entry = ProcessedLogEntry(
                        entry_number=line + 1,
                        timestamp=entry.time.strftime(ZERO_TIME_FORMAT),
                        sort_timestamp=entry.time.timestamp(),
                        log_level=log_level,
                        event=improved_event,
                        conditions=improved_conditions if improved_conditions else "",
                        uninterpreted=entry.uninterpreted if entry.uninterpreted else "",
                        structured_data=structured_data,
                        has_structured_data=has_json_data,
                        message_type=message_type,
                        original_timestamp=str(int(entry.time.timestamp()))  # Use timestamp as original for Gen3
                    )
                    processed_entries.append(processed_entry)
                except Exception as e:
                    logger.warning(f'Error processing entry {line}: {e}')

        return processed_entries

    def _filter_collected_entries(self, collected_entries, start_time=None, end_time=None):
        """Filter collected entries by time range."""
        if not start_time and not end_time:
            return collected_entries

        filtered_entries = []
        for sort_timestamp, entry_payload, entry_num in collected_entries:
            # Parse entry timestamp
            time_str = entry_payload.get('time', '0')
            entry_time = self._parse_entry_timestamp(time_str)

            # Skip obviously invalid timestamps (year > 2030 or epoch)
            if entry_time.year > 2030 or entry_time.year == 1970:
                filtered_entries.append((sort_timestamp, entry_payload, entry_num))
                continue

            # Apply timezone to entry timestamp for comparison
            try:
                # Try to load timezone utilities from wherever they live
                _utils = _load_utils()
                if _utils is not None:
                    entry_time_tz = _utils.apply_timezone_to_datetime(entry_time, None)  # Use system timezone
                else:
                    # Fallback - assume local timezone
                    entry_time_tz = entry_time.replace(tzinfo=timezone.utc).astimezone()
            except:
                # Last fallback - use naive datetime
                entry_time_tz = entry_time

            # Apply filters
            if start_time and entry_time_tz < start_time:
                continue
            if end_time and entry_time_tz > end_time:
                continue

            filtered_entries.append((sort_timestamp, entry_payload, entry_num))

        return filtered_entries

    def _filter_gen3_entries(self, collected_gen3_entries, start_time=None, end_time=None):
        """Filter Gen3 entries by time range."""
        if not start_time and not end_time:
            return collected_gen3_entries

        filtered_entries = []
        for timestamp, entry, line in collected_gen3_entries:
            # Convert timestamp to datetime object
            entry_time = datetime.fromtimestamp(timestamp)

            # Apply timezone for comparison
            try:
                # Try to load timezone utilities from wherever they live
                _utils = _load_utils()
                if _utils is not None:
                    entry_time_tz = _utils.apply_timezone_to_datetime(entry_time, None)  # Use system timezone
                else:
                    # Fallback - assume local timezone
                    entry_time_tz = entry_time.replace(tzinfo=timezone.utc).astimezone()
            except:
                # Last fallback - use naive datetime
                entry_time_tz = entry_time

            # Apply filters
            if start_time and entry_time_tz < start_time:
                continue
            if end_time and entry_time_tz > end_time:
                continue

            filtered_entries.append((timestamp, entry, line))

        return filtered_entries

    def get_version_and_header(self, log: LogFile):
        logger = logger_for_input(self.log_file.file_path)
        sys_info = OrderedDict()
        log_version = REV0
        raw = log.raw()

        # "b2 XX fb" telemetry format (Gen3 DSR/X and similar). File starts with a
        # 0xB2 header whose third byte is 0xFB; byte 0x01 is the file-header size
        # (0x75 for MBB, 0x7d for BMS). Header field offsets validated against the
        # atomicdog-gen3 Kaitai spec (zlog.yml / bms-g3.ksy.yml).
        if len(raw) >= 3 and raw[0] == 0xB2 and raw[2] == 0xFB:
            log_version = REV4

            def _strz(offset, limit=24):
                try:
                    value = log.unpack_str(offset, count=limit)
                    value = value.split('\x00', 1)[0].strip()
                    if value and BinaryTools.is_printable(value):
                        return value
                except Exception:
                    pass
                return None

            filename_vin = self.log_file.get_filename_vin()
            header_vin = _strz(0x29, 17)
            if header_vin and is_vin(header_vin):
                sys_info['VIN'] = header_vin
                if filename_vin and header_vin != filename_vin:
                    logger.warning("VIN mismatch: header:%s filename:%s",
                                   header_vin, filename_vin)
            else:
                sys_info['VIN'] = filename_vin if filename_vin else 'Unknown'

            sys_info['Serial number'] = 'Unknown'  # not located in this header
            # Model / firmware / board offsets are only known for the MBB header
            # layout (byte 0x01 == 0x75). The BMS layout (0x7d) differs and is not
            # specified upstream, so those fields stay Unknown rather than garbage.
            if raw[0x01] == 0x75:
                sys_info['Model'] = _strz(0x19, 12) or 'Unknown'
                try:
                    sys_info['Firmware rev.'] = log.unpack('uint8', 0x67)
                except Exception:
                    sys_info['Firmware rev.'] = 'Unknown'
                try:
                    sys_info['Board rev.'] = log.unpack('uint8', 0x65)
                except Exception:
                    sys_info['Board rev.'] = 'Unknown'
                firmware_build = _strz(0x6b, 12)
                if firmware_build:
                    sys_info['Firmware build'] = firmware_build
            else:
                sys_info['Model'] = 'Unknown'
                sys_info['Firmware rev.'] = 'Unknown'
                sys_info['Board rev.'] = 'Unknown'

        if len(sys_info) == 0 and (self.log_file.is_mbb() or self.log_file.is_unknown()):
            # Check for ring buffer format (2024+ firmware) - starts with log entries
            if log.raw()[0] == 0xb2 or (len(log.raw()) == 0x40000 and log.index_of_sequence(b'\xa1\xa1\xa1\xa1')):
                # Ring buffer format detected
                log_version = REV3  # New revision for ring buffer format
                filename_vin = self.log_file.get_filename_vin()
                first_run_idx = log.index_of_sequence(b'\xa1\xa1\xa1\xa1')

                # System info block sits at fixed offsets relative to the first-run
                # date header (0xa1a1a1a1). Observed on 2026 Cypher II MBB dumps:
                #   +0x1ea  serial number  (e.g. "RKT212300208")
                #   +0x22c  VIN            (17 chars, occasionally 'i'-prefixed)
                #   +0x246  model code     (e.g. "DS11")
                def _read_field(offset, count):
                    try:
                        if offset is not None and 0 <= offset and offset + count <= len(log.raw()):
                            value = log.unpack_str(offset, count=count).strip('\x00').strip()
                            if value and BinaryTools.is_printable(value):
                                return value
                    except Exception:
                        pass
                    return None

                # VIN: prefer the copy embedded in the header, validate, fall back
                # to the filename and warn on any mismatch.
                header_vin = None
                if first_run_idx:
                    header_vin = _read_field(first_run_idx + 0x22c, 17)
                    if header_vin and not is_vin(header_vin):
                        # Some dumps prefix the VIN with a stray byte (e.g. 'i').
                        header_vin = _read_field(first_run_idx + 0x22d, 17)
                if header_vin and is_vin(header_vin):
                    sys_info['VIN'] = header_vin
                    if filename_vin and header_vin != filename_vin:
                        logger.warning("VIN mismatch: header:%s filename:%s",
                                       header_vin, filename_vin)
                else:
                    sys_info['VIN'] = filename_vin if filename_vin else 'Unknown'

                # Serial number: fixed offset from the header, then legacy fallbacks.
                serial = None
                if first_run_idx:
                    serial = _read_field(first_run_idx + 0x1ea, 15)
                    if serial and (len(serial) < 8 or not serial.isalnum()):
                        serial = None
                    if serial is None:
                        legacy = _read_field(first_run_idx + 0x302, 15)
                        if legacy and len(legacy) >= 8 and legacy.isalnum():
                            serial = legacy
                if serial is None:
                    for search_offset in [0x3bd10, 0x3bd00, 0x3bd20]:
                        candidate = _read_field(search_offset, 15)
                        if candidate and len(candidate) >= 8 and candidate.isalnum():
                            serial = candidate
                            break
                sys_info['Serial number'] = serial if serial else 'Unknown'

                # First run date
                initial_date = _read_field(first_run_idx + 4, 20) if first_run_idx else None
                sys_info['Initial date'] = initial_date if initial_date else 'Unknown'

                # Model code (e.g. "DS11"); firmware/board rev not yet located here.
                model = _read_field(first_run_idx + 0x246, 4) if first_run_idx else None
                sys_info['Model'] = model if model else 'Unknown'
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
        if self.log_version == REV4:
            # "b2 XX fb" telemetry format: a fixed file header (size at byte 0x01)
            # is followed by ring-buffer garbage, then 0xB2-framed entries. Skip the
            # header and start at the first entry marker so the length-stepped walk
            # stays aligned (parsing from offset 0 would consume the file header's
            # own 0xB2 magic as a bogus entry and drift through the log).
            header_size = raw_log[0x01] if len(raw_log) > 1 else 0
            entries_start = raw_log.find(b'\xb2', header_size)
            if entries_start < 0:
                entries_start = 0
            event_log = raw_log[entries_start:]
            entries_count = event_log.count(b'\xb2')
            logger.info('REV4 telemetry log: entries start at 0x%x, %d markers',
                        entries_start, entries_count)
        elif self.log_version == REV3:
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
        return self.log_version < REV2 or self.log_version >= REV3

    def emit_tabular_decoding(self, output_file: str, out_format='tsv', logger=None, start_time=None, end_time=None, unnest=False):
        file_suffix = '.tsv' if out_format == 'tsv' else '.csv'
        tabular_output_file = output_file.replace('.txt', file_suffix, 1)
        field_sep = '\t' if out_format == 'tsv' else CSV_DELIMITER
        record_sep = os.linesep
        # Modify headers based on unnest option
        if unnest:
            headers = ['entry', 'timestamp', 'log_level', 'message', 'condition_key', 'condition_value', 'uninterpreted']
        else:
            headers = ['entry', 'timestamp', 'log_level', 'message', 'conditions', 'uninterpreted']

        if not logger:
            logger = logger_for_input(self.log_file.file_path)

        # Use cached processed entries with optional time filtering (OPTIMIZED)
        processed_entries = self._get_processed_entries(start_time, end_time)

        with open(tabular_output_file, 'w', encoding='utf-8') as output:
            def write_row(values):
                output.write(field_sep.join(values) + record_sep)

            write_row(headers)

            # Write processed entries
            for entry in processed_entries:
                if unnest and entry.structured_data:
                    # Unnest structured data into multiple rows
                    for key, value in entry.structured_data.items():
                        row_values = [
                            str(entry.entry_number),
                            entry.timestamp,
                            entry.log_level,
                            entry.event,
                            key,
                            str(value),
                            entry.uninterpreted
                        ]
                        write_row([print_value_tabular(x) for x in row_values])
                else:
                    # Standard single-row format
                    if unnest:
                        # For unnest format but no structured data, use empty key/value
                        row_values = [
                            str(entry.entry_number),
                            entry.timestamp,
                            entry.log_level,
                            entry.event,
                            '',  # condition_key
                            entry.conditions,  # condition_value (original conditions text)
                            entry.uninterpreted
                        ]
                    else:
                        # Use JSON-encoded structured data in conditions field if available
                        conditions_output = entry.conditions
                        if entry.structured_data:
                            conditions_output = json.dumps(entry.structured_data, separators=(',', ':'))

                        row_values = [
                            str(entry.entry_number),
                            entry.timestamp,
                            entry.log_level,
                            entry.event,
                            conditions_output,
                            entry.uninterpreted
                        ]
                    write_row([print_value_tabular(x) for x in row_values])

        logger_for_input(self.log_file.file_path).info('Saved to %s', tabular_output_file)

    def emit_json_decoding(self, output_file: str, logger=None, start_time=None, end_time=None):
        """Generate JSON output with structured log entries"""
        json_output_file = output_file.replace('.txt', '.json', 1)

        if not logger:
            logger = logger_for_input(self.log_file.file_path)

        # Use cached processed entries with optional time filtering (OPTIMIZED)
        processed_entries = self._get_processed_entries(start_time, end_time)

        # Prepare the JSON structure
        json_output = {
            'metadata': {
                'source_file': self.log_file.file_path,
                'log_type': 'MBB' if 'MBB' in self.log_file.file_path or 'Mbb' in self.log_file.file_path else 'BMS',
                'parser_version': f'zero-log-parser-{PARSER_VERSION}',
                'generated_at': datetime.now().isoformat(),
                'timezone': f'UTC{self.timezone_offset/3600:+.1f}' if self.timezone_offset else 'UTC+0.0',
                'total_entries': len(processed_entries)
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

        # Convert processed entries to JSON format
        for entry in processed_entries:
            json_entry = {
                'entry_number': entry.entry_number,
                'timestamp': entry.timestamp,
                'sort_timestamp': entry.sort_timestamp,
                'log_level': entry.log_level,
                'event': entry.event,
                'conditions': entry.conditions if entry.conditions and not entry.structured_data else None,
                'uninterpreted': entry.uninterpreted if entry.uninterpreted else None,
                'is_structured_data': entry.has_structured_data,
                'message_type': entry.message_type
            }

            # Add structured data if available
            if entry.structured_data:
                json_entry['structured_data'] = entry.structured_data

            json_output['entries'].append(json_entry)

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

    def emit_zero_compatible_decoding(self, output_file: str, logger=None, start_time=None, end_time=None):
        with open(output_file, 'w', encoding='utf-8-sig') as f:
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

            # Use centralized processing
            processed_entries = self._collect_and_process_entries(logger, start_time, end_time)

            write_line('Printing {0} of {0} log entries..'.format(len(processed_entries)))
            write_line()
            write_line(' Entry    Time of Log            Level     Event                      Conditions')
            f.write(self.header_divider)

            # Track unknown entries for compatibility with original logic
            unknown_entries = 0
            unknown = []

            def format_structured_data(structured_data):
                """Format structured data as readable key-value pairs"""
                if not structured_data:
                    return ""

                # Create readable key-value pairs
                formatted_pairs = []
                for key, value in structured_data.items():
                    # Convert snake_case to readable format
                    readable_key = key.replace('_', ' ').title()

                    # Format value based on type and key patterns
                    if isinstance(value, str):
                        formatted_value = value
                    elif 'percent' in key.lower():
                        formatted_value = f"{value}%"
                    elif 'ma' in key.lower():  # Check mA before amps to avoid conflict
                        formatted_value = f"{value}mA"
                    elif 'amps' in key.lower() or 'current' in key.lower():
                        formatted_value = f"{value}A"
                    elif 'mv' in key.lower():
                        formatted_value = f"{value}mV"
                    elif 'volts' in key.lower() or 'voltage' in key.lower():
                        formatted_value = f"{value}V"
                    elif 'celsius' in key.lower() or 'temp' in key.lower():
                        formatted_value = f"{value}°C"
                    else:
                        formatted_value = str(value)

                    formatted_pairs.append(f"{readable_key}: {formatted_value}")

                return ", ".join(formatted_pairs)

            # Output processed entries with zero-compatible formatting
            for entry in processed_entries:
                line_prefix = (self.output_line_number_field(entry.entry_number)
                               + self.output_time_field(entry.timestamp)
                               + f'  {entry.log_level:8}')

                # Determine what to show in conditions field
                conditions_display = ""
                if entry.structured_data:
                    # Format structured data as readable key-value pairs
                    conditions_display = format_structured_data(entry.structured_data)
                elif entry.conditions:
                    conditions_display = entry.conditions

                if conditions_display:
                    if '???' in conditions_display:
                        u = conditions_display[0]
                        unknown_entries += 1
                        if u not in unknown:
                            unknown.append(u)
                        conditions_display = '???'
                        write_line(
                            line_prefix + '   {message} {conditions}'.format(
                                message=entry.event, conditions=conditions_display))
                    else:
                        write_line(
                            line_prefix + '   {message:25}  {conditions}'.format(
                                message=entry.event, conditions=conditions_display))
                elif entry.uninterpreted:
                    # Handle Gen3 format with uninterpreted field
                    output_line = line_prefix + '   {event} [{uninterpreted}]'.format(
                        event=entry.event,
                        uninterpreted=entry.uninterpreted)
                    if re.match(r'\s+\[', output_line):
                        raise ValueError()
                    write_line(output_line)
                else:
                    write_line(line_prefix + '   {message}'.format(message=entry.event))

            write_line()

        # Log statistics (compatibility with original logic)
        if unknown:
            logger.info('%d unknown entries of types %s',
                        unknown_entries,
                        ', '.join(hex(ord(x)) for x in unknown))

        logger.info('Saved to %s', output_file)

    def _get_vin(self):
        """Get VIN from header info, handling various formats"""
        return self.header_info.get('VIN', 'Unknown')

    def _get_entry_key(self, entry_payload, entry_num):
        """Generate a unique key for entry deduplication based on timestamp, event type, and content"""
        timestamp = entry_payload.get('time', '0')
        event = entry_payload.get('event', '')
        conditions = entry_payload.get('conditions', '')

        # For entries with identical content, use entry number as tiebreaker
        return (timestamp, event, conditions, entry_num)

    def _merge_entries(self, other_log_data):
        """Merge entries from another LogData using cached ProcessedLogEntry objects with optimized deduplication."""
        # Use cached ProcessedLogEntry objects for efficient set-based deduplication
        self_entries = set(self._get_processed_entries())
        other_entries = set(other_log_data._get_processed_entries())

        # Efficient set-based merge with automatic deduplication using ProcessedLogEntry.__hash__ and __eq__
        merged_entries_set = self_entries | other_entries

        # Convert back to sorted list (newest first, matching existing behavior)
        merged_entries_list = sorted(merged_entries_set,
                                   key=lambda x: x.sort_timestamp if x.sort_timestamp and x.sort_timestamp > 0 else 0,
                                   reverse=True)

        # Store merged entries in the cache
        self._processed_entries = merged_entries_list
        self._processing_complete = True

        # Update entries count
        self.entries_count = len(merged_entries_list)

        # For backward compatibility, we need to maintain the original binary/payload format
        # This is a necessary trade-off for the optimization while maintaining compatibility
        if self.log_version < REV2 or self.log_version >= REV3:
            # For Gen2 format, we keep the original entries format but note that it's now optimized
            # The actual data is in _processed_entries cache
            self.entries = b'OPTIMIZED_MERGE_DATA'  # Placeholder - actual data in _processed_entries
        else:
            # For Gen3 format, similar approach
            self.entries = ['OPTIMIZED_MERGE_DATA']  # Placeholder

        return len(merged_entries_list)

    def __add__(self, other):
        """Merge two LogData objects using the + operator"""
        if not isinstance(other, LogData):
            raise TypeError(f"Cannot merge LogData with {type(other).__name__}")

        # Check VIN compatibility
        self_vin = self._get_vin()
        other_vin = other._get_vin()

        if self_vin != 'Unknown' and other_vin != 'Unknown' and self_vin != other_vin:
            raise MismatchingVinError(self_vin, other_vin)

        # Create new merged LogData
        from copy import deepcopy
        merged = deepcopy(self)

        # Merge entries with duplicate removal using optimized ProcessedLogEntry approach
        merged._merge_entries(other)

        # Prefer non-Unknown VIN
        if merged._get_vin() == 'Unknown' and other_vin != 'Unknown':
            merged.header_info['VIN'] = other_vin

        # Merge other header info, preferring non-Unknown values
        for key, value in other.header_info.items():
            if key not in merged.header_info or merged.header_info[key] == 'Unknown':
                if value != 'Unknown':
                    merged.header_info[key] = value

        return merged

    def __iadd__(self, other):
        """Merge another LogData object into this one using the += operator"""
        if not isinstance(other, LogData):
            raise TypeError(f"Cannot merge LogData with {type(other).__name__}")

        # Check VIN compatibility
        self_vin = self._get_vin()
        other_vin = other._get_vin()

        if self_vin != 'Unknown' and other_vin != 'Unknown' and self_vin != other_vin:
            raise MismatchingVinError(self_vin, other_vin)

        # Merge entries with duplicate removal
        self.entries, self.entries_count = self._merge_entries(other.entries, other.entries_count)

        # Prefer non-Unknown VIN
        if self._get_vin() == 'Unknown' and other_vin != 'Unknown':
            self.header_info['VIN'] = other_vin

        # Merge other header info, preferring non-Unknown values
        for key, value in other.header_info.items():
            if key not in self.header_info or self.header_info[key] == 'Unknown':
                if value != 'Unknown':
                    self.header_info[key] = value

        return self

    def __radd__(self, other):
        """Support sum() and other right-hand addition operations"""
        if other == 0:  # sum() starts with 0
            return self
        return other.__add__(self)


def parse_log(bin_file: str, output_file: str, tz_code=None, verbosity_level=1, verbose=None, logger=None, output_format='txt', start_time=None, end_time=None, unnest=False):
    """
    Parse a Zero binary log file into a human readable text file
    """
    # Handle backward compatibility for verbose parameter
    if verbose is not None:
        verbosity_level = 2 if verbose else 1

    if not logger:
        logger = console_logger(bin_file, verbosity_level=verbosity_level)
    logger.info('Parsing %s', bin_file)

    timezone_offset = get_timezone_offset(tz_code)

    log = LogFile(bin_file)
    log_data = LogData(log, timezone_offset=timezone_offset, verbosity_level=verbosity_level)

    if output_format.lower() in ['csv', 'tsv']:
        # Generate CSV/TSV output
        log_data.emit_tabular_decoding(output_file, out_format=output_format.lower(), start_time=start_time, end_time=end_time, unnest=unnest)
    elif output_format.lower() == 'json':
        # Generate JSON output
        log_data.emit_json_decoding(output_file, start_time=start_time, end_time=end_time)
    elif output_format.lower() == 'txt':
        # Generate standard text output
        if log_data.has_official_output_reference():
            log_data.emit_zero_compatible_decoding(output_file, start_time=start_time, end_time=end_time)
        else:
            log_data.emit_tabular_decoding(output_file, start_time=start_time, end_time=end_time, unnest=unnest)
            log_data.emit_zero_compatible_decoding(output_file, start_time=start_time, end_time=end_time)
    else:
        # Default to text format for unknown formats
        if log_data.has_official_output_reference():
            log_data.emit_zero_compatible_decoding(output_file, start_time=start_time, end_time=end_time)
        else:
            log_data.emit_tabular_decoding(output_file, start_time=start_time, end_time=end_time, unnest=unnest)
            log_data.emit_zero_compatible_decoding(output_file, start_time=start_time, end_time=end_time)


def default_parsed_output_for(bin_file_path: str):
    return os.path.splitext(bin_file_path)[0] + '.txt'


def is_log_file_path(file_path: str):
    return file_path.endswith('.bin')


def console_logger(name: str, verbosity_level=1, verbose=None):
    # Handle backward compatibility for verbose parameter
    if verbose is not None:
        verbosity_level = 2 if verbose else 1

    # Map verbosity levels to logging levels
    level_map = {
        0: logging.ERROR,    # Quiet - only errors
        1: logging.INFO,     # Normal - info and above
        2: logging.DEBUG,    # Verbose - debug and above
        3: logging.DEBUG,    # Very verbose - same as verbose for logging
        4: logging.DEBUG     # Debug - same as verbose for logging
    }

    log_level = level_map.get(verbosity_level, logging.INFO)
    logger = logging.Logger(name, level=log_level)
    logger_formatter = logging.Formatter('%(asctime)s [%(name)s] [%(levelname)s] %(message)s')
    logger_handler = logging.StreamHandler()
    logger_handler.setFormatter(logger_formatter)
    logger.addHandler(logger_handler)
    return logger


def logger_for_input(bin_file):
    return logging.getLogger(bin_file)


def generate_merged_output_name(bin_files, output_format='txt'):
    """Generate a meaningful output filename for merged log files"""
    import os
    from datetime import datetime

    # Extract common elements from filenames
    basenames = [os.path.basename(f) for f in bin_files]

    # Try to find common VIN/serial pattern
    common_vin = None
    for name in basenames:
        if len(name) > 17 and name.startswith('538'):  # Zero VIN pattern
            vin_candidate = name[:17]
            if all(vin_candidate in other_name for other_name in basenames):
                common_vin = vin_candidate
                break

    # Create descriptive filename
    if common_vin:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M')
        filename = f"{common_vin}_merged_{len(bin_files)}files_{timestamp}.{output_format}"
    else:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M')
        filename = f"zero_logs_merged_{len(bin_files)}files_{timestamp}.{output_format}"

    return filename


def parse_multiple_logs(bin_files, output_file, tz_code=None, verbosity_level=1, verbose=None, logger=None, output_format='txt', start_time=None, end_time=None, unnest=False):
    """
    Parse multiple Zero binary log files and merge them intelligently

    Args:
        bin_files: List of binary log file paths
        output_file: Output filename for merged result
        tz_code: Timezone offset
        verbose: Enable verbose logging
        logger: Logger instance
        output_format: Output format (txt, csv, tsv, json)
    """
    # Handle backward compatibility for verbose parameter
    if verbose is not None:
        verbosity_level = 2 if verbose else 1

    if not logger:
        logger = console_logger(' + '.join(bin_files), verbosity_level=verbosity_level)

    logger.info('Multi-file parsing: %d files', len(bin_files))

    # Validate all files exist and are readable
    for bin_file in bin_files:
        if not os.path.exists(bin_file):
            raise FileNotFoundError(f"Log file not found: {bin_file}")
        if not is_log_file_path(bin_file):
            logger.warning(f"File may not be a valid log file: {bin_file}")

    merged_log_data = None
    successful_files = 0

    # Convert timezone offset to seconds
    timezone_offset = get_timezone_offset(tz_code)

    # Process each file and merge
    for i, bin_file in enumerate(bin_files):
        try:
            logger.info('[%d/%d] Processing %s', i+1, len(bin_files), bin_file)

            # Parse individual file to LogData
            log_file = LogFile(bin_file, logger=logger)
            log_data = LogData(log_file, timezone_offset=timezone_offset, verbosity_level=verbosity_level)

            if merged_log_data is None:
                # First file becomes the base
                merged_log_data = log_data
                successful_files = 1
                logger.info('Base log data: %s entries', len(log_data.entries) if hasattr(log_data, 'entries') else 'unknown')
            else:
                # Merge with existing data using LogData's smart merging
                try:
                    merged_log_data = merged_log_data + log_data
                    successful_files += 1
                    logger.info('Merged successfully. Total entries: %s',
                              len(merged_log_data.entries) if hasattr(merged_log_data, 'entries') else 'unknown')
                except MismatchingVinError as e:
                    logger.error('VIN mismatch: %s', e)
                    logger.error('Skipping file: %s', bin_file)
                    continue
                except Exception as e:
                    logger.error('Failed to merge %s: %s', bin_file, e)
                    logger.error('Skipping file: %s', bin_file)
                    continue

        except Exception as e:
            logger.error('Failed to process %s: %s', bin_file, e)
            continue

    if merged_log_data is None:
        raise RuntimeError("No files could be processed successfully")

    if successful_files < len(bin_files):
        logger.warning('Successfully merged %d out of %d files', successful_files, len(bin_files))
    else:
        logger.info('Successfully merged all %d files', successful_files)

    # Output merged result
    logger.info('Writing merged log to %s', output_file)

    if output_format.lower() in ['csv', 'tsv']:
        # Generate CSV/TSV output
        merged_log_data.emit_tabular_decoding(output_file, out_format=output_format.lower(), start_time=start_time, end_time=end_time, unnest=unnest)
    elif output_format.lower() == 'json':
        # Generate JSON output
        merged_log_data.emit_json_decoding(output_file, start_time=start_time, end_time=end_time)
    elif output_format.lower() == 'txt':
        # Generate standard text output
        if merged_log_data.has_official_output_reference():
            merged_log_data.emit_zero_compatible_decoding(output_file, start_time=start_time, end_time=end_time)
        else:
            merged_log_data.emit_tabular_decoding(output_file, start_time=start_time, end_time=end_time, unnest=unnest)
            merged_log_data.emit_zero_compatible_decoding(output_file, start_time=start_time, end_time=end_time)
    else:
        # Default to text format for unknown formats
        if merged_log_data.has_official_output_reference():
            merged_log_data.emit_zero_compatible_decoding(output_file, start_time=start_time, end_time=end_time)
        else:
            merged_log_data.emit_tabular_decoding(output_file, start_time=start_time, end_time=end_time, unnest=unnest)
            merged_log_data.emit_zero_compatible_decoding(output_file, start_time=start_time, end_time=end_time)

    logger.info('Multi-file parsing completed: %s', output_file)


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description='Parse Zero Motorcycle binary log files. Supports single or multiple files with smart merging.',
        epilog='For interactive plotting, use: zero-plotting <input_files>')
    parser.add_argument('bin_files', nargs='+', help='Zero *.bin log file(s) to decode. Multiple files will be intelligently merged.')
    parser.add_argument('--timezone', help='Timezone offset in hours from UTC (e.g., -8 for PST, +1 for CET) or timezone name (e.g., "Europe/Berlin", "US/Pacific"). Defaults to local system timezone.')
    parser.add_argument('-o', '--output', help='decoded log filename (auto-generated if not specified)')
    parser.add_argument('-f', '--format', choices=['txt', 'csv', 'tsv', 'json'], default='txt',
                       help='Output format: txt (default), csv, tsv, or json')
    parser.add_argument('--unnest', action='store_true',
                       help='For CSV/TSV formats: expand structured data into separate rows with condition_key and condition_value columns')
    parser.add_argument('--start', help='Filter entries after this time (e.g., "June 2025", "2025-06-15", "last month")')
    parser.add_argument('--end', help='Filter entries before this time (e.g., "June 2025", "2025-06-15", "last month")')
    parser.add_argument('--start-end', help='Filter entries within this period (e.g., "June 2025" sets both start and end boundaries automatically)')
    parser.add_argument('-v', '--verbose', action='count', default=1, help='Increase verbosity level (-v, -vv, -vvv for levels 2, 3, 4)')
    parser.add_argument('-q', '--quiet', action='store_true', help='Quiet mode (verbosity level 0)')
    args = parser.parse_args()

    # Calculate verbosity level
    if args.quiet:
        verbosity_level = 0
    else:
        verbosity_level = args.verbose

    # Handle single vs multiple files
    bin_files = args.bin_files
    tz_code = args.timezone
    output_format = args.format

    # Parse time filtering parameters
    start_time = None
    end_time = None

    # Handle --start-end shorthand
    if args.start_end:
        if args.start or args.end:
            parser.error("--start-end cannot be used with --start or --end")
        try:
            # Load time parsing utilities from wherever they live
            _utils = _load_utils()
            if _utils is None:
                raise ValueError("Time filtering requires the zero_log_parser.utils module")
            start_time, end_time = _utils.parse_time_range(args.start_end, tz_code)
        except Exception as e:
            parser.error(f"Invalid --start-end time specification: {e}")
    else:
        # Handle individual --start and --end parameters
        if args.start:
            try:
                _utils = _load_utils()
                if _utils is None:
                    raise ValueError("Time filtering requires the zero_log_parser.utils module")
                start_time = _utils.parse_time_filter_start(args.start, tz_code)
            except Exception as e:
                parser.error(f"Invalid --start time specification: {e}")

        if args.end:
            try:
                _utils = _load_utils()
                if _utils is None:
                    raise ValueError("Time filtering requires the zero_log_parser.utils module")
                end_time = _utils.parse_time_filter_end(args.end, tz_code)
            except Exception as e:
                parser.error(f"Invalid --end time specification: {e}")

    if len(bin_files) == 1:
        # Single file - use existing behavior
        bin_file = bin_files[0]
        output_file = args.output or default_parsed_output_for(bin_file)
        parse_log(bin_file, output_file, tz_code=tz_code, verbosity_level=verbosity_level, output_format=output_format,
                  start_time=start_time, end_time=end_time, unnest=args.unnest)
    else:
        # Multiple files - use new multi-file parsing
        output_file = args.output or generate_merged_output_name(bin_files, output_format)
        parse_multiple_logs(bin_files, output_file,
                            tz_code=tz_code, verbosity_level=verbosity_level, output_format=output_format,
                            start_time=start_time, end_time=end_time, unnest=args.unnest)


if __name__ == '__main__':
    main()
