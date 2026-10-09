#!/usr/bin/env python3
"""
Bose Sleepbuds II Revival - Command Line Interface.

Usage:
    python cli.py scan                         Scan for Sleepbuds
    python cli.py status                       Connect and read full device state
    python cli.py play <id> --both             Play a sound on both buds
    python cli.py play <id> --phone-free       Play + enable phone-free mode
    python cli.py stop --both                  Stop playback on both buds
    python cli.py volume <0-255> --both        Set volume on both buds
    python cli.py phone-free --both            Enable phone-free mode
    python cli.py phone-free-off --both        Disable phone-free mode
    python cli.py sounds                       List known sound track IDs
    python cli.py convert <file>               Convert audio file
    python cli.py upload <file>                Upload a .bin file
    python cli.py services                     Enumerate all GATT services
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from bleak import BleakClient

from sleepbuds.protocol import (
    ALL_SOUNDS, SOUNDS_DROWSY, SOUNDS_KINGSLEY, DEFAULT_TRACK_ID,
    FILE_ID_TO_NAME, FILE_ID_TO_TRACK_ID, TRACK_ID_TO_NAME,
)
from sleepbuds.scanner import scan_for_sleepbuds, read_device_state, connect_and_read
from sleepbuds.controller import SleepbudsController
from sleepbuds.audio import convert_any_to_sleepbuds, validate_bin_file, get_audio_info
from sleepbuds.tumble import TumbleTransfer

logger = logging.getLogger("sleepbuds")

# Cache the last-used device addresses
_address_file = Path(__file__).parent / ".sleepbuds_address"
_addresses_file = Path(__file__).parent / ".sleepbuds_addresses"


def _save_address(address: str):
    _address_file.write_text(address)


def _load_address() -> str:
    if _address_file.exists():
        return _address_file.read_text().strip()
    return ""


def _save_addresses(addresses: dict[str, str]):
    """Save all discovered bud addresses. Format: name=address per line."""
    lines = [f"{name}={addr}" for name, addr in addresses.items()]
    _addresses_file.write_text("\n".join(lines))


def _load_addresses() -> dict[str, str]:
    """Load saved bud addresses. Returns {name: address}."""
    if _addresses_file.exists():
        result = {}
        for line in _addresses_file.read_text().strip().split("\n"):
            if "=" in line:
                name, addr = line.split("=", 1)
                result[name.strip()] = addr.strip()
        return result
    return {}


async def cmd_scan(args):
    """Scan for Sleepbuds devices."""
    devices = await scan_for_sleepbuds(timeout=args.timeout)
    if not devices:
        print("No Sleepbuds found.")
        print("Make sure they are:")
        print("  - Out of the charging case")
        print("  - Bluetooth is enabled on this computer")
        print("  - Not connected to another device")
        return

    print(f"\nFound {len(devices)} device(s):\n")
    for i, d in enumerate(devices):
        print(f"  [{i+1}] {d.name or 'Unknown'}")
        print(f"      Address: {d.address}")
        rssi = getattr(d, "rssi", None)
        if rssi is not None:
            print(f"      Signal:  {rssi} dBm")
        print()

    # Save first device address (backwards compat)
    _save_address(devices[0].address)
    print(f"Saved address: {devices[0].address}")

    # Save all bud addresses by name for --both support
    all_addrs = {}
    for d in devices:
        if d.name:
            all_addrs[d.name] = d.address
    if all_addrs:
        _save_addresses(all_addrs)
        if len(all_addrs) > 1:
            print(f"Saved {len(all_addrs)} bud addresses (use --both to control both)")


async def cmd_status(args):
    """Connect and read full device state."""
    address = args.address or _load_address()
    if not address:
        print("No device address. Run 'scan' first or use --address.")
        return

    print(f"Connecting to {address}...")
    try:
        state = await connect_and_read(address, timeout=args.timeout)
    except Exception as e:
        print(f"Connection failed: {e}")
        return

    print(f"\n{'='*50}")
    print(f"  Bose Sleepbuds II Status")
    print(f"{'='*50}\n")

    print(f"  Name:              {state.name or 'N/A'}")
    print(f"  Address:           {state.address}")
    print(f"  Firmware:          {state.firmware_version or 'N/A'}")
    print(f"  Serial:            {state.serial_number or 'N/A'}")
    print(f"  Hardware variant:  {state.variant}")
    print()

    battery_str = f"{state.battery_level}%" if state.battery_level >= 0 else "N/A"
    print(f"  Battery:           {battery_str}")
    print(f"  Bud in case:       {state.bud_in_case}")
    print()

    print(f"  Phone-free mode:   {state.phone_free_enabled}")
    print(f"  Has been renamed:  {state.has_been_renamed}")
    print()

    if state.track_id != DEFAULT_TRACK_ID:
        # track_id in audio characteristic could be a file_id or track_id
        track_name = ALL_SOUNDS.get(state.track_id, f"Unknown({state.track_id})")
        print(f"  Playing:           {state.is_playing}")
        print(f"  Sound:             {track_name} (ID: {state.track_id})")
        print(f"  Volume:            {state.volume}")
        if state.masking_timeout_secs > 0:
            print(f"  Timeout:           {state.masking_timeout_secs}s")
            print(f"  Remaining:         {state.time_remaining_secs}s")
        else:
            print(f"  Timeout:           indefinite")
    else:
        print(f"  Playing:           No")
    print()

    if state.sound_ids:
        known = [(fid, FILE_ID_TO_NAME.get(fid)) for fid in state.sound_ids if FILE_ID_TO_NAME.get(fid)]
        unknown = [fid for fid in state.sound_ids if not FILE_ID_TO_NAME.get(fid)]
        print(f"  Sounds on buds ({len(known)} known, {len(unknown)} unknown):")
        for fid in sorted(state.sound_ids):
            name = FILE_ID_TO_NAME.get(fid)
            tid = FILE_ID_TO_TRACK_ID.get(fid)
            if name:
                print(f"    [{fid:5d}] {name:<20s} (track_id={tid})")
            else:
                print(f"    [{fid:5d}] ???")
    else:
        print("  Sounds on buds:    None detected")

    print()

    # Show GATT services summary
    print(f"  GATT Services ({len(state.services)}):")
    for svc_uuid, chars in state.services.items():
        print(f"    {svc_uuid} ({len(chars)} characteristics)")


async def cmd_services(args):
    """Enumerate all GATT services in detail."""
    address = args.address or _load_address()
    if not address:
        print("No device address. Run 'scan' first or use --address.")
        return

    print(f"Connecting to {address}...")
    async with BleakClient(address, timeout=args.timeout) as client:
        print("Connected! Enumerating services...\n")

        for svc in client.services:
            print(f"Service: {svc.uuid}")
            print(f"  Description: {svc.description or 'N/A'}")

            for char in svc.characteristics:
                props = ", ".join(char.properties)
                print(f"  Characteristic: {char.uuid}")
                print(f"    Properties: [{props}]")
                print(f"    Handle: {char.handle}")

                if "read" in char.properties:
                    try:
                        value = await client.read_gatt_char(char.uuid)
                        # Show as hex and attempt UTF-8
                        hex_str = value.hex()
                        try:
                            text = value.decode("utf-8", errors="replace").rstrip("\x00")
                            print(f"    Value: {hex_str}")
                            if text and text.isprintable():
                                print(f"    Text:  {text}")
                        except Exception:
                            print(f"    Value: {hex_str}")
                    except Exception as e:
                        print(f"    Value: <error: {e}>")

                for desc in char.descriptors:
                    print(f"    Descriptor: {desc.uuid}")
            print()


async def _play_synced_on_bud(address: str, bud_name: str, resolved_id: int, volume: int,
                              timeout_secs: int, connect_timeout: float):
    """Send PlayInFuture command to a single bud's Control Point."""
    try:
        async with BleakClient(address, timeout=connect_timeout) as client:
            ctrl = SleepbudsController(client)
            await ctrl.setup_notifications()
            success = await ctrl.play_synced(resolved_id, volume, timeout_secs)
            if success:
                print(f"  {bud_name}: PlayInFuture SUCCESS!")
            else:
                # PlayInFuture failed, try direct audio write as fallback
                print(f"  {bud_name}: PlayInFuture failed, trying direct write...")
                direct = await ctrl.play_sound(resolved_id, volume, timeout_secs)
                if direct:
                    await asyncio.sleep(0.3)
                    from sleepbuds.protocol import Characteristic
                    data = await client.read_gatt_char(Characteristic.AUDIO_DATA)
                    is_playing = bool(data[0] & 0x01) if data else False
                    if is_playing:
                        print(f"  {bud_name}: Direct write playing.")
                    else:
                        print(f"  {bud_name}: Direct write sent but not playing.")
                else:
                    print(f"  {bud_name}: Direct write also failed.")
    except Exception as e:
        print(f"  {bud_name}: Connection failed ({e})")


