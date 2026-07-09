"""Data models: exceptions, processed-entry record, log file wrapper, LogData."""

import json
import logging
import os
import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .binary import BinaryTools, is_vin, vin_guaranteed_prefix, vin_length
from .constants import REV0, REV1, REV2, REV3, REV4, ZERO_TIME_FORMAT
from .gen2 import Gen2
from .gen3 import Gen3


def logger_for_input(bin_file):
    return logging.getLogger(bin_file)


def _load_utils():
    """Return the zero_log_parser.utils module (used by the LogData call sites)."""
    import zero_log_parser.utils as _u
    return _u


class MismatchingVinError(Exception):
    """Raised when attempting to merge LogData objects with different VINs"""
    def __init__(self, vin1, vin2):
        self.vin1 = vin1
        self.vin2 = vin2
        super().__init__(f"Cannot merge logs with different VINs: '{vin1}' != '{vin2}'")


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
        entries = self._get_processed_entries(start_time, end_time)
        from zero_log_parser.emit import emit_tabular
        emit_tabular(entries, output_file, log_file=self.log_file,
                     out_format=out_format,
                     logger=logger_for_input(self.log_file.file_path),
                     unnest=unnest)

    def emit_json_decoding(self, output_file: str, logger=None, start_time=None, end_time=None):
        """Generate JSON output with structured log entries"""
        entries = self._get_processed_entries(start_time, end_time)
        log_info = {
            'vin': getattr(self, 'vin', 'Unknown'),
            'serial_number': getattr(self, 'serial_number', 'Unknown'),
            'initial_date': getattr(self, 'initial_date', 'Unknown'),
            'model': getattr(self, 'model', 'Unknown'),
            'firmware_rev': getattr(self, 'firmware_rev', 'Unknown'),
            'board_rev': getattr(self, 'board_rev', 'Unknown'),
        }
        resolved_logger = logger if logger else logger_for_input(self.log_file.file_path)
        from zero_log_parser.emit import emit_json
        emit_json(entries, output_file, log_file=self.log_file,
                  timezone_offset=self.timezone_offset, log_info=log_info,
                  logger=resolved_logger)

    def emit_zero_compatible_decoding(self, output_file: str, logger=None, start_time=None, end_time=None):
        logger = logger_for_input(self.log_file.file_path)
        entries = self._collect_and_process_entries(logger, start_time, end_time)
        from zero_log_parser.emit import emit_zero_compatible
        emit_zero_compatible(entries, output_file, log_file=self.log_file,
                             header_info=self.header_info,
                             timezone_offset=self.timezone_offset,
                             header_divider=self.header_divider,
                             logger=logger)

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
