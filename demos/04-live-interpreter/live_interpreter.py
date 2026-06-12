"""
Demo 04 — Live Interpreter
==========================
Captures microphone audio in real-time and streams it to the Gemini Live
Translate API for low-latency speech translation.

Architecture
------------
- A background ``AudioCapture`` thread reads 100 ms PCM-16 chunks from PyAudio
  and pushes them onto a ``queue.Queue``.
- The async ``TranslationSession`` coroutine reads from that queue, sends audio
  blobs to the Gemini ``live.connect`` session, and receives both input and
  output transcripts.
- A ``rich.live.Live`` panel is refreshed in place so the terminal remains clean.

Usage
-----
    python live_interpreter.py --target Spanish
    python live_interpreter.py --target Japanese --device 2
    python live_interpreter.py --list-devices

Dependencies
------------
    pip install google-genai pyaudio rich
"""

from __future__ import annotations

import argparse
import asyncio
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

try:
    import pyaudio
except ImportError:
    sys.exit("pyaudio is required.  Run: pip install pyaudio")

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    sys.exit("google-genai is required.  Run: pip install google-genai")

try:
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError:
    sys.exit("rich is required.  Run: pip install rich")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SAMPLE_RATE: int = 16_000          # 16 kHz — required by Gemini Live
CHANNELS: int = 1                   # mono
SAMPLE_WIDTH: int = 2               # 16-bit PCM → 2 bytes per sample
CHUNK_MS: int = 100                 # audio chunk duration in milliseconds
CHUNK_FRAMES: int = SAMPLE_RATE * CHUNK_MS // 1000  # 1 600 frames per chunk

GEMINI_MODEL: str = "gemini-2.0-flash-live-001"

console = Console()


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class TranscriptState:
    """Mutable state shared between the async session and the Rich display."""

    input_lines: list[str] = field(default_factory=list)
    output_lines: list[str] = field(default_factory=list)
    status: str = "Initialising…"
    elapsed_seconds: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update_status(self, status: str) -> None:
        """Thread-safe status update."""
        with self._lock:
            self.status = status

    def append_input(self, text: str) -> None:
        """Thread-safe append to the input transcript."""
        with self._lock:
            self.input_lines.append(text)

    def append_output(self, text: str) -> None:
        """Thread-safe append to the output transcript."""
        with self._lock:
            self.output_lines.append(text)

    def snapshot(self) -> "TranscriptState":
        """Return a shallow copy of the current state for rendering."""
        with self._lock:
            snap = TranscriptState(
                input_lines=list(self.input_lines),
                output_lines=list(self.output_lines),
                status=self.status,
                elapsed_seconds=self.elapsed_seconds,
            )
        return snap


# ---------------------------------------------------------------------------
# Audio capture
# ---------------------------------------------------------------------------

