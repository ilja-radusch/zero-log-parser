# MBB log file layout

*note: values in raw logs are little endian*

## Format Variations

This document describes two observed MBB log file formats:

### Legacy Format (Original Documentation)
- Static data at fixed addresses (0x200, 0x240, etc.)
- Log sections with headers at predictable locations

### Ring Buffer Format (Newer Files, e.g., 2024+ firmware)
- File starts directly with log entries at offset 0x0000
- No static data at documented addresses
- Section headers located at end of file
- File size: exactly 0x40000 bytes (262144 bytes)

## Static addresses (Legacy Format Only)

Address    | Length | Contents
---------- | :----: | --------
0x00000200 | 21     | Serial number
0x00000240 | 17     | VIN number
0x0000027b | 2      | Firmware revision
0x0000027d | 2      | Board revision
0x0000027f | 3      | Bike model (`SS`, `SR`, `DS`, 'FX')

## Ring Buffer Format Layout

In newer log files (2024+ firmware), the structure is:

Address    | Length | Contents
---------- | :----: | --------
0x00000000 | variable | Log entries start immediately (ring buffer)
0x0003BA0E | 24     | First run date section (0xa1a1a1a1 + date string)
0x0003BD10 | ~13    | Serial number (e.g., "RKT212200313")
0x0003BF00 | 16+    | Error log section (0xa3a3a3a3 + header info)
0x0003C100 | 16+    | Event log section (0xa2a2a2a2 + header info)

*Note: Addresses may vary between files. Section headers should be located by scanning for the 4-byte sequences.*

## Log sections (located by header sequence)

### Unknown *(build date?)*

Offset     | Length | Contents
---------- | :----: | --------
0x00000000 | 4      | `0xa0 0xa0 0xa0 0xa0` section header
0x00000004 | 20     | Date and time (ascii)

### Unknown *(first run date?)*

Offset     | Length | Contents
---------- | :----: | --------
0x00000000 | 4      | `0xa1 0xa1 0xa1 0xa1` section header
0x00000004 | 20     | Date and time (ascii)

### Event Log

Offset     | Length     | Contents
---------- | :--------: | --------
0x00000000 | 4          | `0xa2 0xa2 0xa2 0xa2` section header
0x00000004 | 4          | Log entries end address
0x00000008 | 4          | Log entries start address
0x0000000c | 4          | Log entries count
0x00000010 | *variable* | Log data begins

### Error Log

Offset     | Length     | Contents
---------- | :--------: | --------
0x00000000 | 4          | `0xa3 0xa3 0xa3 0xa3` section header
0x00000004 | 4          | Log entries end address
0x00000008 | 4          | Log entries start address
0x0000000c | 4          | Log entries count
0x00000010 | *variable* | Log data begins

The event log file appears to be a direct memory dump from a ring buffer. All logs export as 0x3ffff bytes in length. Bikes logs which exceed this offset begin overwriting themself from the top of the log data section.

# BMS log file layout

Address    | Length | Contents
---------- | :----: | --------
0x00000000 | 3      | `BMS`
0x0000000e | 4      | `0xa1 0xa1 0xa1 0xa1` section header
0x00000012 | 20     | *First run date?*
0x00000300 | 21     | Serial number
0x00000320 | 8      | Pack serial number
0x0000036a | 4      | `0xa0 0xa0 0xa0 0xa0` section header
0x0000036e | 20     | *Date / time - unknown, but close to time @ 0x00000012*
0x00000500 | 4      | `0xa3 0xa3 0xa3 0xa3` section header
0x00000700 | 4      | `0xa2 0xa2 0xa2 0xa2` section header
0x00000704 |        | Main log begins

# Log entry format (shared by MBB and BMS)

Offset | Length    | Contents
------ | :-------: | --------
0x00   | 1         | `0xb2` Entry header
0x01   | 1         | Entry length (including header byte)
0x02   | 1         | Entry type - see section `Log file entry types`
0x03   | 4         | Timestamp (seconds since the Unix epoch)
0x07   | *variable* | Entry data

Note that the entry appears to be encoded in some format starting from the entry type onwards (ie Entry type, timestamp, Entry data). Any bytes of 0xFE are xor'ed with the next byte -1.

For example, the byte sequenze 0xFE, 0x01 becomes 0xFE. The byte sequence OxFE, 0x4d becomes 0xb2. The length of the message is reduced accordingly.

## Log file entry types

### `0x0` - board status
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | cause of reset

### `0x2` - MBB High Throttle Disable.
Offset | Length | Contents
------ | :----: | --------
0x00   | 2      | thr in mv
0x02   | 3      | unknown

### `0x3` - BMS discharge level
Offset | Length | Contents
------ | :----: | --------
0x00   | 2      | L low cell
0x02   | 2      | H high cell
0x04   | 1      | PT pack temp
0x05   | 1      | BT board? temp
0x06   | 4      | AH microamp hours
0x0a   | 1      | SOC %
0x0b   | 4      | PV pack voltage mv
0x0f   | 1      | state 0x01 = 'Bike On', 0x02 = 'Charge', 0x03 = 'Idle'
0x10   | 4      | I microamps
0x14   | 2      | l: unloaded? cell
0x16   | 2      | unknown
B balance = H - L

