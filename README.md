# gemini-live-translate-demos 🌐🎙️

A collection of practical demo applications for the **Gemini 3.5 Live Translate API** — Google's speech-to-speech translation model supporting 70+ languages with near real-time latency.

> **Model:** `gemini-3.5-live-translate-preview` · **API Docs:** [ai.google.dev/gemini-api/docs/live-api/live-translate](https://ai.google.dev/gemini-api/docs/live-api/live-translate)

---

## Demos

| # | Demo | What it does | Best for |
|---|------|-------------|---------|
| [01](demos/01-hello-translate/) | **hello-translate** | Simplest possible example — feed any audio file, print transcripts | First steps / learning the API |
| [02](demos/02-polydub/) | **polydub** | URL or local file → dubbed WAV + SRT subtitles | One-off video translation |
| [03](demos/03-podcast-dubber/) | **podcast-dubber** | Batch a whole episode folder → multiple languages | Podcast creators |
| [04](demos/04-live-interpreter/) | **live-interpreter** | Microphone → real-time translated speech in terminal | Live meetings / events |
| [05](demos/05-subtitle-generator/) | **subtitle-generator** | Video/URL → bilingual SRT (original + translation on every line) | Accessibility / learning |

---

## Architecture overview

All demos share the same core pipeline:

```
Audio Source
────────────────────────────────────────────────────────────
  Microphone         ──┐
  Local file         ──┤  ffmpeg / PyAudio
  YouTube URL        ──┤  ────────────────► PCM 16kHz mono
  Twitch / Bilibili  ──┤   (via yt-dlp)     100ms chunks
  TikTok / Twitter   ──┘
                              │
                              ▼
              ╔══════════════════════════════════╗
              ║  Gemini 3.5 Live Translate API   ║
              ║  model: gemini-3.5-live-translate-preview  ║
              ║  WebSocket streaming (Live API)  ║
              ╚══════════════════════════════════╝
                              │
               ┌──────────────┼──────────────┐
               ▼              ▼              ▼
         Dubbed WAV    SRT Subtitles   Transcripts
         (24kHz PCM)  (timestamped)   (input + output)
```

---

## Getting started

### Prerequisites

```bash
# Python 3.11+
pip install google-genai yt-dlp rich pyaudio

# ffmpeg (required for all demos)
# macOS:
brew install ffmpeg
# Ubuntu/Debian:
sudo apt install ffmpeg
# Windows:
winget install ffmpeg
```

### API Key

```bash
export GEMINI_API_KEY=your_key_here
# Get yours free at: https://aistudio.google.com/apikey
```

---

## Quick demo tour

```bash
# 01 — Hello: translate a local file, print transcripts
python demos/01-hello-translate/hello_translate.py --file my_audio.mp3 --target en

# 02 — polydub: translate any YouTube video
python demos/02-polydub/polydub.py --url "https://youtube.com/watch?v=XXX" --target zh-Hant

# 03 — Podcast Dubber: batch all episodes in a folder
python demos/03-podcast-dubber/podcast_dubber.py --input ./episodes --targets en,ja

# 04 — Live: real-time microphone translation
python demos/04-live-interpreter/live_interpreter.py --target en

# 05 — Subtitles: bilingual SRT (original + translation)
python demos/05-subtitle-generator/subtitle_generator.py --file talk.mp4 --target en
```

---

## Supported languages (70+)

| Language | Code | Language | Code |
|----------|------|----------|------|
| English | `en` | Japanese | `ja` |
| Chinese (Traditional) | `zh-Hant` | Korean | `ko` |
| Chinese (Simplified) | `zh-Hans` | French | `fr` |
| Cantonese | `yue` | Spanish | `es` |
| German | `de` | Arabic | `ar` |
| Portuguese (Brazil) | `pt-BR` | Hindi | `hi` |

[Full list →](https://ai.google.dev/gemini-api/docs/live-api/live-translate#supported-languages)

---

## API notes

| Parameter | Value |
|-----------|-------|
| Model | `gemini-3.5-live-translate-preview` |
| Input audio | Raw PCM s16le, **16 kHz**, mono |
| Output audio | Raw PCM s16le, **24 kHz**, mono |
| Chunk size | **100 ms** recommended |
| Input modalities | **Audio only** (no text input) |
| Transport | WebSocket (Live API) |
| Pricing | $3.50 input / $21.00 output per 1M tokens |

### Key limitation
Translation is **real-time only** — a 30-minute file takes ~30 minutes to process. Plan accordingly for long content.

---

## Platform support (Demo 02 & 05 via yt-dlp)

YouTube · Bilibili · TikTok · Twitter/X · Instagram · Twitch · NicoNico · Facebook · SoundCloud · Vimeo · [1000+ more →](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)

---

## Contributing

PRs welcome! Ideas for new demos:
- [ ] Live stream → WebRTC broadcast with translated audio track
- [ ] Multi-speaker diarization before translation
- [ ] Video player browser extension with live translation overlay

---

## License

MIT — free to use and modify.
