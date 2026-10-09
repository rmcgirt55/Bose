"""
Bose Sleepbuds II BLE Protocol Constants.

All UUIDs, command bytes, and data structures reverse-engineered
from the Bose Sleep APK v3.0.11.
"""

from enum import IntEnum
from uuid import UUID


# =============================================================================
# BLE Service UUIDs
# =============================================================================

class Service:
    DEVICE_INFO = UUID("0000180a-0000-1000-8000-00805f9b34fb")
    BATTERY = UUID("0000180f-0000-1000-8000-00805f9b34fb")
    DROWSY = UUID("0000fe21-0000-1000-8000-00805f9b34fb")  # Main proprietary
    SETTINGS = UUID("0000fe21-0000-1000-8000-00805f9b34fb")  # Same as DROWSY
    AUDIO = UUID("fdc34961-888e-4a93-abf1-b3490d8b231b")
    OTA = UUID("4b25c5ad-978d-467c-b598-155247b9e774")
    UPDATE_V3 = UUID("c76e1666-e7c3-4432-a995-049d09cb9e90")


# =============================================================================
# BLE Characteristic UUIDs
# =============================================================================

class Characteristic:
    # Device Info
    FIRMWARE_VERSION = UUID("00002a26-0000-1000-8000-00805f9b34fb")
    SERIAL_NUMBER = UUID("00002a25-0000-1000-8000-00805f9b34fb")
    DEVICE_NAME = UUID("00002a00-0000-1000-8000-00805f9b34fb")

    # Battery
    BATTERY_LEVEL = UUID("00002a19-0000-1000-8000-00805f9b34fb")

    # Drowsy/Settings service
    SETTINGS = UUID("5e500c28-33db-4d0a-afc8-c9b6df4269bf")
    CONTROL_POINT = UUID("e81359e0-9f35-483a-a46f-7c6d686daa06")

    # Audio service
    AUDIO_DATA = UUID("1cbd5f8f-6c18-4309-9354-703e7d42a74e")
    SOUNDS = UUID("12a3d434-c7de-4760-88c0-4f743ea55e98")
    SYNC = UUID("1cbe5f90-6c19-430a-9355-703f7d43a74f")

    # OTA service
    OTA_COMMAND = UUID("7ee6acc4-884c-4d97-ab3a-26c99459c030")
    OTA_STATE = UUID("b7488798-dd0d-40d2-8372-96524b495b47")
    OTA_IMAGE_BLOCK = UUID("47bbaf32-049e-44d2-ac33-a131fc73f8bd")
    OTA_STATUS = UUID("2c54d3b1-aedc-47ec-82c7-27bb68b6a0e6")
    OTA_IMAGE_INFO = UUID("43246004-5173-4f20-96e2-d594472d828b")

    # V3 Update service
    UPDATE_BINARY_PACKET = UUID("86d60053-6f4a-409c-9a81-93f16c4e15a0")

    # Notification descriptor
    CCCD = UUID("00002902-0000-1000-8000-00805f9b34fb")


# =============================================================================
# Phone-Free Mode Commands
# =============================================================================

PHONE_FREE_ENABLE = bytes.fromhex("01000000")
PHONE_FREE_DISABLE = bytes.fromhex("00000000")


# =============================================================================
# Settings Characteristic Bit Masks (byte 0)
# =============================================================================

class SettingsBit:
    PHONE_FREE = 0x01      # Bit 0
    RENAMED = 0x02         # Bit 1
    BUD_IN_CASE = 0x10     # Bit 4
    NEW_LOGS = 0x20        # Bit 5


# =============================================================================
# Audio Characteristic Layout (12 bytes, little-endian)
#
# V1 (Drowsy) used 8 bytes. V2 (Kingsley / firmware 11.x) uses 12 bytes
# with fade_in and fade_out fields. The firmware rejects 8-byte writes.
# =============================================================================