### `0x4` - BMS charge full
Offset | Length | Contents
------ | :----: | --------
0x00   | 2      | L low cell
0x02   | 2      | H high cell
0x04   | 1      | PT pack temp
0x05   | 1      | BT board? temp
0x06   | 4      | AH microamp hours
0x0a   | 1      | SOC %
0x0b   | 4      | PV pack voltage mv
B balance = H - L

### `0x5` - BMS unknown
Offset | Length | Contents
------ | :----: | --------
0x00   | 17      | ???

### `0x6` - BMS discharge low
Offset | Length | Contents
------ | :----: | --------
0x00   | 2      | L low cell
0x02   | 2      | H high cell
0x04   | 1      | PT pack temp
0x05   | 1      | BT board? temp
0x06   | 4      | AH microamp hours
0x0a   | 1      | SOC %
0x0b   | 4      | PV pack voltage mv
B balance = H - L

### `0x8` - BMS system
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | state 0 = 'Off', 1 = 'On'

### `0x09` - key state
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | state 0 = 'Off', 1 = 'On'

### `0xb` - BMS SOC adjusted for voltage
Offset | Length | Contents
------ | :----: | --------
0x00   | 4      | old uAH
0x02   | 1      | old SOC
0x04   | 4      | new uAH
0x05   | 1      | new SOC
0x06   | 2      | low cell mV

### `0xd` - BMS Current Sensor Zeroed
Offset | Length | Contents
------ | :----: | --------
0x00   | 2      | old mV
0x02   | 2      | new mV
0x04   | 1      | corrfact

### `0xe` - BMS unknown
Offset | Length | Contents
------ | :----: | --------
0x00   | 3      | ???

### `0x10` - BMS Hibernate
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | state 0 = 'Exiting', 1 = 'Entering'

### `0x11` - BMS Chassis Isolation Fault
Offset | Length | Contents
------ | :----: | --------
0x00   | 4      | ohms to cell
0x04   | 1      | cell

### `0x12` - BMS Reflash
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | Rev
0x02   | 20     | Built date time string

### `0x13` - BMS Changed CAN Node ID
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | old
0x01   | 1      | new

### `0x15` - BMS Contactor
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | state 0 = 'Contactor was Opened 1 = 'Contactor was Closed'
0x01   | 4      | Pack mV
0x05   | 4      | Switched mV
0x09   | 4      | Dischg Cur mA

### `0x16` - BMS Discharge cutback
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | cut % (/255*100.0)

### `0x18` - BMS Contactor drive turned on
Offset | Length | Contents
------ | :----: | --------
0x01   | 4      | Pack mV
0x05   | 4      | Switched mV
0x09   | 1      | Duty cycle %

### `0x1c` - MBB unknown
Offset | Length | Contents
------ | :----: | --------
0x00   | 8      | ???

### `0x1e` - MBB unknown
Offset | Length | Contents
------ | :----: | --------
0x00   | 4      | ???

### `0x1f` - MBB unknown
Offset | Length | Contents
------ | :----: | --------
0x00   | 4      | ???

### `0x20` - MBB unknown
Offset | Length | Contents
------ | :----: | --------
0x00   | 3      | ???

### `0x26` - MBB unknown
Offset | Length | Contents
------ | :----: | --------
0x00   | 6      | ???

### `0x28` - battery CAN link up
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | module number

### `0x29` - battery CAN link down
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | module number

### `0x2a` - Sevcon CAN link up
*(no additional data)*

### `0x2b` - Sevcon CAN link down
*(no additional data)*

### `0x2c` - Riding / run status
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | pack temp (high)
0x01   | 1      | pack temp (low)
0x02   | 2      | pack state of charge (%)
0x04   | 4      | pack voltage - fixed point, scaling factor 1/1000
0x08   | 1      | motor temp
0x0a   | 1      | controller temp
0x0c   | 2      | motor RPM
0x10   | 2      | battery current
0x12   | 1      | mods (??)
0x13   | 2      | motor current
0x15   | 2      | ambient temperature
0x17   | 4      | odometer

*note: all temperatures in degrees celcius*

### `0x2d` - Charging status
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | pack temp (high)
0x01   | 1      | pack temp (low)
0x02   | 2      | pack state of charge (%)
0x04   | 4      | pack voltage - fixed point, scaling factor 1/1000
0x08   | 1      | battery current
0x0c   | 1      | mods (??)
0x0d   | 2      | ambient temperature

*note: all temperatures in degrees celcius*

### `0x2f` - sevcon status
Offset | Length | Contents
------ | :----: | --------
0x00   | 2      | error code
0x02   | 2      | sevcon error code
0x04   | 1      | error reg
0x05   | 1+     | error data

### `0x30` - charger status
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | Module type
0x01   | 1      | Module state

