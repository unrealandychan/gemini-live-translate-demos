"""
Demo 02 — polydub
=================
Universal video/audio dubber powered by Gemini 3.5 Live Translate.

Accepts any URL supported by yt-dlp (YouTube, Twitch, Bilibili, TikTok, …)
or a local file, then produces:
  • dubbed_<lang>.wav   — translated audio
  • subtitles_<lang>.srt — timestamped subtitle track

Architecture:
    yt-dlp (URL) ──► ffmpeg (PCM 16kHz) ──► Gemini Live Translate
    Local file   ──► ffmpeg (PCM 16kHz) ──► Gemini Live Translate
                                                │
                                    output transcript + audio
                                                │
                                        ┌───────┴────────┐
                                  dubbed_<lang>.wav   subtitles_<lang>.srt

Usage:
    python polydub.py --url "https://youtube.com/watch?v=XXX" --target en
    python polydub.py --file episode.mp3 --target zh-Hant
    python polydub.py --url "https://twitch.tv/streamer" --live --target en

Requirements:
    pip install google-genai yt-dlp
    # ffmpeg must be on PATH
"""

from __future__ import annotations

import argparse
import asyncio
import struct
import subprocess
import sys
import wave
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from google import genai
from google.genai import types


# ─── Data types ──────────────────────────────────────────────────────────────

@dataclass
class SubtitleEntry:
    index: int
    start_ms: int
    end_ms: int
    text: str

    def to_srt(self) -> str:
        def fmt(ms: int) -> str:
            td = timedelta(milliseconds=ms)
            total_s = int(td.total_seconds())
            h, rem = divmod(total_s, 3600)
            m, s = divmod(rem, 60)
            ms_part = ms % 1000
            return f"{h:02d}:{m:02d}:{s:02d},{ms_part:03d}"

        return f"{self.index}\n{fmt(self.start_ms)} --> {fmt(self.end_ms)}\n{self.text}\n"


# ─── Audio ingestion ──────────────────────────────────────────────────────────

def get_stream_url(url: str) -> str:
    """Resolve a platform URL to a direct audio stream URL via yt-dlp."""
    cmd = [
        "yt-dlp",
        "--get-url",
        "-f", "bestaudio/best",
        "--no-playlist",
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr.strip()}")
    stream_url = result.stdout.strip().split("\n")[0]
    return stream_url


