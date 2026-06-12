# Demo 04 — Live Interpreter

Real-time speech translation in your terminal, powered by the **Gemini Live Translate API**.  
Speak into your microphone; see the original transcript and its translation side-by-side, updating live.

---

## How it works

```
Microphone
   │  PyAudio (16 kHz, 16-bit PCM, mono)
   │  100 ms chunks
   ▼
audio queue (thread-safe)
   │
   │  asyncio sender coroutine
   ▼
Gemini Live Translate WebSocket session
   │  (gemini-2.0-flash-live-001)
   │
   │  asyncio receiver coroutine
   ▼
TranscriptState  ──►  Rich Live display (side-by-side panels)
```

The script runs two concurrent async tasks inside a single `asyncio` event loop:

| Task | Responsibility |
|------|---------------|
| `_send_audio` | Drains the PCM queue and streams blobs via `session.send_realtime_input` |
| `_receive_responses` | Iterates `session.receive()`, extracts `input_transcription` and `output_transcription` |

A third task refreshes the Rich `Live` panel every 100 ms.

---

## Prerequisites

### Python packages

```bash
pip install google-genai pyaudio rich
```

> **macOS / Linux**: PyAudio requires PortAudio.
> ```bash
> # macOS
> brew install portaudio
>
> # Ubuntu / Debian
> sudo apt-get install portaudio19-dev
> ```

### API key

Export your Google AI / Gemini API key:

```bash
export GEMINI_API_KEY="your-key-here"
```

Or pass it at runtime with `--api-key YOUR_KEY`.

---

## Usage

### Basic — translate to Spanish

```bash
python live_interpreter.py --target Spanish
```

### List available microphone devices

```bash
python live_interpreter.py --list-devices
```

Sample output:

```
 Available Audio Input Devices
┌───────┬──────────────────────────────┬────────────────────┬─────────────────────┐
│ Index │ Name                         │ Max Input Channels │ Default Sample Rate │
├───────┼──────────────────────────────┼────────────────────┼─────────────────────┤
│     0 │ Built-in Microphone          │                  2 │               44100 │
│     2 │ USB Audio Device             │                  1 │               16000 │
└───────┴──────────────────────────────┴────────────────────┴─────────────────────┘
```

### Pick a specific device

```bash
python live_interpreter.py --target French --device 2
```

### Full options

```
usage: live_interpreter.py [-h] [--target LANGUAGE] [--device INDEX]
                           [--list-devices] [--api-key KEY]

Demo 04 — Live Interpreter using Gemini Live Translate API

options:
  -h, --help          show this help message and exit
  --target LANGUAGE   Target language for translation (default: English)
  --device INDEX      PyAudio device index to use as microphone input
  --list-devices      List available audio input devices and exit
  --api-key KEY       Google AI API key (overrides GEMINI_API_KEY env var)
```

---

## Terminal display

```
╭──────────────────── Gemini Live Interpreter ───────────────────╮
│ ╭─── 🎙  Input (original) ───╮  ╭─── 🌐  Output (Spanish) ───╮ │
│ │ Hello, how are you today?  │  │ Hola, ¿cómo estás hoy?     │ │
│ │ The weather is very nice.  │  │ El tiempo está muy bonito. │ │
│ ╰────────────────────────────╯  ╰────────────────────────────╯ │
╰── Connected — speak now · elapsed 00:23 · Ctrl+C to stop ──────╯
```

- **Left panel** — what Gemini heard you say (input transcription)
- **Right panel** — the translation in the target language
- **Footer** — connection status and elapsed time

---

## Supported languages

Any language supported by Gemini.  Pass the full English name:

```
Spanish · French · German · Japanese · Korean · Chinese · Arabic ·
Portuguese · Italian · Russian · Hindi · Dutch · Polish · Turkish …
```

---

## Audio settings

| Parameter | Value | Notes |
|-----------|-------|-------|
| Sample rate | 16 000 Hz | Required by Gemini Live |
| Channels | 1 (mono) | |
| Bit depth | 16-bit PCM | |
| Chunk size | 100 ms | 1 600 frames per send |

---

## Graceful shutdown

Press **Ctrl+C** at any time.  The script will:

1. Signal the audio capture thread to stop.
2. Cancel the async send / receive tasks.
3. Close the Gemini WebSocket session.
4. Print the full session transcript to the terminal.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `No API key found` | Export `GEMINI_API_KEY` or pass `--api-key` |
| `Failed to open audio device` | Run `--list-devices` and pass a valid `--device INDEX` |
| No translation appearing | Check microphone permissions; ensure `--target` language name is correct |
| PyAudio install fails | Install `portaudio19-dev` (Linux) or `portaudio` via Homebrew (macOS) |

---

## File structure

```
04-live-interpreter/
├── live_interpreter.py   # Main script
└── README.md             # This file
```
