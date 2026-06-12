"""
Demo 01 — Hello Translate
=========================
The simplest possible Gemini 3.5 Live Translate example.

Reads a local WAV / MP3 file (or any ffmpeg-supported format), streams it
to the Gemini Live Translate API in 100 ms PCM chunks, and prints the
live input + output transcripts as they arrive.

Usage:
    python hello_translate.py --file sample.mp3 --target zh-Hant
    python hello_translate.py --file speech.wav --target en --echo

Requirements:
    pip install google-genai pydub
    # ffmpeg must be on PATH
"""

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

from google import genai
from google.genai import types


# ── helpers ──────────────────────────────────────────────────────────────────

def read_pcm_chunks(file_path: str, chunk_ms: int = 100) -> list[bytes]:
    """Decode any audio file to raw PCM 16 kHz mono via ffmpeg.

    Returns a list of 100 ms byte chunks ready for the Live API.
    """
    sample_rate = 16_000
    bytes_per_sample = 2  # s16le
    channels = 1
    chunk_bytes = int(sample_rate * bytes_per_sample * channels * chunk_ms / 1000)

    cmd = [
        "ffmpeg", "-loglevel", "quiet",
        "-i", file_path,
        "-f", "s16le",
        "-acodec", "pcm_s16le",
        "-ar", str(sample_rate),
        "-ac", str(channels),
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.decode()}")

    raw = result.stdout
    return [raw[i:i + chunk_bytes] for i in range(0, len(raw), chunk_bytes)]


# ── main translation coroutine ────────────────────────────────────────────────

async def translate(file_path: str, target_lang: str, echo: bool) -> None:
    """Stream audio file through Gemini 3.5 Live Translate and print transcripts."""
    print(f"📂 Loading: {file_path}")
    chunks = read_pcm_chunks(file_path)
    print(f"✅ {len(chunks)} chunks (~{len(chunks) * 100 / 1000:.1f}s of audio)\n")

    client = genai.Client()
    model = "gemini-3.5-live-translate-preview"

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        translation_config=types.TranslationConfig(
            target_language_code=target_lang,
            echo_target_language=echo,
        ),
    )

    print(f"🌐 Connecting — target language: {target_lang}\n")
    print("─" * 60)

    async with client.aio.live.connect(model=model, config=config) as session:

        async def send_audio() -> None:
            """Feed PCM chunks into the session."""
            for chunk in chunks:
                await session.send_realtime_input(
                    audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                )
                # Honour real-time pacing so the model keeps up
                await asyncio.sleep(0.1)

        async def receive_transcripts() -> None:
            """Print input + output transcripts as they stream in."""
            async for response in session.receive():
                sc = response.server_content
                if not sc:
                    continue
                if sc.input_transcription and sc.input_transcription.text:
                    print(f"🗣  Input : {sc.input_transcription.text}")
                if sc.output_transcription and sc.output_transcription.text:
                    print(f"🔤 Output: {sc.output_transcription.text}\n")

        # Run both coroutines concurrently; stop receiving when sending is done
        send_task = asyncio.create_task(send_audio())
        recv_task = asyncio.create_task(receive_transcripts())

        await send_task
        # Give a brief window for final transcripts to arrive
        await asyncio.sleep(3)
        recv_task.cancel()

    print("\n" + "─" * 60)
    print("✅ Done.")


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gemini 3.5 Live Translate — quickstart")
    parser.add_argument("--file", required=True, help="Path to input audio/video file")
    parser.add_argument("--target", default="en", help="BCP-47 target language code (default: en)")
    parser.add_argument("--echo", action="store_true",
                        help="Echo input audio when it is already in the target language")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if not Path(args.file).exists():
        print(f"❌ File not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    asyncio.run(translate(args.file, args.target, args.echo))
