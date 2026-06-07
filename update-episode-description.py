#!/usr/bin/env python3
"""Update an existing Podbean episode description with HTML source links."""
import json
import os
import sys
import urllib.request
import urllib.error
import urllib.parse
import base64

PODBEAN_API = "https://api.podbean.com/v1"
SECRETS_PATH = os.path.expanduser("~/.openclaw/secrets.json")
ARCHIVE_PATH = os.path.expanduser("~/repos/llm-observability/podcast-archive.json")


def get_access_token():
    """Get OAuth2 access token."""
    with open(SECRETS_PATH) as f:
        secrets = json.load(f)
    podbean = secrets.get("podbean", {})
    client_id = podbean.get("clientId", "")
    client_secret = podbean.get("clientSecret", "")
    
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
        data = json.loads(r.read())
    return data["access_token"]


def get_episode_id_from_url(episode_url):
    """Extract episode ID from Podbean URL by fetching the episode."""
    # First try to list episodes and match by URL
    token = get_access_token()
    params = urllib.parse.urlencode({
        "access_token": token,
        "limit": 50,  # Get recent episodes
    })
    req = urllib.request.Request(
        f"{PODBEAN_API}/episodes?{params}",
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    
    # Find episode by URL
    for ep in data.get("episodes", []):
        if ep.get("permalink_url") == episode_url:
            return ep.get("id")
    
    return None


def update_episode_description(episode_id, title, new_description, status="publish", episode_type="public"):
    """Update an episode's description/content field."""
    token = get_access_token()
    body = urllib.parse.urlencode({
        "access_token": token,
        "title": title,
        "content": new_description,
        "status": status,
        "type": episode_type,
    }).encode()
    req = urllib.request.Request(
        f"{PODBEAN_API}/episodes/{episode_id}",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        print(f"Podbean API Error ({e.code}): {error_body}")
        raise


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 update-episode-description.py <date>")
        print("Example: python3 update-episode-description.py 2026-03-17")
        sys.exit(1)
    
    date = sys.argv[1]
    
    # Load archive to get episode info
    with open(ARCHIVE_PATH) as f:
        archive = json.load(f)
    
    # Find episode
    episode = None
    for ep in archive["episodes"]:
        if ep["date"] == date:
            episode = ep
            break
    
    if not episode:
        print(f"ERROR: No episode found for {date}")
        sys.exit(1)
    
    podbean_url = episode.get("podbeanUrl", "")
    if not podbean_url:
        print(f"ERROR: Episode {date} has no podbeanUrl")
        sys.exit(1)
    
    print(f"Updating episode: {episode['title']}")
    print(f"URL: {podbean_url}")
    
    # Simple description with just the summary (Podbean API has 500-char limit)
    new_description = episode['summary'][:500]
    
    print(f"\nNew description ({len(new_description)} chars):")
    print("─" * 60)
    print(new_description[:500])
    if len(new_description) > 500:
        print(f"... [truncated, total {len(new_description)} chars]")
    print("─" * 60)
    
    # Get episode ID
    print("\nFetching episode ID from Podbean...")
    episode_id = get_episode_id_from_url(podbean_url)
    if not episode_id:
        print("ERROR: Could not find episode ID")
        sys.exit(1)
    
    print(f"Episode ID: {episode_id}")
    
    # Update description
    print("\nUpdating description...")
    result = update_episode_description(
        episode_id, 
        episode['title'], 
        new_description,
        status="publish",
        episode_type="public"
    )
    
    print("✅ Success! Episode description updated.")
    print(f"View at: {podbean_url}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
