# Demo 03 — Podcast Dubber 🎧

Batch-process an entire folder of podcast episodes into multiple languages simultaneously.

## Features
- ✅ Process whole episode library in one command
- ✅ Multiple target languages in parallel
- ✅ Rich progress display per episode
- ✅ `manifest.json` summary of all jobs
- ✅ Configurable concurrency (API rate limit friendly)

## Quick start

```bash
pip install google-genai rich
# ffmpeg must be on PATH

export GEMINI_API_KEY=your_k...n

# Dub all episodes in ./episodes to English + Traditional Chinese + Japanese
python podcast_dubber.py --input ./episodes --targets en,zh-Hant,ja

# With custom output dir and 2 concurrent sessions
python podcast_dubber.py --input ./episodes --targets en --output ./dubbed --workers 2
```

## Input / Output structure

```
episodes/                       ← --input
├── ep001_cantonese.mp3
├── ep002_cantonese.mp3
└── ep003_cantonese.mp3

dubbed/                         ← --output
├── ep001_cantonese_en.wav
├── ep001_cantonese_en.srt
├── ep001_cantonese_zh-Hant.wav
├── ep001_cantonese_zh-Hant.srt
├── ...
└── manifest.json               ← full summary
```

## Supported audio formats
`.mp3` · `.wav` · `.m4a` · `.aac` · `.ogg` · `.flac` · `.mp4` · `.webm`

## Cost estimation
At $3.50 / million input tokens, a 30-minute episode ≈ $0.10–$0.20 per language.
