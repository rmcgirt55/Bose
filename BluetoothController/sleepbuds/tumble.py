"""
TUMBLE Protocol - Sound file transfer for Bose Sleepbuds II.

Implements the complete TUMBLE file transfer protocol for uploading
audio files to the Sleepbuds over BLE.
"""

import asyncio
import struct
import logging
import zlib
from pathlib import Path
from typing import Optional, Callable

from bleak import BleakClient

from .protocol import (
    Characteristic, Service, ResponseCode,
    TumbleCmd, TumbleStatus, TumbleState,
    TUMBLE_BYTES_PER_CLUSTER, TUMBLE_PACKETS_PER_BLOCK,
    TUMBLE_BYTES_PER_PACKET, TUMBLE_BLOCK_TIMEOUT,
    TUMBLE_CONNECTION_INTERVAL_DROWSY,
    TUMBLE_CONNECTION_INTERVAL_KINGSLEY,
    CONNECTION_PARAM_OPCODE, DEFAULT_LATENCY,
    DEFAULT_SUPERVISION_TIMEOUT,
)

logger = logging.getLogger(__name__)


def _crc32(data: bytes) -> int:
    """Compute CRC-32 of data, return as unsigned 32-bit int."""
    return zlib.crc32(data) & 0xFFFFFFFF


