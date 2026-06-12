# Demo 02 — polydub 🎙️

Universal video/audio dubber — feed it **any URL or local file**, get back translated audio + subtitles.

## Features
- ✅ **400+ platforms** via [yt-dlp](https://github.com/yt-dlp/yt-dlp) (YouTube, Bilibili, TikTok, Twitter/X, …)
- ✅ Local files: `.mp3`, `.mp4`, `.wav`, `.m4a`, anything ffmpeg handles
- ✅ Outputs `dubbed_<lang>.wav` + `subtitles_<lang>.srt`
- ✅ Real-time progress display

## Architecture

```
URL  ──► yt-dlp ──► direct stream URL ──┐
                                         ├──► ffmpeg (PCM 16kHz) ──► Gemini Live Translate ──► WAV + SRT
File ────────────────────────────────────┘
```

## Quick start

```bash
pip install google-genai yt-dlp
# ffmpeg must be on PATH

export GEMINI_API_KEY=your_key_here

# Translate a YouTube video to English
python polydub.py --url "https://youtube.com/watch?v=XXX" --target en

# Translate a local podcast to Traditional Chinese
python polydub.py --file episode.mp3 --target zh-Hant

# Translate a lecture video to Japanese, save to ./output
python polydub.py --file lecture.mp4 --target ja --output ./output
```

## Output

```
output/
├── video_en.wav        ← dubbed audio
└── video_en.srt        ← subtitle track
```

To hardcode subtitles into the video with ffmpeg:
```bash
ffmpeg -i original.mp4 -i video_en.wav -vf subtitles=video_en.srt output_dubbed.mp4
```

## Supported platforms (via yt-dlp)
YouTube · Bilibili · TikTok · Twitter/X · Instagram · Twitch · NicoNico · and [1000+ more](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)
