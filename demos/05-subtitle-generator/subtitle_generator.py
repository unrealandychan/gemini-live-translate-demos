"""
Demo 05 — Subtitle Generator
============================
Generate bilingual SRT subtitle files from any video/audio source.

Takes a local file or a URL (resolved via yt-dlp), streams the audio through
the Gemini 3.5 Live Translate API, and produces **three** .srt files:

    {stem}_bilingual.srt  — each entry: Line 1 = original, Line 2 = translation
    {stem}_original.srt   — original-language captions only
    {stem}_{target}.srt   — translation-language subtitles only

Both ``input_audio_transcription`` (original language) and
``output_audio_transcription`` (translation) are collected and aligned into
bilingual entries using a shared timing clock derived from the audio chunks
being streamed.

Architecture::

    URL  ──► yt-dlp ──► direct stream URL ──┐
                                             ├──► ffmpeg (PCM 16 kHz) ──► Gemini Live Translate
    File ────────────────────────────────────┘
                                                         │
                                         input_transcription + output_transcription
                                                         │
                                              ┌──────────┼──────────┐
                                      bilingual.srt  original.srt  {lang}.srt

Usage::

    python subtitle_generator.py --file lecture.mp4 --target es
    python subtitle_generator.py --url "https://youtube.com/watch?v=XXX" --target zh-Hant
    python subtitle_generator.py --file podcast.mp3 --target ja --output ./subs

Requirements::

    pip install google-genai yt-dlp rich
    # ffmpeg must be on PATH
    # GEMINI_API_KEY must be set in environment
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Generator

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from google import genai
from google.genai import types


console = Console()

# ─── Constants ────────────────────────────────────────────────────────────────

CHUNK_MS: int = 100
"""Duration of each PCM audio chunk sent to the Live API, in milliseconds."""

SAMPLE_RATE: int = 16_000
"""PCM sample rate required by the Live Translate API."""

BYTES_PER_SAMPLE: int = 2          # 16-bit signed little-endian
CHANNELS: int = 1                   # mono
CHUNK_BYTES: int = int(SAMPLE_RATE * BYTES_PER_SAMPLE * CHANNELS * CHUNK_MS / 1000)
"""Byte size of one 100 ms PCM chunk (3 200 bytes at 16 kHz / 16-bit / mono)."""

SENTENCE_DELIMITERS: frozenset[str] = frozenset(".!?。！？\n")
"""Characters that signal a natural sentence boundary to trigger a subtitle flush."""

MAX_ENTRY_DURATION_MS: int = 4_000
"""Hard cap on subtitle entry duration; entries are flushed after this many ms."""

DRAIN_SECONDS: float = 5.0
"""Seconds to wait after sending audio for the server to emit remaining transcripts."""

MODEL: str = "gemini-3.5-live-translate-preview"
"""Gemini model identifier for the Live Translate API."""


# ─── Data types ───────────────────────────────────────────────────────────────

@dataclass
class BilingualEntry:
    """One time-aligned subtitle entry carrying both original and translated text.

    Attributes:
        index: 1-based SRT sequence number.
        start_ms: Entry start time in milliseconds.
        end_ms: Entry end time in milliseconds.
        original: Transcript text in the source language.
        translated: Transcript text in the target language.
    """

    index: int
    start_ms: int
    end_ms: int
    original: str
    translated: str


@dataclass
class SubtitleResult:
    """Paths and statistics produced by a subtitle generation run.

    Attributes:
        bilingual_srt: Path to the bilingual (two-line) SRT file.
        original_srt: Path to the original-language SRT file.
        translated_srt: Path to the translated-language SRT file.
        entry_count: Total number of bilingual entries written.
        duration_s: Approximate audio duration processed, in seconds.
        target_lang: BCP-47 code of the target language.
    """

    bilingual_srt: Path
    original_srt: Path
    translated_srt: Path
    entry_count: int
    duration_s: float
    target_lang: str


# ─── SRT helpers ──────────────────────────────────────────────────────────────

def _fmt_time(ms: int) -> str:
    """Format a millisecond offset as an SRT timestamp string.

    Args:
        ms: Time offset in milliseconds.

    Returns:
        A string in the form ``HH:MM:SS,mmm`` as required by the SRT spec.

    Example::

        >>> _fmt_time(61_500)
        '00:01:01,500'
    """
    td = timedelta(milliseconds=ms)
    total_s = int(td.total_seconds())
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms % 1000:03d}"


def write_srt_file(
    path: Path,
    entries: list[BilingualEntry],
    track: str,
) -> None:
    """Write a .srt subtitle file for the requested track.

    Each entry is re-indexed sequentially from 1 in the output.  Entries
    whose relevant text is empty are silently skipped.

    Args:
        path: Destination .srt file path (parent directories must exist).
        entries: Ordered list of :class:`BilingualEntry` objects.
        track: Which text to include.  One of:

            * ``"bilingual"`` — Line 1: original, Line 2: translation.
            * ``"original"``  — original-language text only.
            * ``"translated"`` — translated text only.

    Raises:
        ValueError: If *track* is not one of the recognised values.
    """
    if track not in ("bilingual", "original", "translated"):
        raise ValueError(
            f"Unknown track {track!r}. Choose from 'bilingual', 'original', 'translated'."
        )

    with open(path, "w", encoding="utf-8") as fh:
        idx = 1
        for entry in entries:
            if track == "original":
                body = entry.original
            elif track == "translated":
                body = entry.translated
            else:  # bilingual
                parts: list[str] = []
                if entry.original:
                    parts.append(entry.original)
                if entry.translated:
                    parts.append(entry.translated)
                body = "\n".join(parts)

            if not body.strip():
                continue

            fh.write(
                f"{idx}\n"
                f"{_fmt_time(entry.start_ms)} --> {_fmt_time(entry.end_ms)}\n"
                f"{body}\n\n"
            )
            idx += 1


# ─── Audio ingestion ──────────────────────────────────────────────────────────

def resolve_url(url: str) -> str:
    """Resolve a platform URL to a direct audio stream URL via yt-dlp.

    Supports any of the 1 000+ sites that yt-dlp handles (YouTube, Bilibili,
    TikTok, Twitter/X, Instagram, Twitch, NicoNico, …).

    Args:
        url: A video or audio page URL on any yt-dlp-supported platform.

    Returns:
        A direct CDN URL that ffmpeg can ingest without further authentication.

    Raises:
        RuntimeError: If yt-dlp exits with a non-zero status code.
    """
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


def stream_pcm_chunks(source: str) -> Generator[bytes, None, None]:
    """Stream raw 16-bit PCM mono 16 kHz chunks from any audio/video source.

    Spawns ffmpeg as a subprocess and yields consecutive 100 ms byte buffers
    as they are decoded, enabling streaming without buffering the full file.

    Args:
        source: A local file path (absolute or relative) or a direct remote
            URL that ffmpeg can open (e.g. a yt-dlp-resolved CDN link).

    Yields:
        bytes: A ``CHUNK_BYTES``-sized (3 200 bytes) raw PCM buffer representing
            100 ms of mono 16 kHz audio.

    Raises:
        RuntimeError: If ffmpeg exits with a non-zero status.
    """
    cmd = [
        "ffmpeg", "-loglevel", "quiet",
        "-i", source,
        "-f", "s16le",
        "-acodec", "pcm_s16le",
        "-ar", str(SAMPLE_RATE),
        "-ac", str(CHANNELS),
        "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None

    try:
        while True:
            chunk = proc.stdout.read(CHUNK_BYTES)
            if not chunk:
                break
            yield chunk
    finally:
        proc.wait()
        if proc.returncode not in (0, None) and proc.stderr:
            err = proc.stderr.read().decode(errors="replace")
            raise RuntimeError(f"ffmpeg exited {proc.returncode}: {err[:200]}")


# ─── Bilingual subtitle builder ───────────────────────────────────────────────

class BilingualSubtitleBuilder:
    """Accumulates streaming transcript fragments into aligned bilingual SRT entries.

    Maintains two independent text buffers — one for the original language
    (``input_audio_transcription``) and one for the translation
    (``output_audio_transcription``) — sharing a single audio-time clock.

    Entries are flushed to the completed list whenever:

    * Either buffer receives text containing a sentence boundary character, **or**
    * The current window has exceeded :attr:`max_entry_ms` milliseconds.

    The clock is advanced externally by the audio-sending coroutine via
    :meth:`set_elapsed_ms` so that timing accurately reflects audio position
    rather than wall-clock time.

    Args:
        max_entry_ms: Maximum duration in milliseconds before an entry is
            force-flushed, regardless of sentence boundaries.
    """

    def __init__(self, max_entry_ms: int = MAX_ENTRY_DURATION_MS) -> None:
        """Initialise the builder with an empty state and the given flush threshold.

        Args:
            max_entry_ms: Maximum subtitle window duration in milliseconds
                before a forced flush occurs (default: ``MAX_ENTRY_DURATION_MS``).
        """
        self._max_entry_ms: int = max_entry_ms
        self._entries: list[BilingualEntry] = []
        self._index: int = 1

        # Shared audio-time clock (milliseconds of audio sent so far)
        self._elapsed_ms: int = 0

        # Pending text buffers
        self._orig_text: str = ""
        self._trans_text: str = ""

        # Per-buffer and per-window start timestamps
        self._orig_start_ms: int = 0
        self._trans_start_ms: int = 0
        self._window_start_ms: int = 0

    def set_elapsed_ms(self, ms: int) -> None:
        """Advance the shared audio-time clock.

        Should be called by the audio-sending coroutine after each chunk is
        dispatched, so both transcript handlers see an up-to-date time offset.

        Args:
            ms: Cumulative audio time in milliseconds sent so far.
        """
        self._elapsed_ms = ms

    def add_original(self, text: str) -> None:
        """Append a source-language transcript fragment and conditionally flush.

        Args:
            text: A text fragment from ``server_content.input_transcription``.
        """
        if not self._orig_text:
            self._orig_start_ms = self._elapsed_ms
        self._orig_text += text
        self._maybe_flush(text)

    def add_translated(self, text: str) -> None:
        """Append a translated transcript fragment and conditionally flush.

        Args:
            text: A text fragment from ``server_content.output_transcription``.
        """
        if not self._trans_text:
            self._trans_start_ms = self._elapsed_ms
        self._trans_text += text
        self._maybe_flush(text)

    def _maybe_flush(self, trigger_text: str) -> None:
        """Flush both buffers if a sentence boundary or time limit is reached.

        Args:
            trigger_text: The most recently received text fragment, used to
                check for sentence-ending characters.
        """
        duration_ms = self._elapsed_ms - self._window_start_ms
        has_boundary = any(c in trigger_text for c in SENTENCE_DELIMITERS)
        if has_boundary or duration_ms >= self._max_entry_ms:
            self._flush()

    def _flush(self) -> None:
        """Unconditionally emit a :class:`BilingualEntry` from current pending buffers.

        No-ops if both buffers are empty.  Resets both buffers and advances
        the window start to the current elapsed time.
        """
        orig = self._orig_text.strip()
        trans = self._trans_text.strip()
        if not orig and not trans:
            return

        # Start time is the earliest non-empty buffer's start
        start_ms = min(
            self._orig_start_ms if orig else self._elapsed_ms,
            self._trans_start_ms if trans else self._elapsed_ms,
        )
        self._entries.append(BilingualEntry(
            index=self._index,
            start_ms=start_ms,
            end_ms=self._elapsed_ms,
            original=orig,
            translated=trans,
        ))
        self._index += 1
        self._orig_text = ""
        self._trans_text = ""
        self._window_start_ms = self._elapsed_ms

    def finalize(self) -> list[BilingualEntry]:
        """Flush any remaining pending text and return all completed entries.

        Should be called once after the receive coroutine has been cancelled
        and all server responses have been drained.

        Returns:
            A list of :class:`BilingualEntry` objects in chronological order.
        """
        self._flush()
        return list(self._entries)


# ─── Core translation engine ──────────────────────────────────────────────────

async def generate_subtitles(
    source: str,
    target_lang: str,
    output_dir: Path,
    stem: str,
    progress: Progress,
    task_id: TaskID,
) -> SubtitleResult:
    """Stream audio through Gemini Live Translate and write bilingual SRT files.

    Opens a single Live Translate session and concurrently:

    * Sends PCM audio chunks at real-time pacing (100 ms sleep between chunks),
      advancing a shared audio clock after each chunk.
    * Receives server events, dispatching ``input_transcription`` fragments to
      the original-language buffer and ``output_transcription`` fragments to the
      translation buffer of a :class:`BilingualSubtitleBuilder`.

    On completion, writes three .srt files to *output_dir*:

    * ``{stem}_bilingual.srt``  — both tracks, two lines per entry
    * ``{stem}_original.srt``   — original-language captions only
    * ``{stem}_{target_lang}.srt`` — translated subtitles only

    Args:
        source: Local file path or a yt-dlp-resolved direct stream URL.
        target_lang: BCP-47 language code for the translation target.
        output_dir: Directory in which to write output files (must exist).
        stem: Base filename stem, e.g. ``"my_video"`` → ``"my_video_bilingual.srt"``.
        progress: Active Rich :class:`Progress` instance for live updates.
        task_id: The task handle within *progress* to advance.

    Returns:
        A :class:`SubtitleResult` containing paths to the three output files
        and summary metadata.

    Raises:
        RuntimeError: Propagated from :func:`stream_pcm_chunks` if ffmpeg fails.
    """
    bilingual_path = output_dir / f"{stem}_bilingual.srt"
    original_path = output_dir / f"{stem}_original.srt"
    translated_path = output_dir / f"{stem}_{target_lang}.srt"

    # Materialise all chunks upfront so we have an accurate total for the progress bar
    chunks: list[bytes] = list(stream_pcm_chunks(source))
    total_chunks = len(chunks)
    progress.update(task_id, total=total_chunks)

    builder = BilingualSubtitleBuilder()

    client = genai.Client()
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        translation_config=types.TranslationConfig(
            target_language_code=target_lang,
            echo_target_language=False,
        ),
    )

    # A single-element list acts as a mutable closure cell for the shared clock
    clock: list[int] = [0]

    async with client.aio.live.connect(model=MODEL, config=config) as session:

        async def send_audio() -> None:
            """Feed every PCM chunk to the Live session at real-time pace."""
            for chunk in chunks:
                await session.send_realtime_input(
                    audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                )
                await asyncio.sleep(CHUNK_MS / 1000.0)
                clock[0] += CHUNK_MS
                builder.set_elapsed_ms(clock[0])
                progress.advance(task_id)

        async def receive_output() -> None:
            """Dispatch incoming transcript events to the bilingual builder."""
            async for response in session.receive():
                sc = response.server_content
                if not sc:
                    continue
                if sc.input_transcription and sc.input_transcription.text:
                    builder.add_original(sc.input_transcription.text)
                if sc.output_transcription and sc.output_transcription.text:
                    builder.add_translated(sc.output_transcription.text)

        send_task = asyncio.create_task(send_audio())
        recv_task = asyncio.create_task(receive_output())

        await send_task
        # Allow extra time for the server to emit trailing transcript events
        await asyncio.sleep(DRAIN_SECONDS)
        recv_task.cancel()

    entries = builder.finalize()

    write_srt_file(bilingual_path, entries, "bilingual")
    write_srt_file(original_path, entries, "original")
    write_srt_file(translated_path, entries, "translated")

    return SubtitleResult(
        bilingual_srt=bilingual_path,
        original_srt=original_path,
        translated_srt=translated_path,
        entry_count=len(entries),
        duration_s=total_chunks * CHUNK_MS / 1000.0,
        target_lang=target_lang,
    )


# ─── CLI ─────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    """Construct the command-line argument parser.

    Returns:
        A configured :class:`argparse.ArgumentParser` with ``--file`` / ``--url``
        as a mutually exclusive required group, plus ``--target`` and ``--output``.
    """
    parser = argparse.ArgumentParser(
        description="subtitle-generator — bilingual SRT subtitles from any video/audio source",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python subtitle_generator.py --file lecture.mp4 --target es
  python subtitle_generator.py --url "https://youtube.com/watch?v=XXX" --target zh-Hant
  python subtitle_generator.py --file podcast.mp3 --target ja --output ./subs
  python subtitle_generator.py --file interview.wav --target fr --output ./subtitles
        """,
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--url",
        metavar="URL",
        help="Platform URL resolved via yt-dlp (YouTube, Bilibili, TikTok, Twitter/X, …)",
    )
    src.add_argument(
        "--file",
        metavar="PATH",
        help="Local audio or video file (any format supported by ffmpeg)",
    )
    parser.add_argument(
        "--target",
        default="en",
        metavar="LANG",
        help="BCP-47 target language code for the translation (default: en)",
    )
    parser.add_argument(
        "--output",
        default=".",
        metavar="DIR",
        help="Directory to write output .srt files (default: current directory)",
    )
    return parser