def pcm_chunk_generator(source: str, chunk_ms: int = 100):
    """Yield raw 16-bit PCM mono 16 kHz chunks from any audio/video source.

    Args:
        source: File path or direct stream URL.
        chunk_ms: Size of each yielded chunk in milliseconds.

    Yields:
        bytes: Raw PCM chunk.
    """
    sample_rate = 16_000
    bytes_per_sample = 2
    channels = 1
    chunk_bytes = int(sample_rate * bytes_per_sample * channels * chunk_ms / 1000)

    cmd = [
        "ffmpeg", "-loglevel", "quiet",
        "-i", source,
        "-f", "s16le",
        "-acodec", "pcm_s16le",
        "-ar", str(sample_rate),
        "-ac", str(channels),
        "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout is not None

    while True:
        chunk = proc.stdout.read(chunk_bytes)
        if not chunk:
            break
        yield chunk

    proc.wait()


# ─── Output writers ───────────────────────────────────────────────────────────

class WavWriter:
    """Incrementally write PCM frames to a WAV file."""

    def __init__(self, path: Path, sample_rate: int = 24_000) -> None:
        self._wav = wave.open(str(path), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(sample_rate)

    def write(self, data: bytes) -> None:
        self._wav.writeframes(data)

    def close(self) -> None:
        self._wav.close()


class SrtWriter:
    """Collect transcript chunks and build a .srt file."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._entries: list[SubtitleEntry] = []
        self._index = 1
        self._elapsed_ms = 0
        self._pending_text = ""
        self._pending_start = 0

    def add_chunk(self, text: str, chunk_duration_ms: int = 100) -> None:
        """Accumulate text; flush to a subtitle entry every ~3 seconds."""
        if not self._pending_text:
            self._pending_start = self._elapsed_ms
        self._pending_text += text
        self._elapsed_ms += chunk_duration_ms

        # Flush on sentence boundaries or every 3 s
        if any(c in text for c in ".!?。！？\n") or (
            self._elapsed_ms - self._pending_start >= 3_000
        ):
            self._flush()

    def _flush(self) -> None:
        if not self._pending_text.strip():
            return
        self._entries.append(SubtitleEntry(
            index=self._index,
            start_ms=self._pending_start,
            end_ms=self._elapsed_ms,
            text=self._pending_text.strip(),
        ))
        self._index += 1
        self._pending_text = ""

    def save(self) -> None:
        self._flush()
        with open(self._path, "w", encoding="utf-8") as f:
            for entry in self._entries:
                f.write(entry.to_srt() + "\n")


# ─── Core translation engine ──────────────────────────────────────────────────

@dataclass
class DubResult:
    wav_path: Path
    srt_path: Path
    input_transcript: str = ""
    output_transcript: str = ""


async def run_translation(
    source: str,
    target_lang: str,
    output_dir: Path,
    stem: str,
    is_live: bool = False,
) -> DubResult:
    """Stream audio source through Gemini Live Translate; write WAV + SRT.

    Args:
        source: Direct audio URL or local file path.
        target_lang: BCP-47 target language code.
        output_dir: Directory to write output files.
        stem: Base filename stem (without extension).
        is_live: If True, stream without a defined end.

    Returns:
        DubResult with paths to created files.
    """
    wav_path = output_dir / f"{stem}_{target_lang}.wav"
    srt_path = output_dir / f"{stem}_{target_lang}.srt"

    wav_writer = WavWriter(wav_path)
    srt_writer = SrtWriter(srt_path)
    in_transcript_parts: list[str] = []
    out_transcript_parts: list[str] = []

    client = genai.Client()
    model = "gemini-3.5-live-translate-preview"

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        translation_config=types.TranslationConfig(
            target_language_code=target_lang,
            echo_target_language=False,
        ),
    )

    chunks = list(pcm_chunk_generator(source))  # For VOD: materialise list
    total = len(chunks)
    print(f"📦 {total} chunks  (~{total * 0.1:.0f}s)  →  {wav_path.name}")

    async with client.aio.live.connect(model=model, config=config) as session:

        async def send_audio() -> None:
            for i, chunk in enumerate(chunks, 1):
                await session.send_realtime_input(
                    audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                )
                await asyncio.sleep(0.1)
                if i % 100 == 0:
                    pct = i / total * 100
                    print(f"  ↑ {pct:.0f}%  sent", flush=True)

        async def receive_output() -> None:
            async for response in session.receive():
                sc = response.server_content
                if not sc:
                    continue
                if sc.input_transcription and sc.input_transcription.text:
                    in_transcript_parts.append(sc.input_transcription.text)
                if sc.output_transcription and sc.output_transcription.text:
                    text = sc.output_transcription.text
                    out_transcript_parts.append(text)
                    srt_writer.add_chunk(text)
                if sc.model_turn:
                    for part in sc.model_turn.parts:
                        if part.inline_data:
                            wav_writer.write(part.inline_data.data)

        send_task = asyncio.create_task(send_audio())
        recv_task = asyncio.create_task(receive_output())

        await send_task
        await asyncio.sleep(5)  # Drain final chunks
        recv_task.cancel()

    wav_writer.close()
    srt_writer.save()

    return DubResult(
        wav_path=wav_path,
        srt_path=srt_path,
        input_transcript=" ".join(in_transcript_parts),
        output_transcript=" ".join(out_transcript_parts),
    )


# ─── CLI ─────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="polydub — universal multi-platform video/audio dubber",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python polydub.py --url "https://youtube.com/watch?v=XXX" --target en
  python polydub.py --file episode.mp3 --target zh-Hant
  python polydub.py --file lecture.mp4 --target ja --output ./output
        """,
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="Platform URL (YouTube, Twitch, Bilibili, TikTok…)")
    src.add_argument("--file", help="Local audio/video file")
    parser.add_argument("--target", default="en", help="BCP-47 target language (default: en)")
    parser.add_argument("--output", default=".", help="Output directory (default: current dir)")
    parser.add_argument("--live", action="store_true", help="Treat as live stream (no defined end)")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.url:
        print(f"🔗 Resolving stream URL via yt-dlp…")
        try:
            source = get_stream_url(args.url)
        except RuntimeError as e:
            print(f"❌ {e}", file=sys.stderr)
            sys.exit(1)
        stem = "video"
    else:
        source = args.file
        stem = Path(args.file).stem

    print(f"🎙  Source : {source[:80]}{'…' if len(source) > 80 else ''}")
    print(f"🌐 Target : {args.target}")
    print(f"📁 Output : {output_dir.resolve()}\n")

    result = asyncio.run(
        run_translation(source, args.target, output_dir, stem, is_live=args.live)
    )

    print(f"\n✅ Done!")
    print(f"   🔊 Audio   → {result.wav_path}")
    print(f"   📄 Subtitles → {result.srt_path}")
    if result.output_transcript:
        print(f"\n📝 Translation preview:\n{result.output_transcript[:300]}…")


if __name__ == "__main__":
    main()