AUDIO_FRAME_SIZE = 12
DEFAULT_TRACK_ID = 65440  # 0xFFA0 - no sound selected


class AudioOffset:
    STATE = 0       # 1 byte: bit 0 = playing
    VOLUME = 1      # 1 byte: 0-255
    TRACK_ID = 2    # 2 bytes: little-endian short
    TIMEOUT = 4     # 2 bytes: seconds (0 = indefinite)
    REMAINING = 6   # 2 bytes: seconds remaining
    FADE_IN = 8     # 2 bytes: fade-in duration in seconds
    FADE_OUT = 10   # 2 bytes: fade-out duration in seconds


# =============================================================================
# Control Point Response Codes
# =============================================================================

class ResponseCode(IntEnum):
    SUCCESS = 0x00
    NOT_SUPPORTED = 0x02
    INVALID_PARAM = 0x03
    PARAM_UPDATE_FAIL = 0x04
    REQUEST_REJECTED = 0x05
    TIMEOUT = 0x06
    ALREADY_BONDED = 0x07
    PARAMS_ALREADY_MET = 0x08
    TIMING_ERROR = 0x09
    RESPONSE_BUSY = 0x0C
    GENERAL_FAILURE = 0xFF


# =============================================================================
# TUMBLE Protocol Commands (SubModule 3)
# =============================================================================

class TumbleCmd:
    QUERY_DEVICE = bytes([1, 3])
    FILE_PROPERTIES = bytes([2, 3])
    CREATE_SOUND = bytes([3, 3])
    DELETE_FILE = bytes([4, 3])
    START_TRANSFER = bytes([5, 3])
    QUERY_STATE = bytes([6, 3])
    FLOW_CONTROL = bytes([7, 3])
    CANCEL = bytes([8, 3])
    CONFIRM_CLUSTER = bytes([9, 3])
    END = bytes([10, 3])
    CREATE_FILE = bytes([11, 3])


class TumbleStatus(IntEnum):
    SUCCESS = 0x00
    INVALID_PARAM = 0x03
    PARAMS_ALREADY_MET = 0x08
    INSUFFICIENT_RESOURCES = 0x80
    TIMEOUT = 0x84
    SHORT_PACKETS = 0x85
    BLOCK_COMPLETE = 0x86
    BLOCK_TIMEOUT = 0x87
    HAL_ERROR = 0x87
    TOO_MANY_BYTES = 0x8C
    BAD_CRC = 0x8D
    FILE_NOT_FOUND = 0x90
    SOUND_OPEN = 0x91
    SOUND_PROTECTED = 0x92
    FILE_COMPLETE = 0x93
    CLUSTER_COMPLETE = 0xCF
    FAIL = 0xFF


class TumbleState(IntEnum):
    IDLE = 1
    RECEIVING = 2
    READY = 3
    VERIFY = 4
    STANDBY = 5


# =============================================================================
# TUMBLE Protocol Defaults
# =============================================================================

TUMBLE_BYTES_PER_CLUSTER = 32768
TUMBLE_PACKETS_PER_BLOCK = 2
TUMBLE_BYTES_PER_PACKET = 16
TUMBLE_BLOCK_TIMEOUT = 10
TUMBLE_CONNECTION_INTERVAL_KINGSLEY = 15
TUMBLE_CONNECTION_INTERVAL_DROWSY = 30


# =============================================================================
# Other Command OpCodes
# =============================================================================

class SystemCmd:
    """System commands (SubModule 0)."""
    SYSTEM_FW_VERSION = bytes([61, 0])
    RADIO_FW_VERSION = bytes([58, 0])
    CASE_FW_VERSION = bytes([59, 0])
    FW_UPDATE_STATUS = bytes([60, 0])


class AlarmCmd:
    """Alarm commands (SubModule 2)."""
    READ_BBA_ALARM = bytes([0, 2])
    WRITE_BBA_ALARM = bytes([1, 2])
    CANCEL_BBA_ALARM = bytes([2, 2])