async def _play_on_bud(address: str, bud_name: str, resolved_id: int, volume: int,
                       timeout_secs: int, connect_timeout: float,
                       phone_free: bool = False):
    """Play a sound on a single bud."""
    try:
        async with BleakClient(address, timeout=connect_timeout) as client:
            ctrl = SleepbudsController(client)

            if phone_free:
                # Disable phone-free first to unlock audio state
                print(f"  {bud_name}: Disabling phone-free to unlock audio state...")
                await ctrl.disable_phone_free()
                await asyncio.sleep(0.3)

            success = await ctrl.play_sound(resolved_id, volume, timeout_secs)
            if success:
                await asyncio.sleep(0.3)
                from sleepbuds.protocol import Characteristic
                data = await client.read_gatt_char(Characteristic.AUDIO_DATA)
                is_playing = bool(data[0] & 0x01) if data else False
                print(f"  {bud_name}: Audio write done (playing={is_playing})")
                print(f"  {bud_name}: Audio state: {data.hex()}")

                if phone_free:
                    # Enable phone-free to trigger autonomous playback
                    print(f"  {bud_name}: Enabling phone-free mode...")
                    await ctrl.enable_phone_free()
                    await asyncio.sleep(0.5)
                    # Read back to verify
                    data2 = await client.read_gatt_char(Characteristic.AUDIO_DATA)
                    is_playing2 = bool(data2[0] & 0x01) if data2 else False
                    print(f"  {bud_name}: After phone-free: playing={is_playing2} state={data2.hex()}")
                elif is_playing:
                    print(f"  {bud_name}: Playing!")
                else:
                    print(f"  {bud_name}: Write sent but not playing.")
            else:
                print(f"  {bud_name}: Failed to send play command.")
    except Exception as e:
        print(f"  {bud_name}: Connection failed ({e})")