class AudioCapture:
    """
    Runs PyAudio in a background thread and pushes 100 ms PCM-16 chunks onto
    *audio_queue*.

    Parameters
    ----------
    device_index:
        PyAudio device index for the desired input.  ``None`` uses the system
        default.
    audio_queue:
        A ``queue.Queue`` that receives ``bytes`` objects (raw PCM-16LE).
    """

    def __init__(
        self,
        audio_queue: "queue.Queue[Optional[bytes]]",
        device_index: Optional[int] = None,
    ) -> None:
        """
        Initialise the capture helper.

        Parameters
        ----------
        audio_queue:
            Destination queue for captured PCM chunks.
        device_index:
            PyAudio device index, or ``None`` for the system default.
        """
        self._queue = audio_queue
        self._device_index = device_index
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._pa: Optional[pyaudio.PyAudio] = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Open the PyAudio stream and begin capturing in a daemon thread."""
        self._thread = threading.Thread(target=self._run, daemon=True, name="audio-capture")
        self._thread.start()

    def stop(self) -> None:
        """Signal the capture thread to stop and wait for it to exit."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        # Sentinel so the consumer knows the stream is finished.
        self._queue.put(None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Target for the capture thread — opens stream and reads chunks."""
        self._pa = pyaudio.PyAudio()
        try:
            stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                input_device_index=self._device_index,
                frames_per_buffer=CHUNK_FRAMES,
            )
        except OSError as exc:
            console.print(f"[red]Failed to open audio device: {exc}[/red]")
            self._queue.put(None)
            self._pa.terminate()
            return

        try:
            while not self._stop_event.is_set():
                try:
                    data = stream.read(CHUNK_FRAMES, exception_on_overflow=False)
                    self._queue.put(data)
                except OSError:
                    break
        finally:
            stream.stop_stream()
            stream.close()
            self._pa.terminate()


# ---------------------------------------------------------------------------
# Rich display helpers
# ---------------------------------------------------------------------------

def _build_panel(state: TranscriptState, target_language: str) -> Panel:
    """
    Compose the Rich renderable shown in the live display.

    Parameters
    ----------
    state:
        A snapshot of the current transcript state.
    target_language:
        Human-readable target language name, e.g. ``"Spanish"``.

    Returns
    -------
    Panel
        A Rich ``Panel`` ready to be rendered.
    """
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)

    # --- Input column ---
    input_text = Text()
    for line in state.input_lines[-20:]:          # keep last 20 lines visible
        input_text.append(line + "\n", style="white")

    # --- Output column ---
    output_text = Text()
    for line in state.output_lines[-20:]:
        output_text.append(line + "\n", style="bright_green")

    input_panel = Panel(
        input_text,
        title="[bold cyan]🎙  Input (original)[/bold cyan]",
        border_style="cyan",
    )
    output_panel = Panel(
        output_text,
        title=f"[bold green]🌐  Output ({target_language})[/bold green]",
        border_style="green",
    )

    grid.add_row(input_panel, output_panel)

    elapsed = f"{int(state.elapsed_seconds // 60):02d}:{int(state.elapsed_seconds % 60):02d}"
    footer = f"[dim]{state.status}   ·   elapsed {elapsed}   ·   Ctrl+C to stop[/dim]"

    return Panel(
        grid,
        title="[bold magenta]Gemini Live Interpreter[/bold magenta]",
        subtitle=footer,
        border_style="magenta",
    )


# ---------------------------------------------------------------------------
# Device listing
# ---------------------------------------------------------------------------

def list_audio_devices() -> None:
    """
    Print a table of available PyAudio input devices to the console and exit.
    """
    pa = pyaudio.PyAudio()
    device_count = pa.get_device_count()

    table = Table(title="Available Audio Input Devices", show_lines=True)
    table.add_column("Index", style="cyan", justify="right")
    table.add_column("Name", style="white")
    table.add_column("Max Input Channels", justify="right")
    table.add_column("Default Sample Rate", justify="right")

    for i in range(device_count):
        info = pa.get_device_info_by_index(i)
        if int(info["maxInputChannels"]) > 0:
            table.add_row(
                str(i),
                str(info["name"]),
                str(info["maxInputChannels"]),
                str(int(info["defaultSampleRate"])),
            )

    pa.terminate()
    console.print(table)


# ---------------------------------------------------------------------------
# Gemini translation session
# ---------------------------------------------------------------------------

async def run_translation_session(
    audio_queue: "queue.Queue[Optional[bytes]]",
    state: TranscriptState,
    target_language: str,
    api_key: str,
) -> None:
    """
    Connect to the Gemini Live Translate API, stream audio chunks, and collect
    transcripts.

    This coroutine runs until ``audio_queue`` produces a ``None`` sentinel
    (signalling end-of-stream) or a ``KeyboardInterrupt`` / ``asyncio.
    CancelledError`` is raised.

    Parameters
    ----------
    audio_queue:
        Queue populated by ``AudioCapture``.  ``None`` is the stop sentinel.
    state:
        Shared ``TranscriptState`` updated with received transcripts.
    target_language:
        The language to translate into, e.g. ``"French"``.
    api_key:
        Google AI API key.
    """
    client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})

    system_instruction = (
        f"You are a professional real-time interpreter. "
        f"Translate everything the user says into {target_language}. "
        f"Preserve meaning, tone, and register. "
        f"Do not add commentary or explanations."
    )

    config = genai_types.LiveConnectConfig(
        response_modalities=["TEXT"],
        system_instruction=system_instruction,
        input_audio_transcription=genai_types.AudioTranscriptionConfig(),
        output_audio_transcription=genai_types.AudioTranscriptionConfig(),
    )

    state.update_status("Connecting to Gemini…")

    async with client.aio.live.connect(model=GEMINI_MODEL, config=config) as session:
        state.update_status("Connected — speak now")

        async def _send_audio() -> None:
            """Read from the queue and forward PCM chunks to the session."""
            loop = asyncio.get_running_loop()
            while True:
                # Run the blocking queue.get in a thread-pool executor so we
                # don't stall the event loop.
                chunk: Optional[bytes] = await loop.run_in_executor(
                    None, audio_queue.get
                )
                if chunk is None:
                    return
                await session.send_realtime_input(
                    audio=genai_types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                )

        async def _receive_responses() -> None:
            """Collect server-sent events and update the transcript state."""
            async for response in session.receive():
                # Input transcription (what the user said)
                if (
                    response.server_content
                    and response.server_content.input_transcription
                    and response.server_content.input_transcription.text
                ):
                    text = response.server_content.input_transcription.text.strip()
                    if text:
                        state.append_input(text)

                # Output transcription (the translation)
                if (
                    response.server_content
                    and response.server_content.output_transcription
                    and response.server_content.output_transcription.text
                ):
                    text = response.server_content.output_transcription.text.strip()
                    if text:
                        state.append_output(text)

                # Model turn text parts (fallback for some response shapes)
                if response.server_content and response.server_content.model_turn:
                    for part in response.server_content.model_turn.parts or []:
                        if part.text and part.text.strip():
                            state.append_output(part.text.strip())

        # Run sender and receiver concurrently; cancel both when either finishes.
        sender = asyncio.create_task(_send_audio())
        receiver = asyncio.create_task(_receive_responses())

        try:
            done, pending = await asyncio.wait(
                {sender, receiver},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        except asyncio.CancelledError:
            sender.cancel()
            receiver.cancel()
            raise

    state.update_status("Session closed")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """
    Parse command-line arguments.

    Returns
    -------
    argparse.Namespace
        Parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description="Demo 04 — Live Interpreter using Gemini Live Translate API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--target",
        metavar="LANGUAGE",
        default="English",
        help="Target language for translation (default: English)",
    )
    parser.add_argument(
        "--device",
        metavar="INDEX",
        type=int,
        default=None,
        help="PyAudio device index to use as microphone input",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List available audio input devices and exit",
    )
    parser.add_argument(
        "--api-key",
        metavar="KEY",
        default=None,
        help="Google AI API key (overrides GEMINI_API_KEY env var)",
    )
    return parser.parse_args()


