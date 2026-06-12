"""
Demo 03 — Podcast Dubber
========================
Batch-process a folder of podcast episodes into multiple dubbed languages.

Given a directory of audio files, this tool:
  1. Discovers all supported audio files
  2. Translates each episode into one or more target languages (concurrently)
  3. Writes output WAV + SRT per episode per language
  4. Produces a manifest.json summary

Ideal workflow for podcast creators who want to expand audience reach by
publishing dubbed versions of every episode automatically.

Usage:
    python podcast_dubber.py --input ./episodes --targets en,zh-Hant,ja
    python podcast_dubber.py --input ./episodes --targets en --workers 2

Requirements:
    pip install google-genai rich
    # ffmpeg must be on PATH
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import wave
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from google import genai
from google.genai import types


console = Console()

SUPPORTED_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".mp4", ".webm"}


# ─── Data types ──────────────────────────────────────────────────────────────

@dataclass
class EpisodeResult:
    episode: str
    language: str
    wav_path: str
    srt_path: str
    duration_s: float
    status: str = "ok"
    error: str = ""


@dataclass
class DubManifest:
    created_at: str
    input_dir: str
    target_languages: list[str]
    results: list[EpisodeResult] = field(default_factory=list)


# ─── PCM helpers ─────────────────────────────────────────────────────────────

def get_audio_chunks(file_path: Path, chunk_ms: int = 100) -> list[bytes]:
    """Decode audio file to raw PCM 16 kHz mono chunks via ffmpeg."""
    sample_rate = 16_000
    chunk_bytes = int(sample_rate * 2 * 1 * chunk_ms / 1000)

    cmd = [
        "ffmpeg", "-loglevel", "quiet",
        "-i", str(file_path),
        "-f", "s16le", "-acodec", "pcm_s16le",
        "-ar", str(sample_rate), "-ac", "1",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True)
    raw = result.stdout
    return [raw[i:i + chunk_bytes] for i in range(0, len(raw), chunk_bytes)]


class WavWriter:
    def __init__(self, path: Path, sample_rate: int = 24_000) -> None:
        self._wav = wave.open(str(path), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(sample_rate)

    def write(self, data: bytes) -> None:
        self._wav.writeframes(data)

    def close(self) -> None:
        self._wav.close()


def write_srt(path: Path, entries: list[tuple[int, int, str]]) -> None:
    """Write SRT file from list of (start_ms, end_ms, text) tuples."""
    with open(path, "w", encoding="utf-8") as f:
        for i, (start, end, text) in enumerate(entries, 1):
            def fmt(ms: int) -> str:
                td = timedelta(milliseconds=ms)
                total_s = int(td.total_seconds())
                h, rem = divmod(total_s, 3600)
                m, s = divmod(rem, 60)
                return f"{h:02d}:{m:02d}:{s:02d},{ms % 1000:03d}"
            f.write(f"{i}\n{fmt(start)} --> {fmt(end)}\n{text.strip()}\n\n")


# ─── Core translation ─────────────────────────────────────────────────────────

async def dub_episode(
    file_path: Path,
    target_lang: str,
    output_dir: Path,
    task_id: int,
    progress: Progress,
) -> EpisodeResult:
    """Translate a single episode into one target language.

    Args:
        file_path: Path to source audio file.
        target_lang: BCP-47 target language code.
        output_dir: Directory to write output files.
        task_id: Rich progress task ID for updates.
        progress: Rich Progress instance.

    Returns:
        EpisodeResult with details of the completed job.
    """
    wav_path = output_dir / f"{file_path.stem}_{target_lang}.wav"
    srt_path = output_dir / f"{file_path.stem}_{target_lang}.srt"

    try:
        chunks = get_audio_chunks(file_path)
        total_chunks = len(chunks)
        progress.update(task_id, total=total_chunks)

        wav_writer = WavWriter(wav_path)
        srt_entries: list[tuple[int, int, str]] = []
        elapsed_ms = 0
        pending_text = ""
        pending_start = 0

        client = genai.Client()
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription=types.AudioTranscriptionConfig(),
            translation_config=types.TranslationConfig(
                target_language_code=target_lang,
                echo_target_language=False,
            ),
        )

        async with client.aio.live.connect(
            model="gemini-3.5-live-translate-preview", config=config
        ) as session:

            async def send_audio() -> None:
                for chunk in chunks:
                    await session.send_realtime_input(
                        audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                    )
                    await asyncio.sleep(0.1)
                    progress.advance(task_id)

            async def receive_output() -> None:
                nonlocal elapsed_ms, pending_text, pending_start
                async for response in session.receive():
                    sc = response.server_content
                    if not sc:
                        continue
                    if sc.output_transcription and sc.output_transcription.text:
                        text = sc.output_transcription.text
                        if not pending_text:
                            pending_start = elapsed_ms
                        pending_text += text
                        elapsed_ms += 100
                        if any(c in text for c in ".!?。！？\n") or (
                            elapsed_ms - pending_start >= 3000
                        ):
                            if pending_text.strip():
                                srt_entries.append((pending_start, elapsed_ms, pending_text))
                            pending_text = ""
                    if sc.model_turn:
                        for part in sc.model_turn.parts:
                            if part.inline_data:
                                wav_writer.write(part.inline_data.data)

            send_task = asyncio.create_task(send_audio())
            recv_task = asyncio.create_task(receive_output())
            await send_task
            await asyncio.sleep(5)
            recv_task.cancel()

        wav_writer.close()
        if pending_text.strip():
            srt_entries.append((pending_start, elapsed_ms, pending_text))
        write_srt(srt_path, srt_entries)

        duration_s = total_chunks * 0.1
        return EpisodeResult(
            episode=file_path.name,
            language=target_lang,
            wav_path=str(wav_path),
            srt_path=str(srt_path),
            duration_s=duration_s,
        )

    except Exception as exc:  # noqa: BLE001
        return EpisodeResult(
            episode=file_path.name,
            language=target_lang,
            wav_path="",
            srt_path="",
            duration_s=0,
            status="error",
            error=str(exc),
        )


# ─── Batch orchestration ──────────────────────────────────────────────────────

async def run_batch(
    input_dir: Path,
    target_langs: list[str],
    output_dir: Path,
    max_workers: int,
) -> DubManifest:
    """Discover episodes and dub them concurrently with a semaphore gate.

    Args:
        input_dir: Directory containing audio files.
        target_langs: List of BCP-47 target language codes.
        output_dir: Output directory for all dubbed files.
        max_workers: Maximum concurrent translation sessions.

    Returns:
        DubManifest summarising all results.
    """
    episodes = [
        f for f in sorted(input_dir.iterdir())
        if f.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    if not episodes:
        console.print(f"[red]No supported audio files found in {input_dir}[/]")
        return DubManifest(
            created_at=datetime.now().isoformat(),
            input_dir=str(input_dir),
            target_languages=target_langs,
        )

    console.print(f"\n🎙  Found [bold]{len(episodes)}[/] episodes × "
                  f"[bold]{len(target_langs)}[/] languages "
                  f"= [bold]{len(episodes) * len(target_langs)}[/] jobs\n")

    manifest = DubManifest(
        created_at=datetime.now().isoformat(),
        input_dir=str(input_dir),
        target_languages=target_langs,
    )

    semaphore = asyncio.Semaphore(max_workers)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:

        async def job(ep: Path, lang: str) -> EpisodeResult:
            async with semaphore:
                task_id = progress.add_task(
                    f"{ep.name[:30]} → {lang}", total=None
                )
                result = await dub_episode(ep, lang, output_dir, task_id, progress)
                status_icon = "✅" if result.status == "ok" else "❌"
                progress.update(task_id, description=f"{status_icon} {ep.name[:30]} → {lang}")
                return result

        tasks = [job(ep, lang) for ep in episodes for lang in target_langs]
        results = await asyncio.gather(*tasks)
        manifest.results.extend(results)

    return manifest


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="podcast-dubber — batch dub a folder of podcast episodes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python podcast_dubber.py --input ./episodes --targets en,zh-Hant,ja
  python podcast_dubber.py --input ./episodes --targets en --output ./dubbed --workers 2
        """,
    )
    parser.add_argument("--input", required=True, help="Directory containing audio files")
    parser.add_argument(
        "--targets", default="en",
        help="Comma-separated BCP-47 language codes (default: en)"
    )
    parser.add_argument("--output", default="./dubbed", help="Output directory")
    parser.add_argument("--workers", type=int, default=2,
                        help="Max concurrent translation sessions (default: 2)")
    args = parser.parse_args()

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    target_langs = [t.strip() for t in args.targets.split(",")]

    manifest = asyncio.run(
        run_batch(input_dir, target_langs, output_dir, args.workers)
    )

    # Save manifest
    manifest_path = output_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(asdict(manifest), f, indent=2, ensure_ascii=False)

    # Summary table
    table = Table(title="Dubbing Summary", show_lines=True)
    table.add_column("Episode")
    table.add_column("Language")
    table.add_column("Duration")
    table.add_column("Status")

    ok = sum(1 for r in manifest.results if r.status == "ok")
    err = len(manifest.results) - ok

    for r in manifest.results:
        icon = "✅" if r.status == "ok" else "❌"
        table.add_row(r.episode, r.language, f"{r.duration_s:.0f}s", icon)

    console.print()
    console.print(table)
    console.print(
        f"\n✅ [green]{ok} succeeded[/]  ❌ [red]{err} failed[/]  "
        f"📄 manifest → {manifest_path}"
    )


if __name__ == "__main__":
    main()