def _resolve_sound_id(sound_id: int) -> int:
    """Resolve a track_id to file_id if needed. Returns file_id."""
    if sound_id >= 1000:
        return sound_id
    for tid, (sname, fid) in {**SOUNDS_DROWSY, **SOUNDS_KINGSLEY}.items():
        if tid == sound_id:
            print(f"Resolved track_id {sound_id} -> file_id {fid} ({sname})")
            return fid
    print(f"WARNING: track_id {sound_id} not found in known sounds.")
    print(f"  Use a file_id instead. Run 'python3 cli.py sounds' to see options.")
    return -1


async def cmd_play(args):
    """Play a sound."""
    sound_id = args.sound_id
    volume = args.volume
    timeout_secs = args.sleep_timer

    resolved_id = _resolve_sound_id(sound_id)
    if resolved_id == -1:
        return

    name = ALL_SOUNDS.get(resolved_id, ALL_SOUNDS.get(sound_id, "Unknown"))
    print(f"Playing {name} (file_id={resolved_id}) at volume {volume}...")
    if timeout_secs > 0:
        h, m = divmod(timeout_secs, 3600)
        m, s = divmod(m, 60)
        parts = []
        if h: parts.append(f"{h}h")
        if m: parts.append(f"{m}m")
        if s: parts.append(f"{s}s")
        print(f"  Sleep timer: {' '.join(parts)} ({timeout_secs}s)")
    else:
        print(f"  Sleep timer: off (indefinite)")

    if args.both:
        addrs = _load_addresses()
        if len(addrs) < 2:
            print("Need both bud addresses. Run 'python3 cli.py scan' first.")
            return

        phone_free = getattr(args, 'phone_free', False)
        sorted_buds = sorted(addrs.items(),
                             key=lambda x: (0 if "left" in x[0].lower() else 1))

        # Step 1: Read first bud's clock and build PlayInFuture with 2s offset
        print("  Reading clock for sync...")
        target_time = None
        try:
            first_addr = sorted_buds[0][1]
            async with BleakClient(first_addr, timeout=args.timeout) as client:
                ctrl = SleepbudsController(client)
                await ctrl.setup_notifications()
                clock_data = await ctrl.read_bud_clock()
                if clock_data and len(clock_data) >= 8:
                    # Build command with 2000ms offset (enough for sequential sends)
                    target_time = ctrl._build_play_in_future(
                        file_id=resolved_id, volume=volume,
                        timeout_secs=timeout_secs,
                        clock_data=clock_data,
                    )
                    # Replace the 750ms default with 2000ms
                    import struct as s
                    minutes = clock_data[2]
                    seconds = clock_data[3]
                    ms = clock_data[4] | (clock_data[5] << 8)
                    ms += 2000  # 2 second offset
                    if ms >= 1000:
                        seconds += ms // 1000
                        ms = ms % 1000
                    if seconds >= 60:
                        minutes += seconds // 60
                        seconds = seconds % 60
                    if minutes >= 60:
                        minutes = minutes % 60
                    target_time = bytearray(target_time)
                    target_time[2] = minutes & 0xFF
                    target_time[3] = seconds & 0xFF
                    s.pack_into("<H", target_time, 4, ms)
                    target_time = bytes(target_time)
                    print(f"  Sync target: min={minutes} sec={seconds} ms={ms}")
        except Exception as e:
            print(f"  Clock read failed ({e}), falling back to unsynchronized")

        # Step 2: Send to each bud sequentially
        from sleepbuds.protocol import Characteristic
        for bud_name, addr in sorted_buds:
            try:
                async with BleakClient(addr, timeout=args.timeout) as client:
                    ctrl = SleepbudsController(client)
                    await ctrl.setup_notifications()

                    if phone_free:
                        await ctrl.disable_phone_free()
                        await asyncio.sleep(0.2)

                    # Try PlayInFuture with shared target time
                    synced = False
                    if target_time:
                        resp = await ctrl.send_control_command(
                            target_time[:2], target_time[2:],
                            wait_response=True, timeout=5.0)
                        if resp and len(resp) > 0 and resp[0] == 0x00:
                            synced = True
                            print(f"  {bud_name}: PlayInFuture synced!")

                    if not synced:
                        # Fallback to direct write
                        await ctrl.play_sound(resolved_id, volume, timeout_secs)

                    await asyncio.sleep(0.3)
                    data = await client.read_gatt_char(Characteristic.AUDIO_DATA)
                    playing = bool(data[0] & 0x01) if data else False

                    if phone_free:
                        await ctrl.enable_phone_free()
                        await asyncio.sleep(0.3)
                        data = await client.read_gatt_char(Characteristic.AUDIO_DATA)
                        playing = bool(data[0] & 0x01) if data else False

                    status = "synced" if synced else "direct"
                    print(f"  {bud_name}: playing={playing} ({status})")
            except Exception as e:
                print(f"  {bud_name}: Connection failed ({e})")
    else:
        address = args.address or _load_address()
        if not address:
            print("No device address. Run 'scan' first or use --address.")
            return
        await _play_on_bud(address, "Bud", resolved_id, volume,
                           timeout_secs, args.timeout,
                           phone_free=getattr(args, 'phone_free', False))


