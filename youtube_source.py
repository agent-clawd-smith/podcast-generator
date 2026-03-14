"""YouTube topic search for podcast sources.

Searches YouTube by topic keywords, fetches transcripts for relevant results.
No manual channel list needed — discovers content automatically.
"""
import json
import os
import re
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone, timedelta


def search_videos(query, max_results=5):
    """Search YouTube for recent videos matching a query.
    Uses YouTube's search page scraping via Invidious API (no API key needed).
    Falls back to a simple RSS-based approach if Invidious is unavailable.
    """
    # Try Invidious instances for search (free, no API key)
    invidious_instances = [
        "https://vid.puffyan.us",
        "https://invidious.fdn.fr",
        "https://invidious.nerdvpn.de",
    ]

    for instance in invidious_instances:
        search_url = (
            f"{instance}/api/v1/search"
            f"?q={urllib.parse.quote(query)}"
            f"&type=video&sort=upload_date&date=today"
            f"&fields=videoId,title,publishedText"
        )
        req = urllib.request.Request(search_url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                results = json.loads(r.read())
            videos = []
            for item in results[:max_results]:
                if item.get("videoId"):
                    videos.append({
                        "video_id": item["videoId"],
                        "title": item.get("title", ""),
                    })
            if videos:
                return videos
        except (urllib.error.URLError, json.JSONDecodeError, OSError):
            continue

    # Fallback: use Firecrawl-discovered YouTube links (caller handles this)
    return []


def get_transcript(video_id, topics):
    """Get transcript for a video and check relevance to topics.
    Returns transcript text if relevant, None otherwise.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        ytt_api = YouTubeTranscriptApi()
        transcript = ytt_api.fetch(video_id)

        # Combine transcript text
        full_text = " ".join(snippet.text for snippet in transcript)

        # Check keyword relevance
        text_lower = full_text.lower()
        topic_keywords = []
        for topic in topics:
            topic_keywords.extend(topic.lower().split())

        matches = sum(1 for kw in set(topic_keywords) if kw in text_lower)
        if matches >= 2:  # At least 2 keyword matches
            return full_text[:5000]
        return None
    except Exception as e:
        print(f"    [youtube] Transcript unavailable for {video_id}: {e}")
        return None


def gather(topics):
    """Search YouTube for each topic and extract relevant transcripts.

    Args:
        topics: List of topic strings to search for.

    Returns:
        List of source dicts with transcripts.
    """
    all_results = []
    seen_ids = set()

    for topic in topics:
        print(f"  [youtube] Searching: {topic}")
        videos = search_videos(topic, max_results=3)
        print(f"    Found {len(videos)} videos")

        for video in videos:
            vid = video["video_id"]
            if vid in seen_ids:
                continue
            seen_ids.add(vid)

            transcript = get_transcript(vid, topics)
            if transcript:
                all_results.append({
                    "source": "youtube",
                    "title": video["title"],
                    "url": f"https://youtube.com/watch?v={vid}",
                    "content": transcript,
                })
                print(f"    Relevant: {video['title'][:60]}")

    return all_results


if __name__ == "__main__":
    results = gather(["network automation", "agentic AI services"])
    print(f"\nTotal: {len(results)} relevant videos")
    for r in results[:3]:
        print(f"  - {r['title'][:80]}")
