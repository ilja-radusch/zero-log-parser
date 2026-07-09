"""Gen3 log entry parser."""

import re
from collections import OrderedDict, namedtuple
from datetime import datetime, timedelta

from .binary import BinaryTools, display_bytes_hex
from .constants import ZERO_TIME_FORMAT
from .parsing import determine_log_level, improve_message_parsing


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
