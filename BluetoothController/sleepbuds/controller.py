"""
Bose Sleepbuds II Controller.

Provides high-level control: play/stop sounds, enable/disable phone-free mode,
set volume, and manage playback state.
"""

import asyncio
import struct
import logging
from typing import Optional, Callable

from bleak import BleakClient

from .protocol import (
    Service, Characteristic, ResponseCode,
    PHONE_FREE_ENABLE, PHONE_FREE_DISABLE,
    AUDIO_FRAME_SIZE, DEFAULT_TRACK_ID,
    AudioOffset, SettingsBit, ALL_SOUNDS,
    CONNECTION_PARAM_OPCODE,
    DEFAULT_FAST_INTERVAL, DEFAULT_STANDARD_INTERVAL,
    DEFAULT_LATENCY, DEFAULT_SUPERVISION_TIMEOUT,
)
from .scanner import BudState, read_device_state, _parse_settings, _parse_audio
from .protocol import TRACK_ID_TO_NAME

logger = logging.getLogger(__name__)


class SleepbudsController:
    """High-level controller for Bose Sleepbuds II."""

    def __init__(self, client: BleakClient):
        self.client = client
        self._notification_callbacks: dict[str, Callable] = {}
        self._control_point_response: Optional[bytes] = None
        self._control_point_event = asyncio.Event()

    async def setup_notifications(self):
        """Subscribe to notifications on key characteristics."""
        # Settings notifications
        try:
            await self.client.start_notify(
                Characteristic.SETTINGS,
                self._on_settings_notify,
            )
            logger.info("Subscribed to Settings notifications")
        except Exception as e:
            logger.debug("Could not subscribe to Settings: %s", e)

        # Audio data notifications
        try:
            await self.client.start_notify(
                Characteristic.AUDIO_DATA,
                self._on_audio_notify,
            )
            logger.info("Subscribed to Audio notifications")
        except Exception as e:
            logger.debug("Could not subscribe to Audio: %s", e)

        # Control point notifications (for command responses)
        try:
            await self.client.start_notify(
                Characteristic.CONTROL_POINT,
                self._on_control_point_notify,
            )
            logger.info("Subscribed to Control Point notifications")
        except Exception as e:
            logger.debug("Could not subscribe to Control Point: %s", e)

    def _on_settings_notify(self, sender, data: bytearray):
        settings = _parse_settings(bytes(data))
        logger.info("Settings notification: %s (raw: %s)", settings, data.hex())

    def _on_audio_notify(self, sender, data: bytearray):
        audio = _parse_audio(bytes(data))
        logger.info("Audio notification: %s (raw: %s)", audio, data.hex())

    def _on_control_point_notify(self, sender, data: bytearray):
        logger.info("Control Point notification: %s", data.hex())
        self._control_point_response = bytes(data)
        self._control_point_event.set()

    async def _wait_for_control_response(self, timeout: float = 5.0) -> Optional[bytes]:
        """Wait for a Control Point notification response."""
        self._control_point_event.clear()
        self._control_point_response = None
        try:
            await asyncio.wait_for(self._control_point_event.wait(), timeout)
            return self._control_point_response
        except asyncio.TimeoutError:
            logger.warning("Timed out waiting for control point response")
            return None

    # =========================================================================
    # Full Post-Connection Handshake (mimics APK initialization)
    # =========================================================================

    async def full_handshake(self):
        """
        Perform the full post-connection handshake that the APK does.

        The APK runs 11 initialization steps after connecting to each bud.
        This may be required before the bud will accept play commands.
        """
        logger.info("Starting full handshake...")

        # Step 1: Enable notifications (Control Point + Settings)
        await self.setup_notifications()

        # Step 2: Read serial number
        try:
            data = await self.client.read_gatt_char(Characteristic.SERIAL_NUMBER)
            serial = data.decode("utf-8", errors="replace").rstrip("\x00")
            logger.info("Handshake: Serial = %s", serial)
        except Exception as e:
            logger.debug("Handshake: Could not read serial: %s", e)

        # Step 3: Read firmware version
        try:
            data = await self.client.read_gatt_char(Characteristic.FIRMWARE_VERSION)
            fw = data.decode("utf-8", errors="replace").rstrip("\x00")
            logger.info("Handshake: Firmware = %s", fw)
        except Exception as e:
            logger.debug("Handshake: Could not read firmware: %s", e)

        # Step 5: Query device properties via TUMBLE (Control Point)
        try:
            resp = await self.send_control_command(
                bytes([0x01, 0x03]),  # QUERY_DEVICE, sub_module=TUMBLE
                b"",
                wait_response=True,
                timeout=3.0,
            )
            if resp:
                logger.info("Handshake: QUERY_DEVICE response: %s", resp.hex())
        except Exception as e:
            logger.debug("Handshake: QUERY_DEVICE failed: %s", e)

        # Step 6: Read Settings characteristic
        try:
            data = await self.client.read_gatt_char(Characteristic.SETTINGS)
            logger.info("Handshake: Settings = %s", data.hex())
        except Exception as e:
            logger.debug("Handshake: Could not read settings: %s", e)

        # Step 8: Read Sounds characteristic
        try:
            data = await self.client.read_gatt_char(Characteristic.SOUNDS)
            logger.info("Handshake: Sounds = %s (%d bytes)", data.hex(), len(data))
        except Exception as e:
            logger.debug("Handshake: Could not read sounds: %s", e)

        # Step 10: Read Audio Data characteristic (CRITICAL per APK)
        try:
            data = await self.client.read_gatt_char(Characteristic.AUDIO_DATA)
            logger.info("Handshake: Audio = %s", data.hex())
        except Exception as e:
            logger.debug("Handshake: Could not read audio: %s", e)

        # Step 11: Set connection interval
        try:
            await self.set_connection_interval(DEFAULT_FAST_INTERVAL)
            logger.info("Handshake: Connection interval set")
        except Exception as e:
            logger.debug("Handshake: Could not set connection interval: %s", e)

        logger.info("Handshake complete.")

    # =========================================================================
    # Phone-Free Mode
    # =========================================================================

    async def enable_phone_free(self) -> bool:
        """
        Enable phone-free mode.

        The buds will continue playing the currently selected sound
        without needing an app connection.

        Returns True if the write succeeded.
        """
        logger.info("Enabling phone-free mode...")
        try:
            await self.client.write_gatt_char(
                Characteristic.SETTINGS,
                PHONE_FREE_ENABLE,
                response=True,
            )
            logger.info("Phone-free enable command sent")

            # Read back to verify
            await asyncio.sleep(0.5)
            data = await self.client.read_gatt_char(Characteristic.SETTINGS)
            enabled = bool(data[0] & SettingsBit.PHONE_FREE) if data else False
            logger.info("Phone-free mode confirmed: %s", enabled)
            return enabled
        except Exception as e:
            logger.error("Failed to enable phone-free mode: %s", e)
            return False

    async def disable_phone_free(self) -> bool:
        """Disable phone-free mode."""
        logger.info("Disabling phone-free mode...")
        try:
            await self.client.write_gatt_char(
                Characteristic.SETTINGS,
                PHONE_FREE_DISABLE,
                response=True,
            )
            logger.info("Phone-free disable command sent")

            await asyncio.sleep(0.5)
            data = await self.client.read_gatt_char(Characteristic.SETTINGS)
            disabled = not bool(data[0] & SettingsBit.PHONE_FREE) if data else False
            logger.info("Phone-free mode disabled: %s", disabled)
            return disabled
        except Exception as e:
            logger.error("Failed to disable phone-free mode: %s", e)
            return False

    # =========================================================================
    # Audio Playback Control
    # =========================================================================

    def _build_audio_frame(
        self,
        playing: bool,
        volume: int,
        track_id: int,
        timeout_secs: int = 0,
        remaining_secs: int = 0,
        fade_in_secs: int = 0,
        fade_out_secs: int = 0,
    ) -> bytes:
        """Build a 12-byte audio data frame (V2 format)."""
        frame = bytearray(AUDIO_FRAME_SIZE)
        frame[AudioOffset.STATE] = 0x01 if playing else 0x00
        frame[AudioOffset.VOLUME] = max(0, min(255, volume))
        struct.pack_into("<H", frame, AudioOffset.TRACK_ID, track_id & 0xFFFF)
        struct.pack_into("<H", frame, AudioOffset.TIMEOUT, timeout_secs)
        struct.pack_into("<H", frame, AudioOffset.REMAINING, remaining_secs)
        struct.pack_into("<H", frame, AudioOffset.FADE_IN, fade_in_secs)
        struct.pack_into("<H", frame, AudioOffset.FADE_OUT, fade_out_secs)
        return bytes(frame)

    async def play_sound(
        self,
        sound_id: int,
        volume: int = 50,
        timeout_secs: int = 0,
    ) -> bool:
        """
        Play a sound on the buds.

        Args:
            sound_id: Sound file ID (see protocol.SOUNDS_KINGSLEY / SOUNDS_DROWSY).
                      This must be a file_id (large number), not a track_id.
            volume: Volume level 0-255
            timeout_secs: Auto-stop after N seconds (0 = play indefinitely)

        Returns True if write succeeded.
        """
        name = ALL_SOUNDS.get(sound_id, "Unknown")
        logger.info("Playing sound %d (%s) at volume %d", sound_id, name, volume)

        frame = self._build_audio_frame(
            playing=True,
            volume=volume,
            track_id=sound_id,
            timeout_secs=timeout_secs,
            remaining_secs=timeout_secs,
        )
        logger.debug("Audio frame (12 bytes): %s", frame.hex())

        try:
            await self._write_audio_data(frame)
            logger.info("Play command sent")
            return True
        except Exception as e:
            logger.error("Failed to play sound: %s", e)
            return False

    async def _write_audio_data(self, frame: bytes) -> None:
        """Write audio data, trying write-with-response first, then without."""
        try:
            await self.client.write_gatt_char(
                Characteristic.AUDIO_DATA,
                frame,
                response=True,
            )
            logger.debug("Audio write succeeded (with-response)")
        except Exception:
            logger.debug("Write-with-response failed, trying without-response")
            await self.client.write_gatt_char(
                Characteristic.AUDIO_DATA,
                frame,
                response=False,
            )
            logger.debug("Audio write succeeded (without-response)")

    async def stop_playback(self) -> bool:
        """Stop current playback."""
        logger.info("Stopping playback...")
        frame = self._build_audio_frame(
            playing=False,
            volume=0,
            track_id=DEFAULT_TRACK_ID,
            timeout_secs=0,
            remaining_secs=0,
        )
        try:
            await self._write_audio_data(frame)
            logger.info("Stop command sent")
            return True
        except Exception as e:
            logger.error("Failed to stop playback: %s", e)
            return False

    async def set_volume(self, volume: int) -> bool:
        """
        Set playback volume.

        Reads current audio state, changes volume, writes back.
        """
        logger.info("Setting volume to %d", volume)
        try:
            data = await self.client.read_gatt_char(Characteristic.AUDIO_DATA)
            if len(data) < AUDIO_FRAME_SIZE:
                logger.error("Audio data too short: %d bytes", len(data))
                return False

            frame = bytearray(data[:AUDIO_FRAME_SIZE])
            frame[AudioOffset.VOLUME] = max(0, min(255, volume))

            await self._write_audio_data(bytes(frame))
            logger.info("Volume set to %d", volume)
            return True
        except Exception as e:
            logger.error("Failed to set volume: %s", e)
            return False

    # =========================================================================
    # Control Point Commands
    # =========================================================================

    async def send_control_command(
        self,
        command: bytes,
        payload: bytes = b"",
        wait_response: bool = True,
        timeout: float = 5.0,
    ) -> Optional[bytes]:
        """
        Send a command via the Control Point characteristic.

        Args:
            command: 2-byte opcode (e.g., TumbleCmd.QUERY_DEVICE)
            payload: Additional payload bytes
            wait_response: Wait for notification response
            timeout: Response timeout in seconds

        Returns response bytes or None.
        """
        data = command + payload
        logger.debug("Sending control command: %s", data.hex())

        if wait_response:
            self._control_point_event.clear()
            self._control_point_response = None

        # Try V3 service first (Update service), fall back to V2 (Drowsy)
        written = False
        for service_uuid in [Service.UPDATE_V3, Service.DROWSY]:
            try:
                await self.client.write_gatt_char(
                    Characteristic.CONTROL_POINT,
                    data,
                    response=True,
                )
                written = True
                break
            except Exception:
                continue

        if not written:
            logger.error("Failed to write control command")
            return None

        if wait_response:
            return await self._wait_for_control_response(timeout)
        return b""

    async def set_connection_interval(self, interval: int) -> bool:
        """Request a BLE connection interval change."""
        import struct as s
        payload = s.pack(
            "<HHHH",
            interval,  # min
            interval,  # max
            DEFAULT_LATENCY,
            DEFAULT_SUPERVISION_TIMEOUT // 10,  # units of 10ms
        )
        resp = await self.send_control_command(
            CONNECTION_PARAM_OPCODE,
            payload,
            wait_response=True,
            timeout=3.0,
        )
        if resp and len(resp) > 0:
            code = resp[0]
            logger.info("Connection interval response: 0x%02X", code)
            return code == ResponseCode.SUCCESS or code == ResponseCode.PARAMS_ALREADY_MET
        return False

    # =========================================================================
    # Alarm (BBA - Bud Based Alarm, sub-module 0x02)
    # =========================================================================

    async def set_alarm(
        self,
        seconds_from_now: int,
        sound_id: int,
        volume: int = 75,
        duration_secs: int = 300,
        fade_in_secs: int = 30,
        fade_out_secs: int = 0,
    ) -> bool:
        """
        Set a bud-based alarm.

        Args:
            seconds_from_now: Seconds until alarm fires (max 64800 = 18 hours)
            sound_id: File ID of the alarm sound
            volume: Alarm volume 0-255 (default 75)
            duration_secs: How long alarm plays (default 300 = 5 min)
            fade_in_secs: Fade-in duration (default 30)
            fade_out_secs: Fade-out duration (default 0)

        Returns True if alarm was set.
        """
        payload = struct.pack(
            "<HBHHHH",
            sound_id & 0xFFFF,
            volume & 0xFF,
            seconds_from_now & 0xFFFF,
            duration_secs & 0xFFFF,
            fade_in_secs & 0xFFFF,
            fade_out_secs & 0xFFFF,
        )
        logger.info("Setting alarm: %ds from now, sound=%d, vol=%d",
                     seconds_from_now, sound_id, volume)
        resp = await self.send_control_command(
            bytes([0x01, 0x02]),  # WRITE_BBA opcode + alarm sub-module
            payload,
            wait_response=True,
            timeout=5.0,
        )
        if resp and len(resp) > 0:
            code = resp[0]
            print(f"    Alarm response: 0x{code:02X} (full: {resp.hex()})")
            return code == ResponseCode.SUCCESS
        return False

    async def cancel_alarm(self) -> bool:
        """Cancel the current bud-based alarm. Returns True if cancelled."""
        logger.info("Cancelling alarm...")
        resp = await self.send_control_command(
            bytes([0x02, 0x02]),  # CANCEL_BBA opcode + alarm sub-module
            b"",
            wait_response=True,
            timeout=5.0,
        )
        if resp and len(resp) > 0:
            code = resp[0]
            return code == ResponseCode.SUCCESS
        return False

    async def read_alarm(self) -> Optional[bytes]:
        """Read the current alarm state from the buds."""
        resp = await self.send_control_command(
            bytes([0x00, 0x02]),  # READ_BBA opcode + alarm sub-module
            b"",
            wait_response=True,
            timeout=5.0,
        )
        return resp

    # =========================================================================
    # Bud Activation (ACTIVATE_GROUP command)
    # =========================================================================

    async def activate_bud(self, major: int = 11, minor: int = 9, patch: int = 0) -> bool:
        """
        Send ACTIVATE_GROUP command to activate this bud.

        Brand new buds may need this activation before they will produce
        audio. The Bose Sleep app sends this during initial setup.

        Args:
            major, minor, patch: Firmware version (default 11.9.0)

        Returns True if activation succeeded.
        """
        # Control mask bits:
        #   0x01 = activate bud
        #   0x02 = activate case
        #   0x04 = activate radio
        #   0x07 = activate all
        control_mask = 0x07  # Activate bud + case + radio

        payload = bytes([control_mask, major, minor, patch])
        logger.info("Activating bud: mask=0x%02X version=%d.%d.%d",
                     control_mask, major, minor, patch)

        resp = await self.send_control_command(
            bytes([0x08, 0x01]),  # ACTIVATE_GROUP opcode + sub_module
            payload,
            wait_response=True,
            timeout=5.0,
        )

        if resp and len(resp) > 0:
            code = resp[0]
            print(f"    Activate response: 0x{code:02X} (full: {resp.hex()})")
            if code == ResponseCode.SUCCESS:
                return True
            else:
                # Try with just bud activation (0x01)
                if control_mask != 0x01:
                    print("    Retrying with bud-only mask (0x01)...")
                    payload2 = bytes([0x01, major, minor, patch])
                    resp2 = await self.send_control_command(
                        bytes([0x08, 0x01]),
                        payload2,
                        wait_response=True,
                        timeout=5.0,
                    )
                    if resp2 and len(resp2) > 0:
                        code2 = resp2[0]
                        print(f"    Retry response: 0x{code2:02X} (full: {resp2.hex()})")
                        return code2 == ResponseCode.SUCCESS
                return False
        else:
            print("    No response to activation command (timeout)")
            return False

    # =========================================================================
    # PlayInFuture (V9 synchronized playback for both buds)
    # =========================================================================

    async def read_bud_clock(self) -> Optional[bytes]:
        """Read the bud's internal clock via the Sync characteristic."""
        try:
            data = await self.client.read_gatt_char(Characteristic.SYNC)
            logger.info("Bud clock raw: %s", data.hex() if data else "none")
            return bytes(data) if data else None
        except Exception as e:
            logger.debug("Could not read bud clock: %s", e)
            return None

    def _build_play_in_future(
        self,
        file_id: int,
        volume: int,
        timeout_secs: int = 0,
        fade_in_secs: int = 0,
        fade_out_secs: int = 0,
        clock_data: Optional[bytes] = None,
    ) -> bytes:
        """
        Build an 18-byte PlayInFuture command for V9 firmware.

        This is sent to the Control Point characteristic on the primary
        (Left) bud, which relays to the secondary (Right) bud.

        Format:
            Byte 0:     0x01 (PLAY_IN_FUTURE opcode)
            Byte 1:     0xBB (audio sub-module)
            Bytes 2-8:  BudTime (7 bytes: timing for sync)
            Bytes 9-10: file_id (uint16 LE)
            Byte 11:    volume
            Bytes 12-13: masking_timeout (int16 LE, seconds)
            Bytes 14-15: fade_in (int16 LE, seconds)
            Bytes 16-17: fade_out (int16 LE, seconds)
        """
        cmd = bytearray(18)
        cmd[0] = 0x01  # PLAY_IN_FUTURE opcode
        cmd[1] = 0xBB  # Audio sub-module

        # BudTime format (7 bytes from Sync characteristic after type byte):
        #   [hours, minutes, seconds, ms_lo, ms_hi, us_lo, us_hi]
        # PlayInFuture copies BudTime bytes 1-6 (skip hours) into cmd[2..7]:
        #   cmd[2] = minutes, cmd[3] = seconds,
        #   cmd[4:6] = milliseconds (uint16 LE), cmd[6:8] = microseconds (uint16 LE)
        # Then add ~750ms offset for synchronization delay.
        if clock_data and len(clock_data) >= 8:
            # Sync characteristic: [type, hours, min, sec, ms_lo, ms_hi, us_lo, us_hi]
            # BudTime bytes[1..6] = clock_data[2..7] (skip type AND hours)
            minutes = clock_data[2]
            seconds = clock_data[3]
            ms = clock_data[4] | (clock_data[5] << 8)
            us = clock_data[6] | (clock_data[7] << 8)

            # Add 750ms offset
            ms += 750
            if ms >= 1000:
                seconds += ms // 1000
                ms = ms % 1000
            if seconds >= 60:
                minutes += seconds // 60
                seconds = seconds % 60
            if minutes >= 60:
                minutes = minutes % 60

            cmd[2] = minutes & 0xFF
            cmd[3] = seconds & 0xFF
            struct.pack_into("<H", cmd, 4, ms)
            struct.pack_into("<H", cmd, 6, us)
            # cmd[8] stays 0x00 (padding before file_id at byte 9)

        struct.pack_into("<H", cmd, 9, file_id & 0xFFFF)
        cmd[11] = max(0, min(255, volume))
        struct.pack_into("<h", cmd, 12, timeout_secs)
        struct.pack_into("<h", cmd, 14, fade_in_secs)
        struct.pack_into("<h", cmd, 16, fade_out_secs)
        return bytes(cmd)

    async def play_synced(
        self,
        file_id: int,
        volume: int = 50,
        timeout_secs: int = 0,
    ) -> bool:
        """
        Play a sound on BOTH buds using PlayInFuture via Control Point.

        Must be called on the primary (Left) bud. The primary bud
        relays the command to the secondary (Right) bud.

        Args:
            file_id: Sound file ID (must be file_id, not track_id)
            volume: Volume 0-255
            timeout_secs: Auto-stop (0=indefinite)
        """
        name = ALL_SOUNDS.get(file_id, "Unknown")
        logger.info("PlayInFuture: %d (%s) vol=%d", file_id, name, volume)

        # Read bud's clock for timing sync
        clock_data = await self.read_bud_clock()

        cmd = self._build_play_in_future(
            file_id=file_id,
            volume=volume,
            timeout_secs=timeout_secs,
            clock_data=clock_data,
        )
        logger.info("PlayInFuture command (18 bytes): %s", cmd.hex())

        # Send via Control Point with response
        resp = await self.send_control_command(
            cmd[:2],       # opcode bytes
            cmd[2:],       # payload
            wait_response=True,
            timeout=5.0,
        )

        if resp and len(resp) > 0:
            code = resp[0]
            logger.info("PlayInFuture response: 0x%02X", code)
            if code == ResponseCode.SUCCESS:
                logger.info("PlayInFuture accepted")
                return True
            else:
                logger.warning("PlayInFuture returned code 0x%02X", code)
                return False
        else:
            logger.warning("No PlayInFuture response")
            return False

    async def cancel_play_in_future(self) -> bool:
        """Cancel a pending PlayInFuture command."""
        logger.info("Cancelling PlayInFuture...")
        resp = await self.send_control_command(
            bytes([0x02, 0xBB]),  # CANCEL_PLAY_IN_FUTURE
            b"",
            wait_response=True,
            timeout=3.0,
        )
        if resp and len(resp) > 0:
            return resp[0] == ResponseCode.SUCCESS
        return False

    # =========================================================================
    # State Reading
    # =========================================================================

    async def read_state(self) -> BudState:
        """Read full device state."""
        return await read_device_state(self.client)

    async def read_battery(self) -> int:
        """Read battery level (0-100)."""
        try:
            data = await self.client.read_gatt_char(Characteristic.BATTERY_LEVEL)
            return data[0] if data else -1
        except Exception:
            return -1

    async def is_phone_free_enabled(self) -> bool:
        """Check if phone-free mode is currently enabled."""
        try:
            data = await self.client.read_gatt_char(Characteristic.SETTINGS)
            return bool(data[0] & SettingsBit.PHONE_FREE) if data else False
        except Exception:
            return False