async def _stop_on_bud(address: str, bud_name: str, connect_timeout: float):
    """Stop playback on a single bud."""
    try:
        async with BleakClient(address, timeout=connect_timeout) as client:
            ctrl = SleepbudsController(client)
            success = await ctrl.stop_playback()
            print(f"  {bud_name}: {'Stopped.' if success else 'Failed.'}")
    except Exception as e:
        print(f"  {bud_name}: Connection failed ({e})")


async def cmd_stop(args):
    """Stop playback. Also disables phone-free to prevent resume after disconnect."""
    print("Stopping playback...")
    if args.both:
        addrs = _load_addresses()
        if len(addrs) < 2:
            print("Need both bud addresses. Run 'python3 cli.py scan' first.")
            return

        sorted_buds = sorted(addrs.items(),
                             key=lambda x: (0 if "left" in x[0].lower() else 1))
        for bud_name, addr in sorted_buds:
            try:
                async with BleakClient(addr, timeout=args.timeout) as client:
                    ctrl = SleepbudsController(client)
                    await ctrl.disable_phone_free()
                    success = await ctrl.stop_playback()
                    print(f"  {bud_name}: {'Stopped' if success else 'Failed'}")
            except Exception as e:
                print(f"  {bud_name}: Connection failed ({e})")
        print("  Tip: Put buds in case to avoid the lost/find tone.")
    else:
        address = args.address or _load_address()
        if not address:
            print("No device address. Run 'scan' first or use --address.")
            return
        try:
            async with BleakClient(address, timeout=args.timeout) as client:
                ctrl = SleepbudsController(client)
                await ctrl.disable_phone_free()
                success = await ctrl.stop_playback()
                print(f"  {'Stopped.' if success else 'Failed.'}")
        except Exception as e:
            print(f"  Connection failed ({e})")


async def cmd_volume(args):
    """Set volume on one or both buds."""
    print(f"Setting volume to {args.level}...")
    if args.both:
        addrs = _load_addresses()
        if len(addrs) < 2:
            print("Need both bud addresses. Run 'python3 cli.py scan' first.")
            return
        sorted_buds = sorted(addrs.items(),
                             key=lambda x: (0 if "left" in x[0].lower() else 1))
        for bud_name, addr in sorted_buds:
            try:
                async with BleakClient(addr, timeout=args.timeout) as client:
                    ctrl = SleepbudsController(client)
                    success = await ctrl.set_volume(args.level)
                    print(f"  {bud_name}: {'Set to ' + str(args.level) if success else 'Failed'}")
            except Exception as e:
                print(f"  {bud_name}: Connection failed ({e})")
    else:
        address = args.address or _load_address()
        if not address:
            print("No device address. Run 'scan' first or use --address.")
            return
        async with BleakClient(address, timeout=args.timeout) as client:
            ctrl = SleepbudsController(client)
            success = await ctrl.set_volume(args.level)
            print(f"Volume set to {args.level}." if success else "Failed.")


async def _phone_free_on_bud(address: str, bud_name: str, enable: bool, connect_timeout: float):
    """Enable/disable phone-free on a single bud."""
    try:
        async with BleakClient(address, timeout=connect_timeout) as client:
            ctrl = SleepbudsController(client)
            await ctrl.setup_notifications()
            if enable:
                success = await ctrl.enable_phone_free()
                print(f"  {bud_name}: {'Enabled' if success else 'Failed'}")
            else:
                success = await ctrl.disable_phone_free()
                print(f"  {bud_name}: {'Disabled' if success else 'Failed'}")
    except Exception as e:
        print(f"  {bud_name}: Connection failed ({e})")


