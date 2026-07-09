"""Output emitters for processed log entries (tabular, JSON, Zero-compatible).

These are free functions consuming a list of ProcessedLogEntry plus the small
amount of log metadata each format needs. The caller resolves the logger (via
logger_for_input) and passes it in, so these functions never construct one and
their stderr behavior matches the original LogData methods exactly.
"""

import json
import os
import re
from datetime import datetime

from . import __version__ as PARSER_VERSION
from .binary import print_value_tabular
from .constants import CSV_DELIMITER


def _output_line_number_field(line: int) -> str:
    return ' {line:05d}'.format(line=line)


def _output_time_field(time: str) -> str:
    return '     {time:>19s}'.format(time=time)


def emit_tabular(entries, output_file, *, log_file, out_format='tsv', logger, unnest=False):
    file_suffix = '.tsv' if out_format == 'tsv' else '.csv'
    tabular_output_file = output_file.replace('.txt', file_suffix, 1)
    field_sep = '\t' if out_format == 'tsv' else CSV_DELIMITER
    record_sep = os.linesep
    # Modify headers based on unnest option
    if unnest:
        headers = ['entry', 'timestamp', 'log_level', 'message', 'condition_key', 'condition_value', 'uninterpreted']
    else:
        headers = ['entry', 'timestamp', 'log_level', 'message', 'conditions', 'uninterpreted']

    processed_entries = entries

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

    logger.info('Saved to %s', tabular_output_file)


def emit_json(entries, output_file, *, log_file, timezone_offset, log_info, logger):
    """Generate JSON output with structured log entries"""
    json_output_file = output_file.replace('.txt', '.json', 1)

    processed_entries = entries

    # Prepare the JSON structure
    json_output = {
        'metadata': {
            'source_file': log_file.file_path,
            'log_type': 'MBB' if 'MBB' in log_file.file_path or 'Mbb' in log_file.file_path else 'BMS',
            'parser_version': f'zero-log-parser-{PARSER_VERSION}',
            'generated_at': datetime.now().isoformat(),
            'timezone': f'UTC{timezone_offset/3600:+.1f}' if timezone_offset else 'UTC+0.0',
            'total_entries': len(processed_entries)
        },
        'log_info': log_info,
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


def emit_zero_compatible(entries, output_file, *, log_file, header_info, timezone_offset,
                         header_divider, logger):
    with open(output_file, 'w', encoding='utf-8-sig') as f:

        def write_line(text=None):
            f.write(text + '\n' if text else '\n')

        write_line('Zero ' + log_file.log_type + ' log')
        write_line()

        for k, v in header_info.items():
            write_line('{0:18} {1}'.format(k, v))

        # Add timezone information
        tz_hours = timezone_offset / 3600
        if tz_hours >= 0:
            tz_str = f'UTC+{tz_hours:.1f}'
        else:
            tz_str = f'UTC{tz_hours:.1f}'
        write_line('{0:18} {1}'.format('Timezone', tz_str))
        write_line()

        processed_entries = entries

        write_line('Printing {0} of {0} log entries..'.format(len(processed_entries)))
        write_line()
        write_line(' Entry    Time of Log            Level     Event                      Conditions')
        f.write(header_divider)

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
                elif key.lower().endswith('kmh'):
                    formatted_value = f"{value} km/h"
                elif key.lower().endswith('mph'):
                    formatted_value = f"{value} mph"
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
            line_prefix = (_output_line_number_field(entry.entry_number)
                           + _output_time_field(entry.timestamp)
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
