# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A daily AI podcast generator pipeline that gathers news from multiple sources, ranks stories, writes a conversational script, and produces multi-speaker audio. Runs as a cron job at 7:30 AM PT via the OpenClaw agent framework.

## Pipeline Architecture

`podcast-generate.py` is the main orchestrator. The pipeline runs five sequential stages:

1. **Gather** — Three source modules (`firecrawl_source.py`, `nitter_source.py`, `youtube_source.py`) each implement a `gather(topics, ...)` function returning `[{"source", "title", "url", "content"}]` dicts. Sources are searched by topic keywords from settings.
2. **Rank** — Gemini Flash via OpenRouter selects 8-12 top stories from all gathered sources.
3. **Script** — `script_writer.py` sends stories to Claude Opus via OpenRouter, producing a two-speaker conversational script with `[SPEAKER_1]:`/`[SPEAKER_2]:` tags.
4. **TTS** — `tts_generator.py` uses Gemini 2.5 Flash TTS (Google direct API) for multi-speaker audio. Scripts are chunked (~4000 chars), each chunk sent to Gemini, raw PCM responses converted to WAV via ffmpeg, then concatenated and encoded to MP3.
5. **Deliver** — Episode archived to `podcast-archive.json`, audio sent to Adam via iMessage, old episodes cleaned up (30-day retention by `cleanup.py`).

## Key External Dependencies

- **Config/secrets**:
  - `~/.openclaw/secrets.json` — API keys (`firecrawl.apiKey`, `openrouter.apiKey`, `google.apiKey`)
  - `podcast-settings.json` — topics, speakers, max duration, enabled flag (in this repo)
  - `podcast-archive.json` — episode history (in this repo)
  - `~/repos/llm-observability/tier-config.json` — budget tier (pipeline aborts at tier 2+)
- **ffmpeg** — required for PCM→WAV→MP3 audio conversion
- **youtube-transcript-api** — Python package for YouTube transcript extraction
- **imsg** CLI at `/opt/homebrew/bin/imsg` — iMessage delivery

## Running

```bash
# Full pipeline
python3 podcast-generate.py

# Individual source modules have __main__ test modes
python3 firecrawl_source.py   # requires secrets.json
python3 nitter_source.py      # no auth needed
python3 youtube_source.py     # no auth needed
python3 cleanup.py            # standalone cleanup
```

## LLM Models Used

| Stage | Model | API | Notes |
|-------|-------|-----|-------|
| Ranking | `google/gemini-2.5-flash` | OpenRouter | Low cost, 200 max tokens |
| Script | `anthropic/claude-opus-4` | OpenRouter | High quality, 8000 max tokens, temp 0.8 |
| TTS | `gemini-2.5-flash-preview-tts` | Google direct | Multi-speaker, voices: Aoede + Charon |

All LLM costs are logged to `~/repos/llm-observability/usage-current-week.jsonl`.

## Design Constraints

- **Two speakers only** — Gemini TTS supports exactly 2 voices, so `script_writer.py` caps speaker profiles at 2 and `tts_generator.py` remaps any extras.
- **Idempotent** — Won't regenerate if today's episode already exists in the archive.
- **Budget-aware** — Checks tier config before running; aborts at tier 2+.
- **No third-party HTTP libraries** — All API calls use `urllib.request` directly (no `requests` dependency).
- **Source modules are stateless** — Each returns a flat list of story dicts; the orchestrator handles dedup and ranking.
