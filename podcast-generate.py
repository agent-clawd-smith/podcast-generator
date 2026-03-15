#!/usr/bin/env python3
"""Daily AI Podcast Generator — main pipeline orchestrator.

Stages:
  0. Pre-flight: load config, check budget tier, idempotency guard
  1. Gather sources: Firecrawl, Nitter, YouTube
  2. Filter & rank stories via Gemini Flash
  3. Generate script via Claude Opus
  4. Generate audio via Gemini TTS
  5. Deliver (archive + iMessage) & cleanup

Invoked by cron daily at 7:30 AM PT (15:30 UTC).
"""
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

# Add this directory to path for imports
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import firecrawl_source
import nitter_source
import youtube_source
import script_writer
import tts_generator
import podbean_publisher
import cleanup as cleanup_module

# Paths
SECRETS_PATH = os.path.expanduser("~/.openclaw/secrets.json")
SETTINGS_PATH = os.path.join(SCRIPT_DIR, "podcast-settings.json")
ARCHIVE_PATH = os.path.join(SCRIPT_DIR, "podcast-archive.json")
TIER_CONFIG_PATH = os.path.expanduser("~/repos/llm-observability/tier-config.json")
PODCASTS_DIR = os.path.join(SCRIPT_DIR, "episodes")
USAGE_LOG_PATH = os.path.expanduser("~/repos/llm-observability/usage-current-week.jsonl")

# OpenRouter for ranking
OPENROUTER_API = "https://openrouter.ai/api/v1/chat/completions"
RANKING_MODEL = "google/gemini-2.5-flash"

def _get_operator_phone():
    try:
        return load_secrets().get("operator_phone", "")
    except Exception:
        return ""


def load_secrets():
    with open(SECRETS_PATH) as f:
        return json.load(f)

def load_settings():
    with open(SETTINGS_PATH) as f:
        return json.load(f)

def load_archive():
    if not os.path.exists(ARCHIVE_PATH):
        return {"episodes": []}
    with open(ARCHIVE_PATH) as f:
        return json.load(f)

def save_archive(archive):
    with open(ARCHIVE_PATH, "w") as f:
        json.dump(archive, f, indent=2)


def check_tier():
    """Check budget tier — abort if Tier 2+."""
    if not os.path.exists(TIER_CONFIG_PATH):
        print("  [preflight] No tier config found, assuming OK")
        return True
    try:
        with open(TIER_CONFIG_PATH) as f:
            config = json.load(f)
        tier = config.get("current_tier", 0)
        spend = config.get("current_spend", 0)
        if tier >= 2:
            print(f"  [preflight] ABORT: Tier {tier} (${spend:.2f} spent) — budget too high for podcast")
            return False
        print(f"  [preflight] Tier {tier} (${spend:.2f} spent) — OK")
        return True
    except (json.JSONDecodeError, IOError):
        return True


def check_idempotency(archive):
    """Skip if today's episode already exists."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for ep in archive.get("episodes", []):
        if ep.get("date") == today:
            print(f"  [preflight] Episode for {today} already exists — skipping")
            return False
    return True


def get_recent_coverage(archive, days=7):
    """Get URLs and summaries from recent episodes for dedup."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    recent_urls = set()
    recent_summaries = []
    for ep in archive.get("episodes", []):
        if ep.get("date", "") >= cutoff:
            for src in ep.get("sources", []):
                if src.get("url"):
                    recent_urls.add(src["url"])
            recent_summaries.append(f"- {ep['date']}: {ep.get('summary', '')[:150]}")
    return recent_urls, recent_summaries


def log_ranking_cost(input_tokens, output_tokens, cost_usd):
    """Log the ranking LLM call cost."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": RANKING_MODEL,
        "provider": "openrouter",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read": 0,
        "cache_write": 0,
        "total_tokens": input_tokens + output_tokens,
        "cost_usd": cost_usd,
        "cost_input": 0,
        "cost_output": 0,
        "cost_cache_read": 0,
        "cost_cache_write": 0,
        "session_file": "daily-podcast",
        "context": "podcast: story ranking/filtering",
    }
    try:
        os.makedirs(os.path.dirname(USAGE_LOG_PATH), exist_ok=True)
        with open(USAGE_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except (IOError, OSError):
        pass


def rank_stories(stories, topics, api_key, recent_summaries=None):
    """Use Gemini Flash to rank and select top stories."""
    if not stories:
        return []

    # Build story list for the prompt
    story_list = ""
    for i, s in enumerate(stories):
        story_list += f"\n{i+1}. [{s['source']}] {s['title']}\n   {s['content'][:200]}\n"

    # Build recent coverage context
    recent_block = ""
    if recent_summaries:
        recent_block = f"""