class TumbleTransfer:
    """
    Manages a TUMBLE file transfer to Sleepbuds.

    Usage:
        transfer = TumbleTransfer(client, variant="drowsy")
        await transfer.setup_notifications()
        success = await transfer.upload_sound(
            sound_id=100,
            file_id=10000,
            file_data=raw_pcm_bytes,
            progress_callback=my_callback,
        )
    """

    def __init__(
        self,
        client: BleakClient,
        variant: str = "drowsy",
        bytes_per_cluster: int = TUMBLE_BYTES_PER_CLUSTER,
        packets_per_block: int = TUMBLE_PACKETS_PER_BLOCK,
        bytes_per_packet: int = TUMBLE_BYTES_PER_PACKET,
        block_timeout: int = TUMBLE_BLOCK_TIMEOUT,
    ):
        self.client = client
        self.variant = variant
        self.bytes_per_cluster = bytes_per_cluster
        self.packets_per_block = packets_per_block
        self.bytes_per_packet = bytes_per_packet
        self.block_size = packets_per_block * bytes_per_packet
        self.block_timeout = block_timeout

        self._flow_event = asyncio.Event()
        self._flow_status: Optional[int] = None
        self._response_event = asyncio.Event()
        self._response_data: Optional[bytes] = None
        self._cancelled = False

    async def setup_notifications(self):
        """Subscribe to Control Point notifications for flow control."""
        try:
            await self.client.start_notify(
                Characteristic.CONTROL_POINT,
                self._on_control_point_notify,
            )
            logger.info("TUMBLE: Subscribed to Control Point notifications")
        except Exception as e:
            logger.error("TUMBLE: Failed to subscribe to notifications: %s", e)
            raise

    def _on_control_point_notify(self, sender, data: bytearray):
        """Handle Control Point notifications during transfer."""
        raw = bytes(data)
        logger.debug("TUMBLE notification: %s", raw.hex())

        if len(raw) < 3:
            return

        status_code = raw[0]
        op_code = raw[1]
        sub_module = raw[2]

        # Flow control responses (TUMBLE sub-module = 3)
        if sub_module == 3 and op_code == 7:  # FLOW_CONTROL
            self._flow_status = status_code
            self._flow_event.set()
        else:
            # General command response
            self._response_data = raw
            self._response_event.set()

    async def _send_command(
        self,
        cmd: bytes,
        payload: bytes = b"",
        timeout: float = 10.0,
    ) -> Optional[bytes]:
        """Send a TUMBLE command and wait for response."""
        self._response_event.clear()
        self._response_data = None

        data = cmd + payload
        logger.debug("TUMBLE send: %s", data.hex())

        await self.client.write_gatt_char(
            Characteristic.CONTROL_POINT,
            data,
            response=True,
        )

        try:
            await asyncio.wait_for(self._response_event.wait(), timeout)
            return self._response_data
        except asyncio.TimeoutError:
            logger.warning("TUMBLE: Command timed out")
            return None

    async def _wait_flow_control(self, timeout: float = 30.0) -> Optional[int]:
        """Wait for a flow control notification."""
        self._flow_event.clear()
        self._flow_status = None
        try:
            await asyncio.wait_for(self._flow_event.wait(), timeout)
            return self._flow_status
        except asyncio.TimeoutError:
            logger.warning("TUMBLE: Flow control timed out")
            return None

    async def _set_fast_connection(self) -> bool:
        """Set a fast BLE connection interval for transfer."""
        interval = (TUMBLE_CONNECTION_INTERVAL_KINGSLEY
                    if self.variant == "kingsley"
                    else TUMBLE_CONNECTION_INTERVAL_DROWSY)

        payload = struct.pack(
            "<HHHH",
            interval, interval,
            DEFAULT_LATENCY,
            DEFAULT_SUPERVISION_TIMEOUT // 10,
        )
        resp = await self._send_command(
            CONNECTION_PARAM_OPCODE, payload, timeout=5.0
        )
        if resp and len(resp) > 0:
            return resp[0] in (ResponseCode.SUCCESS, ResponseCode.PARAMS_ALREADY_MET)
        return False

    # =========================================================================
    # TUMBLE Protocol Steps
    # =========================================================================

    async def query_device(self) -> Optional[bytes]:
        """Step 1: Query device properties."""
        logger.info("TUMBLE: Querying device properties...")
        return await self._send_command(TumbleCmd.QUERY_DEVICE)

    async def create_sound(self, sound_id: int, sound_size: int) -> bool:
        """Step 2: Create a sound entry on the buds."""
        logger.info("TUMBLE: Creating sound %d (size: %d bytes)", sound_id, sound_size)
        payload = struct.pack("<HI", sound_id, sound_size)
        resp = await self._send_command(TumbleCmd.CREATE_SOUND, payload)

        if resp and len(resp) > 0:
            code = resp[0]
            if code == ResponseCode.SUCCESS:
                logger.info("TUMBLE: Sound created successfully")
                return True
            elif code == ResponseCode.PARAMS_ALREADY_MET:
                logger.info("TUMBLE: Sound already exists on buds")
                return True
            else:
                logger.error("TUMBLE: Create sound failed with code 0x%02X", code)
                return False
        return False

    async def create_file(
        self,
        file_id: int,
        file_size: int,
        metadata: bytes = b"",
    ) -> bool:
        """Step 3: Create a file entry for transfer."""
        logger.info("TUMBLE: Creating file %d (size: %d bytes)", file_id, file_size)
        payload = struct.pack("<HI", file_id, file_size) + metadata
        resp = await self._send_command(TumbleCmd.CREATE_FILE, payload)

        if resp and len(resp) > 0:
            code = resp[0]
            if code == ResponseCode.SUCCESS:
                logger.info("TUMBLE: File created successfully")
                return True
            elif code == ResponseCode.PARAMS_ALREADY_MET:
                logger.info("TUMBLE: File already exists")
                return True
            else:
                logger.error("TUMBLE: Create file failed with code 0x%02X", code)
                return False
        return False

    async def start_transfer(
        self,
        sound_id: int,
        cluster_offset: int = 0,
    ) -> bool:
        """Step 4: Start the transfer for a cluster."""
        logger.info("TUMBLE: Starting transfer (sound=%d, cluster=%d)",
                     sound_id, cluster_offset)
        payload = struct.pack(
            "<HHBB",
            sound_id,
            cluster_offset,
            self.packets_per_block,
            self.block_timeout,
        )
        resp = await self._send_command(TumbleCmd.START_TRANSFER, payload)

        if resp and len(resp) > 0:
            code = resp[0]
            if code == ResponseCode.SUCCESS:
                logger.info("TUMBLE: Transfer started")
                return True
            else:
                logger.error("TUMBLE: Start transfer failed with code 0x%02X", code)
                return False
        return False

    async def _send_cluster(
        self,
        cluster_data: bytes,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        total_offset: int = 0,
        total_size: int = 0,
    ) -> bool:
        """Send a single cluster's worth of data block by block."""
        n_blocks = (len(cluster_data) + self.block_size - 1) // self.block_size
        logger.info("TUMBLE: Sending cluster (%d bytes, %d blocks)",
                     len(cluster_data), n_blocks)

        block_idx = 0
        while block_idx < n_blocks:
            if self._cancelled:
                return False

            start = block_idx * self.block_size
            end = min(start + self.block_size, len(cluster_data))
            block_data = cluster_data[start:end]

            # Write block data directly to control point
            await self.client.write_gatt_char(
                Characteristic.CONTROL_POINT,
                block_data,
                response=False,  # Write without response for speed
            )

            # Wait for flow control
            status = await self._wait_flow_control(timeout=self.block_timeout + 5)

            if status is None:
                logger.warning("TUMBLE: No flow control response for block %d", block_idx)
                return False
            elif status == TumbleStatus.BLOCK_COMPLETE:
                block_idx += 1
                if progress_callback and total_size > 0:
                    bytes_done = total_offset + min(end, len(cluster_data))
                    progress_callback(bytes_done, total_size)
            elif status == TumbleStatus.BLOCK_TIMEOUT:
                logger.warning("TUMBLE: Block %d timed out, retrying", block_idx)
                # Don't increment - resend same block
            elif status == TumbleStatus.CLUSTER_COMPLETE:
                logger.info("TUMBLE: Cluster complete")
                return True
            elif status == TumbleStatus.FILE_COMPLETE:
                logger.info("TUMBLE: File complete")
                return True
            else:
                logger.error("TUMBLE: Unexpected flow status 0x%02X", status)
                return False

        # If we sent all blocks without getting CLUSTER_COMPLETE,
        # wait a bit for the final notification
        status = await self._wait_flow_control(timeout=10)
        if status in (TumbleStatus.CLUSTER_COMPLETE, TumbleStatus.FILE_COMPLETE):
            return True

        logger.warning("TUMBLE: Cluster transfer ended without completion signal")
        return True  # Proceed anyway; CRC check will catch errors

    async def confirm_cluster(self, cluster_data: bytes) -> bool:
        """Confirm a cluster's CRC with the device."""
        crc = _crc32(cluster_data)
        logger.info("TUMBLE: Confirming cluster CRC: 0x%08X", crc)

        # Send as 8-byte little-endian (long CRC field, upper 4 bytes zero)
        payload = struct.pack("<Q", crc)
        resp = await self._send_command(TumbleCmd.CONFIRM_CLUSTER, payload)

        if resp and len(resp) > 0:
            code = resp[0]
            if code == ResponseCode.SUCCESS:
                logger.info("TUMBLE: Cluster CRC confirmed")
                return True
            elif code == TumbleStatus.BAD_CRC:
                logger.error("TUMBLE: Cluster CRC mismatch!")
                return False
            else:
                logger.error("TUMBLE: Cluster confirm failed with 0x%02X", code)
                return False
        return False

    async def end_transfer(self, file_data: bytes) -> bool:
        """Finalize the transfer with full file CRC."""
        crc = _crc32(file_data)
        logger.info("TUMBLE: Ending transfer, file CRC: 0x%08X", crc)

        payload = struct.pack("<Q", crc)
        resp = await self._send_command(TumbleCmd.END, payload, timeout=15.0)

        if resp and len(resp) > 0:
            code = resp[0]
            if code == ResponseCode.SUCCESS:
                logger.info("TUMBLE: Transfer completed successfully!")
                return True
            else:
                logger.error("TUMBLE: End transfer failed with 0x%02X", code)
                return False
        return False

    async def cancel_transfer(self):
        """Cancel an in-progress transfer."""
        self._cancelled = True
        logger.info("TUMBLE: Cancelling transfer...")
        await self._send_command(TumbleCmd.CANCEL, timeout=3.0)

    async def delete_file(self, file_id: int) -> bool:
        """Delete a file from the buds."""
        logger.info("TUMBLE: Deleting file %d", file_id)
        payload = struct.pack("<H", file_id)
        resp = await self._send_command(TumbleCmd.DELETE_FILE, payload)

        if resp and len(resp) > 0:
            return resp[0] == ResponseCode.SUCCESS
        return False

    # =========================================================================
    # High-Level Transfer
    # =========================================================================

    async def upload_sound(
        self,
        sound_id: int,
        file_id: int,
        file_data: bytes,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> bool:
        """
        Upload a complete sound file to the Sleepbuds.

        This is a single-channel upload. For stereo, call this twice
        (once for left earbud, once for right) if the buds are
        addressed separately. If connected to the case (which relays
        to both), a single upload may suffice.

        Args:
            sound_id: Sound track ID to assign
            file_id: File ID for this audio file
            file_data: Raw PCM audio bytes (24-bit, 8192 Hz, mono)
            progress_callback: Called with (bytes_done, total_bytes)

        Returns:
            True if upload succeeded
        """
        self._cancelled = False
        total_size = len(file_data)
        logger.info("TUMBLE: Starting upload of %d bytes (sound=%d, file=%d)",
                     total_size, sound_id, file_id)

        # Step 1: Query device
        device_resp = await self.query_device()
        if device_resp is None:
            logger.error("TUMBLE: Device query failed")
            return False
        logger.info("TUMBLE: Device response: %s",
                     device_resp.hex() if device_resp else "none")

        # Step 2: Set fast connection interval
        await self._set_fast_connection()
        await asyncio.sleep(0.5)

        # Step 3: Create sound
        if not await self.create_sound(sound_id, total_size):
            logger.error("TUMBLE: Failed to create sound entry")
            return False

        # Step 4: Create file
        if not await self.create_file(file_id, total_size):
            logger.error("TUMBLE: Failed to create file entry")
            return False

        await asyncio.sleep(0.3)

        # Step 5: Transfer clusters
        n_clusters = (total_size + self.bytes_per_cluster - 1) // self.bytes_per_cluster
        logger.info("TUMBLE: Transferring %d clusters", n_clusters)

        for cluster_idx in range(n_clusters):
            if self._cancelled:
                return False

            cluster_start = cluster_idx * self.bytes_per_cluster
            cluster_end = min(cluster_start + self.bytes_per_cluster, total_size)
            cluster_data = file_data[cluster_start:cluster_end]

            logger.info("TUMBLE: Cluster %d/%d (%d bytes)",
                         cluster_idx + 1, n_clusters, len(cluster_data))

            # Start transfer for this cluster
            if not await self.start_transfer(sound_id, cluster_idx):
                logger.error("TUMBLE: Failed to start cluster %d", cluster_idx)
                return False

            # Send cluster data
            success = await self._send_cluster(
                cluster_data,
                progress_callback=progress_callback,
                total_offset=cluster_start,
                total_size=total_size,
            )
            if not success:
                logger.error("TUMBLE: Cluster %d transfer failed", cluster_idx)
                return False

            # Confirm cluster CRC (except for the last cluster, which is
            # confirmed by TUMBLE_END)
            if cluster_idx < n_clusters - 1:
                # Pad cluster to full size for CRC if needed
                padded = cluster_data
                if len(padded) < self.bytes_per_cluster:
                    padded = cluster_data + b'\x00' * (self.bytes_per_cluster - len(cluster_data))
                if not await self.confirm_cluster(padded):
                    logger.error("TUMBLE: Cluster %d CRC confirmation failed", cluster_idx)
                    return False

            await asyncio.sleep(0.2)

        # Step 6: End transfer
        if not await self.end_transfer(file_data):
            logger.error("TUMBLE: End transfer failed")
            return False

        logger.info("TUMBLE: Upload complete! Sound %d (%d bytes) transferred.",
                     sound_id, total_size)

        if progress_callback:
            progress_callback(total_size, total_size)

        return True