async def cmd_phone_free(args):
    """Enable phone-free mode."""
    print("Enabling phone-free mode...")
    print("(Buds will continue playing without app connection)")
    if args.both:
        addrs = _load_addresses()
        if len(addrs) < 2:
            print("Need both bud addresses. Run 'python3 cli.py scan' first.")
            return
        tasks = [_phone_free_on_bud(addr, name, True, args.timeout)
                 for name, addr in addrs.items()]
        await asyncio.gather(*tasks)
    else:
        address = args.address or _load_address()
        if not address:
            print("No device address. Run 'scan' first or use --address.")
            return
        await _phone_free_on_bud(address, "Bud", True, args.timeout)


async def cmd_phone_free_off(args):
    """Disable phone-free mode."""
    print("Disabling phone-free mode...")
    if args.both:
        addrs = _load_addresses()
        if len(addrs) < 2:
            print("Need both bud addresses. Run 'python3 cli.py scan' first.")
            return
        tasks = [_phone_free_on_bud(addr, name, False, args.timeout)
                 for name, addr in addrs.items()]
        await asyncio.gather(*tasks)
    else:
        address = args.address or _load_address()
        if not address:
            print("No device address. Run 'scan' first or use --address.")
            return
        await _phone_free_on_bud(address, "Bud", False, args.timeout)


async def cmd_alarm(args):
    """Set or cancel a bud-based alarm."""
    if args.cancel:
        print("Cancelling alarm...")
        if args.both:
            addrs = _load_addresses()
            if len(addrs) < 2:
                print("Need both bud addresses. Run 'python3 cli.py scan' first.")
                return
            for bud_name, addr in sorted(addrs.items(),
                                         key=lambda x: (0 if "left" in x[0].lower() else 1)):
                try:
                    async with BleakClient(addr, timeout=args.timeout) as client:
                        ctrl = SleepbudsController(client)
                        await ctrl.setup_notifications()
                        success = await ctrl.cancel_alarm()
                        print(f"  {bud_name}: {'Cancelled' if success else 'Failed'}")
                except Exception as e:
                    print(f"  {bud_name}: Connection failed ({e})")
        else:
            address = args.address or _load_address()
            if not address:
                print("No device address. Run 'scan' first or use --address.")
                return
            async with BleakClient(address, timeout=args.timeout) as client:
                ctrl = SleepbudsController(client)
                await ctrl.setup_notifications()
                success = await ctrl.cancel_alarm()
                print(f"  {'Cancelled' if success else 'Failed'}")
        return

    if not args.time:
        print("Alarm time required. Use HH:MM (e.g. 7:30) or minutes from now (e.g. 60)")
        print("  python3 cli.py alarm 7:30 --both")
        print("  python3 cli.py alarm 60 --both    # 60 minutes from now")
        print("  python3 cli.py alarm --cancel --both")
        return

    # Parse time: "7:30" or "07:30" or minutes-from-now as int
    time_str = args.time
    try:
        if ":" in time_str:
            parts = time_str.split(":")
            alarm_h, alarm_m = int(parts[0]), int(parts[1])
            from datetime import datetime
            now = datetime.now()
            alarm_today = now.replace(hour=alarm_h, minute=alarm_m, second=0, microsecond=0)
            diff = (alarm_today - now).total_seconds()
            if diff <= 0:
                diff += 86400  # tomorrow
            seconds_from_now = int(diff)
        else:
            seconds_from_now = int(time_str) * 60  # interpret as minutes
    except (ValueError, IndexError):
        print(f"Invalid time format: {time_str}")
        print("  Use HH:MM (e.g. 7:30) or minutes from now (e.g. 60)")
        return

    if seconds_from_now > 64800:
        print(f"Alarm too far ahead ({seconds_from_now}s). Max is 18 hours (64800s).")
        return

    # Resolve alarm sound
    sound_id = args.sound
    resolved = _resolve_sound_id(sound_id)
    if resolved == -1:
        return

    h, m = divmod(seconds_from_now, 3600)
    m, s = divmod(m, 60)
    print(f"Setting alarm: {h}h {m}m {s}s from now")
    print(f"  Sound: {ALL_SOUNDS.get(resolved, 'Unknown')} (file_id={resolved})")
    print(f"  Volume: {args.alarm_volume}, Duration: {args.duration}s, Fade-in: {args.fade_in}s")

    if args.both:
        addrs = _load_addresses()
        if len(addrs) < 2:
            print("Need both bud addresses. Run 'python3 cli.py scan' first.")
            return
        for bud_name, addr in sorted(addrs.items(),
                                     key=lambda x: (0 if "left" in x[0].lower() else 1)):
            try:
                async with BleakClient(addr, timeout=args.timeout) as client:
                    ctrl = SleepbudsController(client)
                    await ctrl.setup_notifications()
                    success = await ctrl.set_alarm(
                        seconds_from_now, resolved, args.alarm_volume,
                        args.duration, args.fade_in, args.fade_out)
                    print(f"  {bud_name}: {'Alarm set' if success else 'Failed'}")
            except Exception as e:
                print(f"  {bud_name}: Connection failed ({e})")
    else:
        address = args.address or _load_address()
        if not address:
            print("No device address. Run 'scan' first or use --address.")
            return
        async with BleakClient(address, timeout=args.timeout) as client:
            ctrl = SleepbudsController(client)
            await ctrl.setup_notifications()
            success = await ctrl.set_alarm(
                seconds_from_now, resolved, args.alarm_volume,
                args.duration, args.fade_in, args.fade_out)
            print(f"  {'Alarm set' if success else 'Failed'}")


