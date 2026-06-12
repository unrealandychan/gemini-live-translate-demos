# Demo 01 — Hello Translate

The simplest possible Gemini 3.5 Live Translate example. Feed it any audio file and watch the real-time transcripts roll in.

## What it does
- Decodes any ffmpeg-supported file to PCM 16 kHz
- Streams 100 ms chunks to the Live Translate API
- Prints **input** (original) and **output** (translated) transcripts in real time

## Quick start
```bash
pip install google-genai
export GEMINI_API_KEY=your_key_here

python hello_translate.py --file sample.mp3 --target zh-Hant
python hello_translate.py --file speech.wav --target en --echo
```

## Supported target languages
Any BCP-47 code from the [supported languages list](https://ai.google.dev/gemini-api/docs/live-api/live-translate#supported-languages) — e.g. `en`, `zh-Hant`, `zh-Hans`, `ja`, `ko`, `fr`, `es`.
