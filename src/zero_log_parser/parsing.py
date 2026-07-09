"""Message parsing and log-level classification."""

import re


def improve_message_parsing(event_text: str, conditions_text: str = None, verbosity_level: int = 1, logger=None) -> tuple:
    """
    Improve message parsing by removing redundant prefixes and converting structured data to JSON.

    Args:
        event_text: The event text to process
        conditions_text: Optional conditions text
        verbosity_level: Verbosity level (0=quiet, 1=normal, 2=verbose, 3+=very verbose)
        logger: Logger instance for verbose output

    Returns tuple: (improved_event, improved_conditions, json_data, has_json_data, was_modified, modification_type)

    Note: This method tracks modifications to help identify which Gen2 binary parsers
    need to incorporate advanced message parsing directly for optimization.
    """
    if not event_text:
        return event_text, conditions_text, None, False, False, None

    improved_event = event_text
    improved_conditions = conditions_text
    json_data = None
    was_modified = False
    modification_type = None

    # Remove redundant DEBUG: prefix since we have log_level
    original_event = improved_event
    if improved_event.startswith('DEBUG: '):
        improved_event = improved_event[7:]
        was_modified = True
        modification_type = "prefix_removal"
    elif improved_event.startswith('INFO: '):
        improved_event = improved_event[6:]
        was_modified = True
        modification_type = "prefix_removal"
    elif improved_event.startswith('ERROR: '):
        improved_event = improved_event[7:]
        was_modified = True
        modification_type = "prefix_removal"
    elif improved_event.startswith('WARNING: '):
        improved_event = improved_event[9:]
        was_modified = True
        modification_type = "prefix_removal"

    # Handle edge cases only - most patterns have been moved to optimized Gen2 parsers
    try:
        # Handle abbreviated hex patterns from newer log formats (2025+)
        if re.match(r'^0x[0-9a-f]+(\s+0x[0-9a-f]+)*$', improved_event or '', re.IGNORECASE):
            # Parse hex pattern like "0x28 0x02" or "0x01"
            hex_parts = improved_event.split()
            if len(hex_parts) >= 1:
                try:
                    main_type = int(hex_parts[0], 16)
                    # Handle specific abbreviated patterns for newer formats
                    if main_type == 0x28:  # Battery CAN Link Up
                        if len(hex_parts) >= 2:
                            module_num = int(hex_parts[1], 16)
                            improved_event = f"Module {module_num:02d} CAN Link Up"
                        else:
                            improved_event = "Battery CAN Link Up"
                        improved_conditions = None
                        was_modified = True
                        modification_type = "hex_pattern_expansion"
                        if verbosity_level >= 2 and logger:
                            logger.debug(f"Hex pattern expanded: {original_event} → {improved_event} (needs Gen2 optimization)")
                    elif main_type == 0x29:  # Battery CAN Link Down
                        if len(hex_parts) >= 2:
                            module_num = int(hex_parts[1], 16)
                            improved_event = f"Module {module_num:02d} CAN Link Down"
                        else:
                            improved_event = "Battery CAN Link Down"
                        improved_conditions = None
                        was_modified = True
                        modification_type = "hex_pattern_expansion"
                        if verbosity_level >= 2 and logger:
                            logger.debug(f"Hex pattern expanded: {original_event} → {improved_event} (needs Gen2 optimization)")
                    else:
                        # Mark other unknown hex patterns
                        improved_event = f"Unknown (Type 0x{main_type:02x})"
                        if len(hex_parts) > 1:
                            data_parts = [f"0x{int(part, 16):02x}" for part in hex_parts[1:]]
                            improved_conditions = f"Data: {' '.join(data_parts)}"
                        else:
                            improved_conditions = "No additional data"
                        was_modified = True
                        modification_type = "hex_pattern_expansion"
                        if verbosity_level >= 2 and logger:
                            logger.debug(f"Hex pattern expanded: {original_event} → {improved_event} (needs Gen2 optimization)")
                except ValueError:
                    # If hex conversion fails, mark as malformed
                    improved_event = f"Unknown - {improved_event}"
                    improved_conditions = "Malformed hex pattern"
                    was_modified = True
                    modification_type = "malformed_hex_handling"
                    if verbosity_level >= 2 and logger:
                        logger.debug(f"Malformed hex handled: {original_event} → {improved_event} (needs Gen2 optimization)")

        # Handle single character entries (likely corrupted)
        elif improved_event and len(improved_event) == 1 and improved_event.isalpha():
            improved_event = f"Unknown - Single character: {improved_event}"
            improved_conditions = "Possibly corrupted entry"
            was_modified = True
            modification_type = "corrupted_entry_handling"
            if verbosity_level >= 2 and logger:
                logger.debug(f"Corrupted entry handled: {original_event} → {improved_event} (needs Gen2 optimization)")

    except (ValueError, AttributeError, IndexError):
        # If parsing fails, keep original format
        pass

    # Determine if this entry contains JSON data
    has_json_data = json_data is not None

    return improved_event, improved_conditions, json_data, has_json_data, was_modified, modification_type


def determine_log_level(message: str) -> str:
    """Determine log level based on message content patterns"""
    if not message:
        return 'UNKNOWN'

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