async def cmd_activate(args):
    """Send ACTIVATE_GROUP command to wake up buds."""
    print("Sending ACTIVATE_GROUP command...")

    if args.both:
        addrs = _load_addresses()
        if len(addrs) < 2:
            print("Need both bud addresses. Run 'python3 cli.py scan' first.")
            return

        sorted_buds = sorted(addrs.items(),
                             key=lambda x: (0 if "left" in x[0].lower() else 1))
        for bud_name, addr in sorted_buds:
            try:
                async with BleakClient(addr, timeout=args.timeout) as client:
                    ctrl = SleepbudsController(client)
                    await ctrl.setup_notifications()
                    success = await ctrl.activate_bud(
                        major=args.major, minor=args.minor, patch=args.patch)
                    if success:
                        print(f"  {bud_name}: ACTIVATED")
                    else:
                        print(f"  {bud_name}: Activation failed")
            except Exception as e:
                print(f"  {bud_name}: Connection failed ({e})")
    else:
        address = args.address or _load_address()
        if not address:
            print("No device address. Run 'scan' first or use --address.")
            return
        try:
            async with BleakClient(address, timeout=args.timeout) as client:
                ctrl = SleepbudsController(client)
                await ctrl.setup_notifications()
                success = await ctrl.activate_bud(
                    major=args.major, minor=args.minor, patch=args.patch)
                if success:
                    print("  ACTIVATED")
                else:
                    print("  Activation failed")
        except Exception as e:
            print(f"  Connection failed ({e})")


async def cmd_rawplay(args):
    """Diagnostic: write raw audio frame and read back to test write behavior."""
    from sleepbuds.protocol import Characteristic
    import struct

    address = args.address or _load_address()
    if not address:
        print("No device address. Run 'scan' first or use --address.")
        return

    file_id = args.file_id
    volume = args.volume

    print(f"Raw audio write test: file_id={file_id} volume={volume}")
    async with BleakClient(address, timeout=args.timeout) as client:
        # Read current state
        data = await client.read_gatt_char(Characteristic.AUDIO_DATA)
        print(f"  BEFORE: {data.hex()}")
        cur_playing = bool(data[0] & 0x01)
        cur_vol = data[1]
        cur_tid = struct.unpack_from("<H", data, 2)[0]
        print(f"    playing={cur_playing} vol={cur_vol} track={cur_tid}")

        # Build frame: playing=true, given volume, given file_id, rest zeros
        frame = bytearray(12)
        frame[0] = 0x01  # playing
        frame[1] = volume & 0xFF
        struct.pack_into("<H", frame, 2, file_id & 0xFFFF)
        print(f"  WRITING: {bytes(frame).hex()}")

        # Try write-without-response
        await client.write_gatt_char(Characteristic.AUDIO_DATA, bytes(frame), response=False)
        await asyncio.sleep(0.5)

        data2 = await client.read_gatt_char(Characteristic.AUDIO_DATA)
        print(f"  AFTER (no-resp): {data2.hex()}")
        new_playing = bool(data2[0] & 0x01)
        new_vol = data2[1]
        new_tid = struct.unpack_from("<H", data2, 2)[0]
        print(f"    playing={new_playing} vol={new_vol} track={new_tid}")

        if data2 == data:
            print("  ** NO CHANGE - write was ignored **")
            # Try write-with-response
            print("  Trying write-with-response...")
            try:
                await client.write_gatt_char(Characteristic.AUDIO_DATA, bytes(frame), response=True)
                await asyncio.sleep(0.5)
                data3 = await client.read_gatt_char(Characteristic.AUDIO_DATA)
                print(f"  AFTER (with-resp): {data3.hex()}")
                if data3 == data:
                    print("  ** STILL NO CHANGE **")
                else:
                    print("  ** CHANGED with write-with-response! **")
            except Exception as e:
                print(f"  Write-with-response error: {e}")
        else:
            print("  ** STATE CHANGED **")


def cmd_sounds(args):
    """List all known sound track IDs."""
    print("\nKingsley Variant Sounds:")
    print(f"  {'track':>6}  {'file_id':>8}  Name")
    print(f"  {'-----':>6}  {'-------':>8}  {'----'}")
    for tid in sorted(SOUNDS_KINGSLEY):
        name, fid = SOUNDS_KINGSLEY[tid]
        print(f"  {tid:6d}  {fid:8d}  {name}")

    print("\nDrowsy Variant Sounds:")
    print(f"  {'track':>6}  {'file_id':>8}  Name")
    print(f"  {'-----':>6}  {'-------':>8}  {'----'}")
    for tid in sorted(SOUNDS_DROWSY):
        name, fid = SOUNDS_DROWSY[tid]
        print(f"  {tid:6d}  {fid:8d}  {name}")

    print("\nUse the TRACK ID (first column) with the 'play' command.")
    print("The FILE ID is what the buds store internally.")
    print("Run 'status' to see which sounds are loaded on your buds.")


