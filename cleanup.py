"""Podcast episode cleanup — 30-day local retention.

Note: Podbean free tier API credentials lack episode_delete scope,
so Podbean cleanup must be done manually via the dashboard.
Audio is encoded at 64kbps mono to maximize free tier storage.
"""
import json
import os
from datetime import datetime, timezone, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARCHIVE_PATH = os.path.join(SCRIPT_DIR, "podcast-archive.json")
PODCASTS_DIR = os.path.join(SCRIPT_DIR, "episodes")
RETENTION_DAYS = 30


def cleanup():
    """Remove episodes and audio files older than RETENTION_DAYS."""
    if not os.path.exists(ARCHIVE_PATH):
        return 0

    with open(ARCHIVE_PATH) as f:
        archive = json.load(f)

    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%d")
    episodes = archive.get("episodes", [])
    kept = []
    removed = 0

    for ep in episodes:
        if ep.get("date", "9999") < cutoff:
            # Delete local audio file
            audio_file = ep.get("audioFile", "")
            if audio_file:
                full_path = os.path.join(os.path.dirname(ARCHIVE_PATH), audio_file)
                if os.path.exists(full_path):
                    os.remove(full_path)
                    print(f"  [cleanup] Deleted {audio_file}")
            # Delete local script file
            script_file = ep.get("scriptFile", "")
            if script_file:
                full_path = os.path.join(os.path.dirname(ARCHIVE_PATH), script_file)
                if os.path.exists(full_path):
                    os.remove(full_path)
            removed += 1
        else:
            kept.append(ep)

    if removed > 0:
        archive["episodes"] = kept
        with open(ARCHIVE_PATH, "w") as f:
            json.dump(archive, f, indent=2)
        print(f"  [cleanup] Removed {removed} local episodes older than {RETENTION_DAYS} days")

    return removed


if __name__ == "__main__":
    removed = cleanup()
    print(f"Cleaned up {removed} old episodes")
