"""Publish podcast episodes to Podbean via their API.

Flow:
  1. OAuth2 client_credentials → access_token
  2. POST /files/uploadAuthorize → presigned_url + media_key
  3. PUT audio file to presigned_url
  4. POST /episodes with title, content, media_key
"""
import json
import os
import urllib.request
import urllib.error
import urllib.parse
import base64

PODBEAN_API = "https://api.podbean.com/v1"
SECRETS_PATH = os.path.expanduser("~/.openclaw/secrets.json")


def _get_access_token(client_id, client_secret):
    """Get OAuth2 access token using client credentials grant."""
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


def _authorize_upload(access_token, filename, filesize):
    """Get a presigned upload URL and media_key from Podbean."""
    params = urllib.parse.urlencode({
        "access_token": access_token,
        "filename": filename,
        "filesize": filesize,
        "content_type": "audio/mpeg",
    })
    req = urllib.request.Request(
        f"{PODBEAN_API}/files/uploadAuthorize?{params}",
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    return data["presigned_url"], data["file_key"]


def _upload_file(presigned_url, file_path):
    """Upload audio file to the presigned URL."""
    with open(file_path, "rb") as f:
        file_data = f.read()
    req = urllib.request.Request(
        presigned_url,
        data=file_data,
        headers={"Content-Type": "audio/mpeg"},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.status


def _publish_episode(access_token, title, content, media_key):
    """Create and publish a new episode on Podbean."""
    body = urllib.parse.urlencode({
        "access_token": access_token,
        "title": title,
        "content": content,
        "status": "publish",
        "type": "public",
        "media_key": media_key,
    }).encode()
    req = urllib.request.Request(
        f"{PODBEAN_API}/episodes",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def publish(audio_path, title, summary):
    """Publish a podcast episode to Podbean.

    Args:
        audio_path: Path to the MP3 file.
        title: Episode title.
        summary: Episode description/summary.

    Returns:
        {"success": True, "episode_url": str} or {"success": False, "error": str}
    """
    try:
        with open(SECRETS_PATH) as f:
            secrets = json.load(f)
        podbean = secrets.get("podbean", {})
        client_id = podbean.get("clientId", "")
        client_secret = podbean.get("clientSecret", "")
        if not client_id or not client_secret:
            return {"success": False, "error": "No podbean clientId/clientSecret in secrets.json"}
    except (IOError, json.JSONDecodeError) as e:
        return {"success": False, "error": f"Failed to load secrets: {e}"}

    try:
        print("  [podbean] Authenticating...")
        token = _get_access_token(client_id, client_secret)

        filesize = os.path.getsize(audio_path)
        filename = os.path.basename(audio_path)
        print(f"  [podbean] Authorizing upload ({filesize} bytes)...")
        presigned_url, media_key = _authorize_upload(token, filename, filesize)

        print("  [podbean] Uploading audio...")
        _upload_file(presigned_url, audio_path)

        print("  [podbean] Publishing episode...")
        result = _publish_episode(token, title, summary, media_key)

        episode_url = result.get("episode", {}).get("permalink_url", "")
        print(f"  [podbean] Published: {episode_url}")
        return {"success": True, "episode_url": episode_url}

    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode()[:500]
        except Exception:
            pass
        error_msg = f"Podbean API HTTP {e.code}: {error_body}"
        print(f"  [podbean] {error_msg}")
        return {"success": False, "error": error_msg}
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        error_msg = f"Podbean API error: {e}"
        print(f"  [podbean] {error_msg}")
        return {"success": False, "error": error_msg}


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 4:
        print("Usage: python3 podbean_publisher.py <audio.mp3> <title> <summary>")
        sys.exit(1)
    result = publish(sys.argv[1], sys.argv[2], sys.argv[3])
    print(json.dumps(result, indent=2))
