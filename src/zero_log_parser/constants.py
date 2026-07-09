"""Shared constants for the Zero log parser."""

# Localized time format - use system locale preference
ZERO_TIME_FORMAT = '%Y-%m-%d %H:%M:%S'  # ISO format is more universal
# The output from the MBB (via serial port) lists time as GMT-7
MBB_TIMESTAMP_GMT_OFFSET = -7 * 60 * 60

EMPTY_CSV_VALUE = ''
CSV_DELIMITER = ';'

# Log format revisions.
REV0 = 0
REV1 = 1
REV2 = 2
REV3 = 3  # Ring buffer format (2024+ firmware)
REV4 = 4  # "b2 XX fb" telemetry format (Gen3 DSR/X etc.); 0xB2-framed,
          # 0xF9-terminated header, ~131KB files. See atomicdog-gen3 Kaitai spec.