def cmd_convert(args):
    """Convert an audio file to Sleepbuds format."""
    src = Path(args.input)
    if not src.exists():
        print(f"File not found: {src}")
        return

    # Determine output prefix
    if args.output:
        output_prefix = args.output
    else:
        output_prefix = str(src.parent / src.stem)

    # Show input info
    info = get_audio_info(str(src))
    print(f"Input: {src.name}")
    for k, v in info.items():
        print(f"  {k}: {v}")
    print()

    # Convert
    try:
        left_path, right_path = convert_any_to_sleepbuds(str(src), output_prefix)
    except Exception as e:
        print(f"Conversion failed: {e}")
        return

    # Validate output
    print(f"\nOutput files:")
    for p in [left_path, right_path]:
        v = validate_bin_file(p)
        print(f"  {Path(p).name}")
        print(f"    Size: {v['file_size']:,} bytes")
        print(f"    Duration: {v['duration_secs']:.1f} seconds")
        print(f"    Valid alignment: {v['valid_alignment']}")


async def cmd_upload(args):
    """Upload a .bin file to Sleepbuds."""
    address = args.address or _load_address()
    if not address:
        print("No device address. Run 'scan' first or use --address.")
        return

    bin_path = Path(args.file)
    if not bin_path.exists():
        print(f"File not found: {bin_path}")
        return

    sound_id = args.sound_id
    file_id = args.file_id

    file_data = bin_path.read_bytes()
    info = validate_bin_file(str(bin_path))

    print(f"File: {bin_path.name}")
    print(f"  Size: {info['file_size']:,} bytes")
    print(f"  Duration: {info['duration_secs']:.1f} seconds")
    print(f"  Sound ID: {sound_id}")
    print(f"  File ID: {file_id}")
    print()

    if not info['valid_alignment']:
        print("WARNING: File size is not aligned to 3-byte samples.")
        print("The file may not be in the correct format.")
        resp = input("Continue anyway? [y/N] ")
        if resp.lower() != 'y':
            return

    def progress(done, total):
        pct = done / total * 100 if total > 0 else 0
        bar_len = 40
        filled = int(bar_len * done / total)
        bar = '#' * filled + '-' * (bar_len - filled)
        print(f"\r  [{bar}] {pct:5.1f}% ({done:,}/{total:,})", end="", flush=True)

    print("Connecting and uploading...")
    async with BleakClient(address, timeout=args.timeout) as client:
        transfer = TumbleTransfer(client, variant=args.variant)
        await transfer.setup_notifications()

        success = await transfer.upload_sound(
            sound_id=sound_id,
            file_id=file_id,
            file_data=file_data,
            progress_callback=progress,
        )
        print()  # newline after progress bar

        if success:
            print("Upload complete!")
        else:
            print("Upload failed.")


