"""
Bose Sleepbuds II BLE Scanner & State Reader.

Discovers Sleepbuds via BLE, connects, and reads device state including
battery level, firmware version, loaded sounds, and phone-free mode status.
"""

import asyncio
import struct
import logging
from dataclasses import dataclass, field
from typing import Optional

from bleak import BleakScanner, BleakClient
from bleak.backends.device import BLEDevice

from .protocol import (
    Service, Characteristic, SettingsBit, AudioOffset,
    AUDIO_FRAME_SIZE, DEFAULT_TRACK_ID, ALL_SOUNDS,
    SOUNDS_DROWSY, SOUNDS_KINGSLEY, BYTES_PER_SOUND_ID,
    FILE_ID_TO_NAME, FILE_ID_TO_TRACK_ID,
)

logger = logging.getLogger(__name__)


@dataclass
class BudState:
    """Parsed state of the Sleepbuds."""
    # Device info
    address: str = ""
    name: str = ""
    firmware_version: str = ""
    serial_number: str = ""

    # Settings (from Settings characteristic)
    phone_free_enabled: bool = False
    bud_in_case: bool = False
    has_new_logs: bool = False
    has_been_renamed: bool = False
    battery_from_settings: int = 0

    # Battery (from Battery characteristic)
    battery_level: int = -1

    # Audio state
    is_playing: bool = False
    volume: int = 0
    track_id: int = DEFAULT_TRACK_ID
    masking_timeout_secs: int = 0
    time_remaining_secs: int = 0

    # Files on buds
    sound_ids: list[int] = field(default_factory=list)

    # Hardware variant
    variant: str = "unknown"  # "drowsy" or "kingsley"

    # Raw GATT service map
    services: dict[str, list[str]] = field(default_factory=dict)


def _parse_settings(data: bytes) -> dict:
    """Parse the Settings characteristic bytes."""
    if not data:
        return {}
    byte0 = data[0]
    result = {
        "phone_free_enabled": bool(byte0 & SettingsBit.PHONE_FREE),
        "has_been_renamed": bool(byte0 & SettingsBit.RENAMED),
        "bud_in_case": bool(byte0 & SettingsBit.BUD_IN_CASE),
        "has_new_logs": bool(byte0 & SettingsBit.NEW_LOGS),
    }
    if len(data) > 1:
        result["battery_from_settings"] = data[1]
    return result


def _parse_audio(data: bytes) -> dict:
    """Parse the Audio Data characteristic (12 bytes V2, little-endian)."""
    if len(data) < 8:
        return {}
    is_playing = bool(data[AudioOffset.STATE] & 0x01)
    volume = data[AudioOffset.VOLUME]
    track_id = struct.unpack_from("<H", data, AudioOffset.TRACK_ID)[0]
    timeout = struct.unpack_from("<H", data, AudioOffset.TIMEOUT)[0]
    remaining = struct.unpack_from("<H", data, AudioOffset.REMAINING)[0]
    fade_in = struct.unpack_from("<H", data, AudioOffset.FADE_IN)[0] if len(data) >= 10 else 0
    fade_out = struct.unpack_from("<H", data, AudioOffset.FADE_OUT)[0] if len(data) >= 12 else 0
    return {
        "is_playing": is_playing,
        "volume": volume,
        "track_id": track_id,
        "masking_timeout_secs": timeout,
        "time_remaining_secs": remaining,
        "fade_in_secs": fade_in,
        "fade_out_secs": fade_out,
    }


def _parse_sound_ids(data: bytes) -> list[int]:
    """Parse the Sounds characteristic into a list of sound file IDs."""
    ids = []
    for i in range(0, len(data) - 1, BYTES_PER_SOUND_ID):
        sid = struct.unpack_from("<H", data, i)[0]
        if sid != 0:
            ids.append(sid)
    return ids


def _detect_variant(file_ids: list[int]) -> str:
    """Detect hardware variant from loaded file IDs."""
    drowsy_file_ids = {fid for _, (_, fid) in SOUNDS_DROWSY.items()}
    kingsley_file_ids = {fid for _, (_, fid) in SOUNDS_KINGSLEY.items()}
    drowsy_count = sum(1 for fid in file_ids if fid in drowsy_file_ids)
    kingsley_count = sum(1 for fid in file_ids if fid in kingsley_file_ids)
    if drowsy_count > kingsley_count:
        return "drowsy"
    elif kingsley_count > drowsy_count:
        return "kingsley"
    return "unknown"


async def scan_for_sleepbuds(timeout: float = 10.0) -> list[BLEDevice]:
    """Find Sleepbuds by advertised name or proprietary service UUID."""
    logger.info("Scanning for Bose Sleepbuds (%.1fs)...", timeout)

    bose_service = "0000fe21-0000-1000-8000-00805f9b34fb"

    devices = await BleakScanner.discover(
        timeout=timeout,
        return_adv=True
    )

    found = []

    for address, (device, adv) in devices.items():
        name = device.name or adv.local_name or ""
        name_lower = name.lower()

        services = {
            str(uuid).lower()
            for uuid in adv.service_uuids
        }

        name_match = any(
            word in name_lower
            for word in ("bose", "sleepbud", "drowsy")
        )

        service_match = bose_service in services

        if name_match or service_match:
            logger.info(
                "Found candidate: %s [%s]",
                name or "Unnamed Sleepbud",
                device.address
            )
            found.append(device)

    if not found:
        logger.warning(
            "No Sleepbuds found. Make sure they are out of the case."
        )

    return found