def main() -> None:
    """Parse CLI arguments, resolve the audio source, and run subtitle generation.

    Prints a Rich progress bar during processing and a summary table on
    completion.  Exits with code 1 on input errors.
    """
    args = build_parser().parse_args()
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    console.print(Panel.fit(
        "[bold cyan]Demo 05 — Subtitle Generator[/bold cyan]\n"
        "[dim]Gemini 3.5 Live Translate  →  bilingual .srt[/dim]",
        border_style="cyan",
        padding=(0, 2),
    ))
    console.print()

    # ── Resolve audio source ──────────────────────────────────────────────────
    if args.url:
        console.print("[bold]🔗 Resolving URL via yt-dlp…[/]")
        try:
            source = resolve_url(args.url)
        except RuntimeError as exc:
            console.print(f"[red]❌ yt-dlp error:[/] {exc}")
            sys.exit(1)
        stem = "video"
        console.print(
            f"   [dim]{source[:80]}{'…' if len(source) > 80 else ''}[/]"
        )
    else:
        file_path = Path(args.file)
        if not file_path.exists():
            console.print(f"[red]❌ File not found:[/] {args.file}")
            sys.exit(1)
        source = str(file_path.resolve())
        stem = file_path.stem

    console.print(f"[bold]🌐 Target language:[/]  [green]{args.target}[/]")
    console.print(f"[bold]📁 Output dir:[/]       [dim]{output_dir.resolve()}[/]")
    console.print()

    # ── Run subtitle generation with live progress bar ─────────────────────────
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>5.1f}%"),
        TextColumn("({task.completed}/{task.total} chunks)"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    ) as progress:
        task_id = progress.add_task(
            f"[cyan]Translating → {args.target}[/cyan]",
            total=None,
        )
        result = asyncio.run(
            generate_subtitles(source, args.target, output_dir, stem, progress, task_id)
        )
        progress.update(task_id, description=f"[green]✓ Done → {args.target}[/green]")

    # ── Summary ───────────────────────────────────────────────────────────────
    console.print()
    table = Table(
        title="Generated Subtitle Files",
        show_header=True,
        header_style="bold magenta",
        show_lines=True,
    )
    table.add_column("File", style="dim", no_wrap=True)
    table.add_column("Track", min_width=38)
    table.add_column("Entries", justify="right")

    table.add_row(
        result.bilingual_srt.name,
        "🌍  Bilingual  (original + translation per entry)",
        str(result.entry_count),
    )
    table.add_row(
        result.original_srt.name,
        "🗣   Original language captions",
        "—",
    )
    table.add_row(
        result.translated_srt.name,
        f"🔤  [green]{result.target_lang}[/green] translation subtitles",
        "—",
    )

    console.print(table)
    console.print(
        f"\n✅ [green]Done![/green]  "
        f"[bold]{result.entry_count}[/bold] subtitle entries  "
        f"·  [bold]{result.duration_s:.0f}s[/bold] audio  "
        f"·  output → [dim]{output_dir.resolve()}[/dim]\n"
    )


if __name__ == "__main__":
    main()