IMPORTANT — These topics were already covered in recent episodes. AVOID selecting stories that repeat the same ground:
{chr(10).join(recent_summaries)}
"""

    prompt = f"""You are selecting stories for a daily tech podcast covering: {', '.join(topics)}.

Here are the candidate stories:
{story_list}
{recent_block}
Select the 8-12 most important, diverse, and interesting stories. Return ONLY a JSON array of story indices (1-based), ordered by importance. Example: [3, 1, 7, 5, 2, 9, 4, 8]

Prioritize:
- Breaking news and significant developments
- Stories with broad industry impact
- Diversity of topics (don't cluster on one topic)
- Unique insights over rehashed press releases
- Fresh stories NOT covered in recent episodes listed above"""

    body = json.dumps({
        "model": RANKING_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 200,
        "temperature": 0.3,
    }).encode()

    req = urllib.request.Request(
        OPENROUTER_API,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            response = json.loads(r.read())
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        print(f"  [rank] API error: {e} — using first 10 stories")
        return stories[:10]

    content = response["choices"][0]["message"]["content"]
    usage = response.get("usage", {})
    input_tokens = usage.get("prompt_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0)
    cost_usd = (input_tokens / 1_000_000 * 0.15) + (output_tokens / 1_000_000 * 0.60)
    log_ranking_cost(input_tokens, output_tokens, round(cost_usd, 6))

    # Parse indices from response
    try:
        # Extract JSON array from response (may have surrounding text)
        import re
        match = re.search(r'\[[\d,\s]+\]', content)
        if match:
            indices = json.loads(match.group())
            ranked = []
            for idx in indices:
                if 1 <= idx <= len(stories):
                    ranked.append(stories[idx - 1])
            return ranked[:12]
    except (json.JSONDecodeError, ValueError):
        pass

    print("  [rank] Failed to parse ranking — using first 10")
    return stories[:10]


def send_imessage(text, file_path=None):
    """Send iMessage notification to Adam."""
    cmd = ["/opt/homebrew/bin/imsg", "send", "--to", _get_operator_phone(), "--text", text]
    if file_path and os.path.exists(file_path):
        cmd.extend(["--file", file_path])
    try:
        subprocess.run(cmd, timeout=30, capture_output=True)
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"  [imessage] Failed to send: {e}")


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"=== Daily Podcast Generator — {today} ===\n")

    # ── Stage 0: Pre-flight ──
    print("[Stage 0] Pre-flight checks")
    secrets = load_secrets()
    settings = load_settings()
    archive = load_archive()

    if not settings.get("enabled", True):
        print("  Podcast generation is disabled in settings")
        return

    if not check_tier():
        return

    if not check_idempotency(archive):
        return

    firecrawl_key = secrets.get("firecrawl", {}).get("apiKey", "")
    openrouter_key = secrets.get("openrouter", {}).get("apiKey", "")
    google_key = secrets.get("google", {}).get("apiKey", "")

    if not openrouter_key:
        print("  [preflight] ERROR: No OpenRouter API key in secrets.json")
        return

    topics = settings.get("topics", [])
    speakers = settings.get("speakers", {})
    speaker_profiles = speakers.get("profiles", [
        {"name": "Alex", "style": "Enthusiastic tech journalist"},
        {"name": "Sam", "style": "Pragmatic network engineer"},
    ])
    max_duration = settings.get("maxDurationMinutes", 18)

    print(f"  Topics: {len(topics)}, Speakers: {len(speaker_profiles)}, Max duration: {max_duration}min")
    print()

    # ── Stage 1: Gather Sources ──
    print("[Stage 1] Gathering sources")
    all_sources = []

    # Firecrawl
    if firecrawl_key:
        fc_results = firecrawl_source.gather(topics, firecrawl_key)
        all_sources.extend(fc_results)
        print(f"  Firecrawl: {len(fc_results)} articles")
    else:
        print("  Firecrawl: skipped (no API key)")

    # Twitter/X via Nitter (topic search + optional accounts)
    nitter_accounts = settings.get("nitterAccounts", [])
    twitter_results = nitter_source.gather(topics, accounts=nitter_accounts if nitter_accounts else None)
    all_sources.extend(twitter_results)
    print(f"  Twitter: {len(twitter_results)} tweets")

    # YouTube (topic search + optional channels)
    yt_results = youtube_source.gather(topics)
    all_sources.extend(yt_results)
    print(f"  YouTube: {len(yt_results)} transcripts")

    if not all_sources:
        print("  ERROR: No sources gathered — aborting")
        send_imessage("Podcast generation failed: no sources could be gathered today.")
        return

    print(f"  Total sources: {len(all_sources)}")

    # Dedup against recent episodes
    recent_urls, recent_summaries = get_recent_coverage(archive)
    if recent_urls:
        before = len(all_sources)
        all_sources = [s for s in all_sources if s.get("url", "") not in recent_urls]
        deduped = before - len(all_sources)
        if deduped:
            print(f"  Removed {deduped} stories already covered in recent episodes")
    print(f"  Sources after dedup: {len(all_sources)}")
    print()

    # ── Stage 2: Filter & Rank ──
    print("[Stage 2] Ranking stories")
    ranked_stories = rank_stories(all_sources, topics, openrouter_key, recent_summaries)
    print(f"  Selected {len(ranked_stories)} stories")
    for i, s in enumerate(ranked_stories, 1):
        print(f"    {i}. [{s['source']}] {s['title'][:60]}")
    print()

    # ── Stage 3: Script Writing ──
    print("[Stage 3] Generating script")
    script_result = script_writer.generate_script(
        ranked_stories, speaker_profiles, max_duration, openrouter_key
    )

    if not script_result:
        print("  ERROR: Script generation failed — aborting")
        send_imessage("Podcast generation failed: script writing error.")
        return

    script = script_result["script"]
    word_count = len(script.split())
    print(f"  Script: {word_count} words, cost: ${script_result['cost_usd']}")

    # Save script
    os.makedirs(PODCASTS_DIR, exist_ok=True)
    script_path = os.path.join(PODCASTS_DIR, f"{today}.txt")
    with open(script_path, "w") as f:
        f.write(script)
    print(f"  Script saved: {script_path}")
    print()

    # ── Stage 4: TTS Audio ──
    print("[Stage 4] Generating audio")
    audio_path = os.path.join(PODCASTS_DIR, f"{today}.mp3")

    tts_result = None
    if google_key:
        tts_result = tts_generator.generate_audio(
            script, 2, google_key, audio_path,
            speaker_profiles=speaker_profiles[:2]
        )
    else:
        print("  WARNING: No Google API key — skipping TTS (text-only episode)")

    if tts_result and tts_result.get("success"):
        duration_seconds = tts_result["duration_seconds"]
        tts_cost = tts_result["cost_usd"]
        has_audio = True
        print(f"  Audio: {duration_seconds}s, cost: ${tts_cost}")
    else:
        if google_key:
            print("  WARNING: TTS failed — text-only episode")
        duration_seconds = 0
        tts_cost = 0
        has_audio = False
    print()

    # ── Stage 5: Deliver & Archive ──
    print("[Stage 5] Delivering & archiving")

    # Count firecrawl credits used
    fc_credits = sum(2 for s in ranked_stories if s.get("source") == "firecrawl")

    episode = {
        "date": today,
        "title": script_result["title"],
        "summary": script_result["summary"],
        "sources": [{"title": s["title"], "url": s.get("url", ""), "source": s["source"]} for s in ranked_stories],
        "audioFile": f"episodes/{today}.mp3" if has_audio else "",
        "scriptFile": f"episodes/{today}.txt",
        "durationSeconds": duration_seconds,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "cost": {
            "firecrawl_credits": fc_credits,
            "llm_usd": round(script_result["cost_usd"], 4),
            "tts_usd": round(tts_cost, 4),
            "total_usd": round(script_result["cost_usd"] + tts_cost, 4),
        },
    }

    archive["episodes"].append(episode)
    save_archive(archive)
    print(f"  Archive updated ({len(archive['episodes'])} episodes)")

    # Publish to Podbean
    podbean_url = ""
    if has_audio:
        # Build rich description with sources
        podbean_description = script_result["summary"]
        if script_result.get("sources_text"):
            podbean_description += "\n\n📚 Sources Referenced:\n" + script_result["sources_text"]
        
        pb_result = podbean_publisher.publish(audio_path, script_result["title"], podbean_description)
        if pb_result["success"]:
            podbean_url = pb_result.get("episode_url", "")
            episode["podbeanUrl"] = podbean_url
            save_archive(archive)
            print(f"  Published to Podbean: {podbean_url}")
        else:
            print(f"  WARNING: Podbean publish failed: {pb_result['error']}")

    # Send iMessage notification
    msg = f"Your daily podcast is ready! {script_result['title']}\n\n{script_result['summary']}"
    if podbean_url:
        msg += f"\n\n{podbean_url}"
    elif not has_audio:
        msg += "\n\n(Audio unavailable — text script saved)"
    send_imessage(msg)
    print("  iMessage sent")

    # Cleanup old episodes
    removed = cleanup_module.cleanup()
    if removed:
        print(f"  Cleaned up {removed} old episodes")

    total_cost = script_result["cost_usd"] + tts_cost
    print(f"\n=== Complete! Total cost: ${total_cost:.4f} ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        # Try to notify Adam
        try:
            send_imessage(f"Podcast generation failed with error: {str(e)[:200]}")
        except Exception:
            pass
        sys.exit(1)