async def read_device_state(client: BleakClient) -> BudState:
    """Read all available state from a connected Sleepbuds device."""
    state = BudState()
    state.address = client.address

    # Enumerate all services
    for svc in client.services:
        chars = []
        for char in svc.characteristics:
            props = ",".join(char.properties)
            chars.append(f"{char.uuid} [{props}]")
        state.services[svc.uuid] = chars

    # Read device name
    try:
        data = await client.read_gatt_char(Characteristic.DEVICE_NAME)
        state.name = data.decode("utf-8", errors="replace").rstrip("\x00")
        logger.info("Device name: %s", state.name)
    except Exception as e:
        logger.debug("Could not read device name: %s", e)

    # Read firmware version
    try:
        data = await client.read_gatt_char(Characteristic.FIRMWARE_VERSION)
        state.firmware_version = data.decode("utf-8", errors="replace").rstrip("\x00")
        logger.info("Firmware: %s", state.firmware_version)
    except Exception as e:
        logger.debug("Could not read firmware version: %s", e)

    # Read serial number
    try:
        data = await client.read_gatt_char(Characteristic.SERIAL_NUMBER)
        state.serial_number = data.decode("utf-8", errors="replace").rstrip("\x00")
        logger.info("Serial: %s", state.serial_number)
    except Exception as e:
        logger.debug("Could not read serial number: %s", e)

    # Read battery level
    try:
        data = await client.read_gatt_char(Characteristic.BATTERY_LEVEL)
        state.battery_level = data[0] if data else -1
        logger.info("Battery: %d%%", state.battery_level)
    except Exception as e:
        logger.debug("Could not read battery level: %s", e)

    # Read settings characteristic (phone-free mode, etc.)
    try:
        data = await client.read_gatt_char(Characteristic.SETTINGS)
        logger.info("Settings raw: %s", data.hex())
        settings = _parse_settings(data)
        state.phone_free_enabled = settings.get("phone_free_enabled", False)
        state.bud_in_case = settings.get("bud_in_case", False)
        state.has_new_logs = settings.get("has_new_logs", False)
        state.has_been_renamed = settings.get("has_been_renamed", False)
        state.battery_from_settings = settings.get("battery_from_settings", 0)
        logger.info("Phone-free mode: %s", state.phone_free_enabled)
        logger.info("Battery (settings): %d%%", state.battery_from_settings)
    except Exception as e:
        logger.debug("Could not read settings: %s", e)

    # Read audio data characteristic (playback state)
    try:
        data = await client.read_gatt_char(Characteristic.AUDIO_DATA)
        logger.info("Audio raw: %s", data.hex())
        audio = _parse_audio(data)
        state.is_playing = audio.get("is_playing", False)
        state.volume = audio.get("volume", 0)
        state.track_id = audio.get("track_id", DEFAULT_TRACK_ID)
        state.masking_timeout_secs = audio.get("masking_timeout_secs", 0)
        state.time_remaining_secs = audio.get("time_remaining_secs", 0)

        track_name = ALL_SOUNDS.get(state.track_id, "Unknown")
        logger.info("Playing: %s, Volume: %d, Track: %d (%s)",
                     state.is_playing, state.volume, state.track_id, track_name)
    except Exception as e:
        logger.debug("Could not read audio data: %s", e)

    # Read sounds on buds
    try:
        data = await client.read_gatt_char(Characteristic.SOUNDS)
        logger.info("Sounds raw: %s", data.hex())
        state.sound_ids = _parse_sound_ids(data)
        state.variant = _detect_variant(state.sound_ids)
        logger.info("Sounds on buds: %s", state.sound_ids)
        logger.info("Hardware variant: %s", state.variant)
        for fid in state.sound_ids:
            name = FILE_ID_TO_NAME.get(fid, f"Unknown({fid})")
            track_id = FILE_ID_TO_TRACK_ID.get(fid, "?")
            logger.info("  File %d: %s (track_id=%s)", fid, name, track_id)
    except Exception as e:
        logger.debug("Could not read sounds: %s", e)

    return state


async def connect_and_read(address: str, timeout: float = 15.0) -> BudState:
    """Connect to a Sleepbuds device and read its full state."""
    logger.info("Connecting to %s...", address)
    async with BleakClient(address, timeout=timeout) as client:
        logger.info("Connected!")
        return await read_device_state(client)


async def discover_and_read(scan_timeout: float = 10.0) -> Optional[BudState]:
    """Scan for Sleepbuds, connect to the first one found, and read state."""
    devices = await scan_for_sleepbuds(timeout=scan_timeout)
    if not devices:
        return None
    device = devices[0]
    return await connect_and_read(device.address)
