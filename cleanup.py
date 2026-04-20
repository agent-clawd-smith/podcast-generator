"""Podcast episode cleanup — 30-day retention (local + Podbean)."""
import json
import os
import urllib.request
import urllib.error
import urllib.parse
import base64
from datetime import datetime, timezone, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARCHIVE_PATH = os.path.join(SCRIPT_DIR, "podcast-archive.json")
PODCASTS_DIR = os.path.join(SCRIPT_DIR, "episodes")
SECRETS_PATH = os.path.expanduser("~/.openclaw/secrets.json")
PODBEAN_API = "https://api.podbean.com/v1"
RETENTION_DAYS = 30


def _podbean_token():
    """Get Podbean OAuth2 access token."""
    with open(SECRETS_PATH) as f:
        secrets = json.load(f)
    podbean = secrets.get("podbean", {})
    client_id = podbean.get("clientId", "")
    client_secret = podbean.get("clientSecret", "")
    if not client_id or not client_secret:
        return None
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    req = urllib.request.Request(
        f"{PODBEAN_API}/oauth/token",
        data=body,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["access_token"]


def _podbean_list_episodes(token):
    """List all episodes from Podbean."""
    episodes = []
    offset = 0
    while True:
        params = urllib.parse.urlencode({
            "access_token": token,
            "offset": offset,
            "limit": 50,
        })
        req = urllib.request.Request(f"{PODBEAN_API}/episodes?{params}", method="GET")
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        batch = data.get("episodes", [])
        if not batch:
            break
        episodes.extend(batch)
        offset += len(batch)
        if len(batch) < 50:
            break
    return episodes


def _podbean_delete_episode(token, episode_id):
    """Delete an episode from Podbean."""
    params = urllib.parse.urlencode({
        "access_token": token,
        "id": episode_id,
    })
    req = urllib.request.Request(
        f"{PODBEAN_API}/episodes?{params}",
        method="DELETE",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def cleanup_podbean():
    """Delete Podbean episodes older than RETENTION_DAYS."""
    try:
        token = _podbean_token()
        if not token:
            return 0
    except Exception as e:
        print(f"  [cleanup] Podbean auth failed: {e}")
        return 0

    try:
        pb_episodes = _podbean_list_episodes(token)
    except Exception as e:
        print(f"  [cleanup] Podbean list failed: {e}")
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    removed = 0

    for ep in pb_episodes:
        # Podbean publish_time is a Unix timestamp
        publish_time = ep.get("publish_time")
        if not publish_time:
            continue
        ep_date = datetime.fromtimestamp(int(publish_time), tz=timezone.utc)
        if ep_date < cutoff:
            try:
                _podbean_delete_episode(token, ep["id"])
                print(f"  [cleanup] Deleted from Podbean: {ep.get('title', ep['id'])}")
                removed += 1
            except Exception as e:
                print(f"  [cleanup] Failed to delete {ep.get('title', '')}: {e}")

    return removed


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

    # Also clean up Podbean
    pb_removed = cleanup_podbean()
    if pb_removed:
        print(f"  [cleanup] Removed {pb_removed} Podbean episodes older than {RETENTION_DAYS} days")

    return removed + pb_removed


if __name__ == "__main__":
    removed = cleanup()
    print(f"Cleaned up {removed} old episodes")
