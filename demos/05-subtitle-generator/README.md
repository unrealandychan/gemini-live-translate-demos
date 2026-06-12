# Demo 05 — Subtitle Generator 🎬

Generate **bilingual SRT subtitle files** from any video or audio source, powered by the **Gemini 3.5 Live Translate API**.

Each subtitle entry contains the original-language transcript on line 1 and the translation on line 2 — ready to drop into any video player or editor that supports `.srt`.

---

## Features

- ✅ **Three .srt outputs per run** — bilingual, original-only, and translation-only
- ✅ **Any input source** — local file or 1 000+ platforms via [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- ✅ **Any language pair** — source language is auto-detected; target is any BCP-47 code
- ✅ **Accurate timing** — subtitle timestamps are derived from the audio-chunk clock, not wall time
- ✅ **Rich progress bar** — live chunk count, percentage, elapsed & remaining time
- ✅ **Sentence-aware flushing** — entries split on `.!?` (and CJK equivalents) or every 4 s

---

## Architecture

```
URL  ──► yt-dlp ──► direct stream URL ──┐
                                         ├──► ffmpeg (PCM 16 kHz mono) ──► Gemini Live Translate
File ────────────────────────────────────┘
                                                   │
                             input_audio_transcription   output_audio_transcription
                                     (original)               (translation)
                                                   │
                                        BilingualSubtitleBuilder
                                       (shared audio-time clock)
                                                   │
                                  ┌────────────────┼─────────────────┐
                         bilingual.srt        original.srt       {lang}.srt
```

---

## Quick start

```bash
pip install google-genai yt-dlp rich
# ffmpeg must be on PATH

export GEMINI_API_KEY=your_key_here
```

### Translate a local video to Spanish

```bash
python subtitle_generator.py --file lecture.mp4 --target es
```

### Translate a YouTube video to Traditional Chinese

```bash
python subtitle_generator.py --url "https://youtube.com/watch?v=XXX" --target zh-Hant
```

### Translate a podcast MP3 to Japanese, save to ./subs

```bash
python subtitle_generator.py --file podcast.mp3 --target ja --output ./subs
```

### Translate a WAV interview to French

```bash
python subtitle_generator.py --file interview.wav --target fr --output ./subtitles
```

---

## Output

For an input file `lecture.mp4` with `--target es`:

```
./
├── lecture_bilingual.srt       ← original + Spanish on every entry
├── lecture_original.srt        ← source language captions only
└── lecture_es.srt              ← Spanish subtitles only
```

### Bilingual `.srt` format

```srt
1
00:00:00,000 --> 00:00:03,400
Welcome to today's lecture on machine learning.
Bienvenidos a la clase de hoy sobre aprendizaje automático.

2
00:00:03,400 --> 00:00:07,100
We will start with the basics of neural networks.
Comenzaremos con los fundamentos de las redes neuronales.
```

---

## Burn subtitles into the video (ffmpeg)

```bash
# Hardcode bilingual subtitles
ffmpeg -i lecture.mp4 -vf "subtitles=lecture_bilingual.srt" lecture_with_subs.mp4

# Mux as a soft subtitle track (selectable in media players)
ffmpeg -i lecture.mp4 -i lecture_es.srt -c copy -c:s mov_text -metadata:s:s:0 language=spa lecture_soft_subs.mp4
```

---

## CLI reference

```
usage: subtitle_generator.py [-h] (--url URL | --file PATH)
                              [--target LANG] [--output DIR]

subtitle-generator — bilingual SRT subtitles from any video/audio source

options:
  -h, --help      show this help message and exit
  --url URL       Platform URL resolved via yt-dlp (YouTube, Bilibili,
                  TikTok, Twitter/X, …)
  --file PATH     Local audio or video file (any format supported by ffmpeg)
  --target LANG   BCP-47 target language code for the translation (default: en)
  --output DIR    Directory to write output .srt files (default: current dir)
```

---

## Supported platforms (via yt-dlp)

YouTube · Bilibili · TikTok · Twitter/X · Instagram · Twitch · NicoNico ·  
Facebook · Dailymotion · Vimeo · SoundCloud · and [1 000+ more](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)

---

## Requirements

| Dependency | Purpose |
|---|---|
| `google-genai` | Gemini 3.5 Live Translate API client |
| `yt-dlp` | Resolve platform URLs to direct audio streams |
| `rich` | Progress bar and formatted console output |
| `ffmpeg` (system) | Decode any audio/video format to PCM 16 kHz mono |

---

## Notes

- The source language is **auto-detected** by the Gemini model; you do not need to specify it.
- For very short clips (< 5 s), all transcript text may arrive during the post-send drain window — this is expected behaviour.
- If `--url` resolves to a playlist, only the first item is downloaded (yt-dlp `--no-playlist`).
- Subtitle timing is an approximation based on the audio-chunk clock; it may drift slightly from the true start-of-speech position, especially for speech with long pauses.
