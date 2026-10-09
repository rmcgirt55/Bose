"""
Audio format converter for Bose Sleepbuds II.

Converts WAV, MP3, FLAC, OGG, and other audio formats to the
Sleepbuds' raw PCM format: 24-bit, 8192 Hz, mono, little-endian.
"""

import struct
import logging
import wave
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Sleepbuds audio specs
SAMPLE_RATE = 8192
BIT_DEPTH = 24
BYTES_PER_SAMPLE = 3  # 24-bit = 3 bytes


def _read_wav(path: Path) -> tuple[bytes, int, int, int]:
    """Read a WAV file and return (raw_data, sample_rate, sample_width, channels)."""
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
        return frames, rate, sample_width, channels


def _resample_simple(samples: list[float], src_rate: int, dst_rate: int) -> list[float]:
    """Simple linear interpolation resampling."""
    if src_rate == dst_rate:
        return samples

    ratio = src_rate / dst_rate
    n_out = int(len(samples) / ratio)
    out = []

    for i in range(n_out):
        src_pos = i * ratio
        idx = int(src_pos)
        frac = src_pos - idx

        if idx + 1 < len(samples):
            val = samples[idx] * (1.0 - frac) + samples[idx + 1] * frac
        else:
            val = samples[idx] if idx < len(samples) else 0.0
        out.append(val)

    return out


def _pcm_bytes_to_floats(data: bytes, sample_width: int, channels: int) -> tuple[list[float], list[float]]:
    """Convert raw PCM bytes to float samples [-1.0, 1.0], return (left, right)."""
    if sample_width == 1:
        fmt = "B"  # unsigned 8-bit
        max_val = 128.0
        offset = -128
    elif sample_width == 2:
        fmt = "<h"  # signed 16-bit LE
        max_val = 32768.0
        offset = 0
    elif sample_width == 3:
        fmt = None  # handle manually
        max_val = 8388608.0
        offset = 0
    elif sample_width == 4:
        fmt = "<i"  # signed 32-bit LE
        max_val = 2147483648.0
        offset = 0
    else:
        raise ValueError(f"Unsupported sample width: {sample_width}")

    frame_size = sample_width * channels
    n_frames = len(data) // frame_size

    left = []
    right = []

    for i in range(n_frames):
        base = i * frame_size

        for ch in range(channels):
            ch_base = base + ch * sample_width

            if sample_width == 3:
                # 24-bit: read 3 bytes, sign-extend
                b = data[ch_base:ch_base + 3]
                if len(b) < 3:
                    break
                val = b[0] | (b[1] << 8) | (b[2] << 16)
                if val & 0x800000:
                    val -= 0x1000000
            else:
                val = struct.unpack_from(fmt, data, ch_base)[0] + offset

            sample = val / max_val

            if ch == 0:
                left.append(sample)
            elif ch == 1:
                right.append(sample)

    if channels == 1:
        right = list(left)

    return left, right


def _floats_to_24bit_le(samples: list[float]) -> bytes:
    """Convert float samples [-1.0, 1.0] to 24-bit little-endian PCM bytes."""
    max_val = 8388607  # 2^23 - 1
    out = bytearray()

    for s in samples:
        clamped = max(-1.0, min(1.0, s))
        val = int(clamped * max_val)

        if val < 0:
            val += 0x1000000  # two's complement for 24-bit

        out.append(val & 0xFF)
        out.append((val >> 8) & 0xFF)
        out.append((val >> 16) & 0xFF)

    return bytes(out)