### `0x31` - MBB BMS Isolation Fault
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | BMS module
0x01   | 3      | unknown

### `0x33` - battery status
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | `0x00`=disconnecting, `0x01`=connecting, `0x02`=registered
0x01   | 1      | module number
0x02   | 4      | module voltage - fixed point, scaling factor 1/1000
0x06   | 4      | maximum system voltage - fixed point, scaling factor 1/1000
0x0a   | 4      | minimum system voltage - fixed point, scaling factor 1/1000

### `0x34` - power state
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | state
0x01   | 1      | `0x01`=key switch, `0x04`=onboard charger

### `0x35` - MBB unknown
Offset | Length | Contents
------ | :----: | --------
0x00   | 5      | ???

### `0x36` - Sevcon power state
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | state

### `0x37` - MBB BT RX buffer overflow detected
Offset | Length | Contents
------ | :----: | --------
0x00   | 3      | unknown

### `0x38` - bluetooth state
*(no additional data)*

### `0x39` - battery discharge current limited
Offset | Length | Contents
------ | :----: | --------
0x00   | 2      | discharge current

### `0x3a` - Low Chassis isolation
Offset | Length | Contents
------ | :----: | --------
0x00   | 4      | kOhms of isolation
0x04   | 1      | Cell affected

### `0x3b` - precharge decay too steep
*(no additional data)*

### `0x3c` - disarmed status
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | pack temp (high)
0x01   | 1      | pack temp (low)
0x02   | 2      | pack state of charge (%)
0x04   | 4      | pack voltage - fixed point, scaling factor 1/1000
0x08   | 2      | motor temp
0x0a   | 2      | controller temp
0x0c   | 2      | motor RPM
0x10   | 2      | battery current
0x12   | 1      | mods (??)
0x13   | 2      | motor current
0x15   | 2      | ambient temperature
0x17   | 4      | odometer

### `0x3d` - battery module contactor closed
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | module number

### `0x3e` - cell voltages
Offset | Length | Contents
------ | :----: | --------
0x00   | 2      | cell 1 mV
0x02   | 2      | cell 2 mV
0x04   | 2      | cell 3 mV
0x06   | 2      | cell 4 mV
0x08   | 2      | cell 5 mV
0x0a   | 2      | cell 6 mV
0x0c   | 2      | cell 7 mV
0x10   | 2      | cell 8 mV
0x12   | 2      | cell 9 mV
0x14   | 2      | cell 10 mV
0x16   | 2      | cell 11 mV
0x18   | 2      | cell 12 mV
0x1a   | 2      | cell 13 mV
0x1c   | 2      | cell 14 mV
0x1e   | 2      | cell 15 mV
0x20   | 2      | cell 16 mV
0x22   | 2      | cell 17 mV
0x24   | 2      | cell 18 mV
0x26   | 2      | cell 19 mV
0x28   | 2      | cell 20 mV
0x2a   | 2      | cell 21 mV
0x2c   | 2      | cell 22 mV
0x2e   | 2      | cell 23 mV
0x30   | 2      | cell 24 mV
0x32   | 2      | cell 25 mV
0x34   | 2      | cell 26 mV
0x36   | 2      | cell 27 mV
0x38   | 2      | cell 28 mV

### `0x51` (Type 81) - Vehicle State Telemetry
*Added in 2025+ firmware*

Offset | Length | Contents
------ | :----: | --------
0x00   | 68     | Vehicle state telemetry data (68 bytes total)

**Decoded structure (little-endian):**
- Bytes 36-39: Vehicle state string ("TOP", "RUN", "STOP", "UN", etc.)
- Bytes 0-3: Odometer reading (meters)
- Bytes 4-7: SOC raw value
- Bytes 8-11: Ambient temperature raw value
- Bytes 12-15: Temperature sensor 1 (°C)
- Bytes 16-19: Temperature sensor 2 (°C)  
- Bytes 20-23: Temperature sensor 3 (°C)
- Bytes 24-27: Temperature sensor 4 (°C)
- Remaining bytes: Additional telemetry values (under investigation)

### `0x54` (Type 84) - Sensor Data
*Added in 2025+ firmware*

Offset | Length | Contents
------ | :----: | --------
0x00   | 22     | Sensor telemetry data (22 bytes total)

**Decoded structure (little-endian):**
- Bytes 0-3: Odometer reading (meters)
- Bytes 4-7: Sensor reading 1
- Bytes 8-11: Sensor reading 2
- Bytes 12-15: Sensor reading 3
- Bytes 16-19: Sensor reading 4
- Bytes 20-21: Status flags (uint16)

### `0xfb` (Type 251) - System Information
*Added in 2025+ firmware*

Offset | Length | Contents
------ | :----: | --------
0x00   | ~110   | System information including VIN, serial numbers, firmware versions

**Contains:**
- MBB identifier
- VIN number (17 characters)
- Serial numbers
- Firmware version strings
- System configuration data

### `0xfd` - debug string
Offset | Length     | Contents
------ | :--------: | --------
0x00   | *variable* | message text (ascii)