async def _async_main(
    args: argparse.Namespace,
    audio_queue: "queue.Queue[Optional[bytes]]",
    state: TranscriptState,
) -> None:
    """
    Async entry point: runs the translation session alongside a periodic
    display-refresh coroutine.

    Parameters
    ----------
    args:
        Parsed CLI arguments.
    audio_queue:
        Queue shared with ``AudioCapture``.
    state:
        Shared transcript state.
    """
    api_key: str = args.api_key or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        console.print(
            "[red]No API key found.  Set GEMINI_API_KEY or pass --api-key.[/red]"
        )
        raise SystemExit(1)

    start_time = time.monotonic()

    async def _refresh_display(live: Live) -> None:
        """Periodically refresh the Rich live display."""
        while True:
            state.elapsed_seconds = time.monotonic() - start_time
            snap = state.snapshot()
            live.update(_build_panel(snap, args.target))
            await asyncio.sleep(0.1)

    with Live(
        _build_panel(state.snapshot(), args.target),
        console=console,
        refresh_per_second=10,
        screen=False,
    ) as live:
        display_task = asyncio.create_task(_refresh_display(live))
        try:
            await run_translation_session(
                audio_queue=audio_queue,
                state=state,
                target_language=args.target,
                api_key=api_key,
            )
        finally:
            display_task.cancel()
            try:
                await display_task
            except asyncio.CancelledError:
                pass


def main() -> None:
    """
    Script entry point.

    Parses arguments, optionally lists audio devices, then starts the audio
    capture thread and Gemini translation session.  Handles ``KeyboardInterrupt``
    for graceful shutdown.
    """
    args = parse_args()

    if args.list_devices:
        list_audio_devices()
        return

    state = TranscriptState()
    audio_queue: "queue.Queue[Optional[bytes]]" = queue.Queue(maxsize=200)
    capture = AudioCapture(audio_queue=audio_queue, device_index=args.device)

    console.rule("[bold magenta]Gemini Live Interpreter — Demo 04[/bold magenta]")
    console.print(
        f"  Target language : [bold green]{args.target}[/bold green]\n"
        f"  Audio device    : [cyan]{'default' if args.device is None else args.device}[/cyan]\n"
        f"  Sample rate     : [cyan]{SAMPLE_RATE} Hz[/cyan]\n"
        f"  Chunk size      : [cyan]{CHUNK_MS} ms[/cyan]\n"
    )

    capture.start()

    try:
        asyncio.run(_async_main(args, audio_queue, state))
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted — shutting down…[/yellow]")
    except SystemExit:
        pass
    finally:
        capture.stop()
        # Drain the queue so the capture thread's sentinel is consumed.
        while not audio_queue.empty():
            try:
                audio_queue.get_nowait()
            except queue.Empty:
                break

    # Print final transcripts after the live display closes.
    snap = state.snapshot()
    if snap.input_lines or snap.output_lines:
        console.rule("[dim]Session transcript[/dim]")
        for orig, trans in zip(snap.input_lines, snap.output_lines):
            console.print(f"  [white]{orig}[/white]")
            console.print(f"  [bright_green]→ {trans}[/bright_green]")
            console.print()

    console.print("[dim]Done.[/dim]")


if __name__ == "__main__":
    main()