def main():
    # Shared arguments available to all subcommands
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--address", "-a",
        help="BLE device address (saved from last scan if omitted)",
    )
    shared.add_argument(
        "--timeout", "-t",
        type=float, default=15.0,
        help="Connection/scan timeout in seconds (default: 15)",
    )
    shared.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    shared.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging (very verbose)",
    )

    parser = argparse.ArgumentParser(
        description="Bose Sleepbuds II Revival Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[shared],
        epilog="""
Examples:
  %(prog)s scan                         Find Sleepbuds
  %(prog)s status -v                    Read device state (verbose)
  %(prog)s play 34 --volume 80          Play "Warm Static" (Drowsy)
  %(prog)s phone-free                   Enable phone-free mode
  %(prog)s convert rain.wav             Convert audio to Sleepbuds format
  %(prog)s upload rain_left.bin -s 200  Upload converted audio
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # scan
    subparsers.add_parser("scan", parents=[shared], help="Scan for Sleepbuds devices")

    # status
    subparsers.add_parser("status", parents=[shared], help="Read full device state")

    # services
    subparsers.add_parser("services", parents=[shared], help="Enumerate all GATT services")

    # play
    p_play = subparsers.add_parser("play", parents=[shared], help="Play a sound")
    p_play.add_argument("sound_id", type=int,
                        help="Sound ID: track_id (e.g. 35) auto-resolves to file_id, or pass file_id directly (e.g. 31567)")
    p_play.add_argument("--volume", type=int, default=50, help="Volume 0-255 (default: 50)")
    p_play.add_argument("--sleep-timer", type=int, default=0,
                        help="Auto-stop after N seconds (0=indefinite)")
    p_play.add_argument("--both", action="store_true", help="Play on both buds (requires scan first)")
    p_play.add_argument("--phone-free", action="store_true",
                        help="Disable phone-free, write audio, re-enable (single connection)")

    # stop
    p_stop = subparsers.add_parser("stop", parents=[shared], help="Stop playback")
    p_stop.add_argument("--both", action="store_true", help="Stop on both buds")

    # volume
    p_vol = subparsers.add_parser("volume", parents=[shared], help="Set volume")
    p_vol.add_argument("level", type=int, help="Volume level 0-255")
    p_vol.add_argument("--both", action="store_true", help="Set volume on both buds")

    # phone-free
    p_pf = subparsers.add_parser("phone-free", parents=[shared], help="Enable phone-free mode")
    p_pf.add_argument("--both", action="store_true", help="Enable on both buds")

    # phone-free-off
    p_pfo = subparsers.add_parser("phone-free-off", parents=[shared], help="Disable phone-free mode")
    p_pfo.add_argument("--both", action="store_true", help="Disable on both buds")

    # alarm
    p_alarm = subparsers.add_parser("alarm", parents=[shared],
                                    help="Set or cancel a bud-based alarm")
    p_alarm.add_argument("time", nargs="?", default=None,
                         help="Alarm time: HH:MM (e.g. 7:30) or minutes from now (e.g. 60)")
    p_alarm.add_argument("--sound", type=int, default=57,
                         help="Alarm sound track_id or file_id (default: 57 = Gentle Bells)")
    p_alarm.add_argument("--alarm-volume", type=int, default=75,
                         help="Alarm volume 0-255 (default: 75)")
    p_alarm.add_argument("--duration", type=int, default=300,
                         help="Alarm play duration in seconds (default: 300 = 5 min)")
    p_alarm.add_argument("--fade-in", type=int, default=30,
                         help="Fade-in seconds (default: 30)")
    p_alarm.add_argument("--fade-out", type=int, default=0,
                         help="Fade-out seconds (default: 0)")
    p_alarm.add_argument("--cancel", action="store_true", help="Cancel current alarm")
    p_alarm.add_argument("--both", action="store_true", help="Set alarm on both buds")

    # activate
    p_act = subparsers.add_parser("activate", parents=[shared],
                                  help="Send ACTIVATE_GROUP to wake up buds")
    p_act.add_argument("--both", action="store_true", help="Activate both buds")
    p_act.add_argument("--major", type=int, default=11, help="Firmware major version (default: 11)")
    p_act.add_argument("--minor", type=int, default=9, help="Firmware minor version (default: 9)")
    p_act.add_argument("--patch", type=int, default=0, help="Firmware patch version (default: 0)")

    # rawplay (diagnostic)
    p_raw = subparsers.add_parser("rawplay", parents=[shared],
                                  help="Diagnostic: raw audio write test")
    p_raw.add_argument("file_id", type=int, help="File ID to write")
    p_raw.add_argument("--volume", type=int, default=30, help="Volume (default: 30)")

    # sounds
    subparsers.add_parser("sounds", parents=[shared], help="List known sound track IDs")

    # convert
    p_conv = subparsers.add_parser("convert", parents=[shared], help="Convert audio to Sleepbuds format")
    p_conv.add_argument("input", help="Input audio file (WAV, MP3, etc.)")
    p_conv.add_argument("--output", "-o", help="Output prefix (default: same as input)")

    # upload
    p_up = subparsers.add_parser("upload", parents=[shared], help="Upload .bin file to Sleepbuds")
    p_up.add_argument("file", help="Path to .bin file")
    p_up.add_argument("--sound-id", "-s", type=int, default=200,
                      help="Sound ID to assign (default: 200)")
    p_up.add_argument("--file-id", "-f", type=int, default=20000,
                      help="File ID (default: 20000)")
    p_up.add_argument("--variant", choices=["drowsy", "kingsley"], default="drowsy",
                      help="Hardware variant (default: drowsy)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # Setup logging
    if args.debug:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s: %(message)s")
    elif args.verbose:
        logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING, format="%(message)s")

    # Run command
    if args.command == "sounds":
        cmd_sounds(args)
    elif args.command == "convert":
        cmd_convert(args)
    else:
        try:
            coro = {
                "scan": cmd_scan,
                "status": cmd_status,
                "services": cmd_services,
                "play": cmd_play,
                "stop": cmd_stop,
                "volume": cmd_volume,
                "phone-free": cmd_phone_free,
                "phone-free-off": cmd_phone_free_off,
                "upload": cmd_upload,
                "alarm": cmd_alarm,
                "activate": cmd_activate,
                "rawplay": cmd_rawplay,
            }[args.command]
            asyncio.run(coro(args))
        except KeyboardInterrupt:
            print("\nCancelled.")
        except Exception as e:
            print(f"Error: {e}")
            if args.debug:
                import traceback
                traceback.print_exc()


if __name__ == "__main__":
    main()