class SyncCmd:
    """Sync commands - single byte written to Sync characteristic."""
    BEGIN_SYNC = 0x54
    SYNC_OFFSET = 0x55
    SYNC_CLOCK = 0x7F
    TEST_OFFSET = 0x5F
    READ_CLOCK = 0x00
    READ_DRIFT = 0x80


# =============================================================================
# Connection Parameter Change
# =============================================================================

CONNECTION_PARAM_OPCODE = bytes([0xD0, 0x00])  # V3 format
DEFAULT_STANDARD_INTERVAL = 120
DEFAULT_FAST_INTERVAL = 16
DEFAULT_LATENCY = 0
DEFAULT_SUPERVISION_TIMEOUT = 2000  # ms


# =============================================================================
# File Metadata
# =============================================================================

class FileType(IntEnum):
    SOUND = 0
    PLAYLIST = 1
    FIRMWARE = 2
    UNKNOWN = 255

BYTES_PER_SOUND_ID = 2


# =============================================================================
# Known Sound IDs
#
# The Sleepbuds use TWO ID systems:
#   - "track_id" (aka "id"): Small numbers (26-103) used in the Audio
#     Characteristic to select which sound plays.
#   - "file_id": Larger numbers (3631-58776) used in the Sounds/Files
#     Characteristic to identify which files are stored on the buds.
#
# The Sounds characteristic returns FILE IDs.
# The Audio characteristic uses TRACK IDs.
# =============================================================================

# Track ID -> (name, file_id) for Drowsy variant
SOUNDS_DROWSY = {
    26: ("Rustle", 14373),
    28: ("Shower", 6626),
    30: ("Swell", 16444),
    32: ("Tranquility", 31258),
    34: ("Warm Static", 8278),
    45: ("Boardwalk", 14145),
    46: ("Sunrise", 54674),
    48: ("Celesta", 25574),
    50: ("Short Ding", 37900),       # Alert
    52: ("Glockenspiel", 3631),      # Alert
    54: ("Staccato Bells", 32301),   # Alert
    56: ("Gentle Bells", 28071),     # Alert
    58: ("Xylophone", 9144),         # Alert
}

# Track ID -> (name, file_id) for Kingsley variant
SOUNDS_KINGSLEY = {
    27: ("Rustle", 13198),
    29: ("Shower", 43402),
    31: ("Swell", 10972),
    33: ("Tranquility", 58776),
    35: ("Warm Static", 31567),
    47: ("Sunrise", 54675),
    49: ("Celesta", 25575),
    51: ("Short Ding", 37901),
    53: ("Glockenspiel", 3632),
    55: ("Staccato Bells", 32302),
    57: ("Gentle Bells", 28072),
    59: ("Xylophone", 9145),
    76: ("Rinse", 20000),
    80: ("Fabled", 20004),
    81: ("Starboard", 20005),
    83: ("Gilded Dream", 20007),
    86: ("Outbound", 20010),
    93: ("Ode", 20017),
    94: ("Wanderlust", 20018),
    95: ("Boardwalk", 20019),
    103: ("Windowseat", 20028),
}

# File ID -> (name, track_id) mapping (for parsing Sounds characteristic)
FILE_ID_TO_NAME = {}
FILE_ID_TO_TRACK_ID = {}
TRACK_ID_TO_NAME = {}

for _track_id, (_name, _file_id) in {**SOUNDS_DROWSY, **SOUNDS_KINGSLEY}.items():
    FILE_ID_TO_NAME[_file_id] = _name
    FILE_ID_TO_TRACK_ID[_file_id] = _track_id
    TRACK_ID_TO_NAME[_track_id] = _name

# Combined name lookup (checks both track IDs and file IDs)
ALL_SOUNDS = {**TRACK_ID_TO_NAME, **FILE_ID_TO_NAME}