def convert_wav_to_sleepbuds(
    input_path: str,
    output_prefix: str,
) -> tuple[str, str]:
    """
    Convert a WAV file to Sleepbuds format.

    Creates two files: {output_prefix}_left.bin and {output_prefix}_right.bin

    Args:
        input_path: Path to source WAV file
        output_prefix: Output file path prefix

    Returns:
        Tuple of (left_path, right_path)
    """
    src = Path(input_path)
    logger.info("Converting %s to Sleepbuds format...", src.name)

    raw_data, src_rate, sample_width, channels = _read_wav(src)
    logger.info("Source: %d Hz, %d-bit, %d channel(s), %d bytes",
                src_rate, sample_width * 8, channels, len(raw_data))

    # Convert to floats
    left_floats, right_floats = _pcm_bytes_to_floats(raw_data, sample_width, channels)
    logger.info("Decoded %d frames", len(left_floats))

    # Resample to 8192 Hz
    left_resampled = _resample_simple(left_floats, src_rate, SAMPLE_RATE)
    right_resampled = _resample_simple(right_floats, src_rate, SAMPLE_RATE)
    logger.info("Resampled to %d Hz: %d samples", SAMPLE_RATE, len(left_resampled))

    duration_secs = len(left_resampled) / SAMPLE_RATE
    logger.info("Duration: %.1f seconds", duration_secs)

    # Convert to 24-bit little-endian PCM
    left_bytes = _floats_to_24bit_le(left_resampled)
    right_bytes = _floats_to_24bit_le(right_resampled)

    # Write output files
    left_path = f"{output_prefix}_left.bin"
    right_path = f"{output_prefix}_right.bin"

    with open(left_path, "wb") as f:
        f.write(left_bytes)
    with open(right_path, "wb") as f:
        f.write(right_bytes)

    logger.info("Written: %s (%d bytes)", left_path, len(left_bytes))
    logger.info("Written: %s (%d bytes)", right_path, len(right_bytes))

    expected_size = int(duration_secs * SAMPLE_RATE * BYTES_PER_SAMPLE)
    logger.info("Expected size: %d bytes (actual: %d)", expected_size, len(left_bytes))

    return left_path, right_path


def convert_any_to_sleepbuds(
    input_path: str,
    output_prefix: str,
) -> tuple[str, str]:
    """
    Convert any audio file to Sleepbuds format using pydub (if available)
    or fall back to WAV-only conversion.

    Args:
        input_path: Path to source audio file (WAV, MP3, FLAC, OGG, etc.)
        output_prefix: Output file path prefix

    Returns:
        Tuple of (left_path, right_path)
    """
    src = Path(input_path)

    if src.suffix.lower() == ".wav":
        return convert_wav_to_sleepbuds(input_path, output_prefix)

    # Try pydub for non-WAV formats
    try:
        from pydub import AudioSegment
    except ImportError:
        raise RuntimeError(
            f"Cannot convert {src.suffix} files without pydub. "
            "Install with: pip install pydub\n"
            "Also requires ffmpeg: brew install ffmpeg"
        )

    logger.info("Converting %s via pydub...", src.name)

    audio = AudioSegment.from_file(str(src))
    logger.info("Source: %d Hz, %d-bit, %d channel(s), %.1f seconds",
                audio.frame_rate, audio.sample_width * 8,
                audio.channels, audio.duration_seconds)

    # Export as WAV to a temp file, then convert
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name
        audio.export(tmp_path, format="wav")

    try:
        return convert_wav_to_sleepbuds(tmp_path, output_prefix)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def get_audio_info(path: str) -> dict:
    """Get basic info about an audio file."""
    src = Path(path)
    if src.suffix.lower() == ".wav":
        raw_data, rate, width, channels = _read_wav(src)
        n_frames = len(raw_data) // (width * channels)
        return {
            "format": "WAV",
            "sample_rate": rate,
            "bit_depth": width * 8,
            "channels": channels,
            "duration_secs": n_frames / rate,
            "file_size": src.stat().st_size,
        }
    return {
        "format": src.suffix.upper().lstrip("."),
        "file_size": src.stat().st_size,
    }


def validate_bin_file(path: str) -> dict:
    """Validate a .bin file matches expected Sleepbuds format."""
    size = Path(path).stat().st_size
    n_samples = size // BYTES_PER_SAMPLE
    duration = n_samples / SAMPLE_RATE
    remainder = size % BYTES_PER_SAMPLE

    return {
        "file_size": size,
        "samples": n_samples,
        "duration_secs": duration,
        "valid_alignment": remainder == 0,
        "sample_rate": SAMPLE_RATE,
        "bit_depth": BIT_DEPTH,
    }
