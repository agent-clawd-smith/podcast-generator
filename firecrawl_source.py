"""Firecrawl news search for podcast topics."""
import json
import os
import urllib.request
import urllib.error

FIRECRAWL_API = "https://api.firecrawl.dev/v1/search"

def search_topic(topic, api_key, limit=5):
    """Search Firecrawl for recent articles on a topic.
    Returns list of {"source": "firecrawl", "title": str, "url": str, "content": str}
    
    Logs credit usage to centralized tracker.
    """
    # Log credit usage (2 credits per search)
    try:
        import sys
        sys.path.insert(0, os.path.expanduser("~/repos/workspace-tools"))
        from firecrawl_tracker import log_usage
        log_usage(credits=2, service="podcast", operation="topic_search")
    except Exception:
        pass  # Don't fail if tracker unavailable
    
    body = json.dumps({
        "query": topic,
        "limit": limit,
        "lang": "en",
        "tbs": "qdr:d",  # Past 24 hours
        "scrapeOptions": {"formats": ["markdown"]},
    }).encode()
    req = urllib.request.Request(
        FIRECRAWL_API,
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        print(f"  [firecrawl] Error searching '{topic[:40]}': {e}")
        return []

    results = data.get("data", [])
    return [
        {
            "source": "firecrawl",
            "title": r.get("metadata", {}).get("title", r.get("title", "")),
            "url": r.get("url", ""),
            "content": r.get("markdown", r.get("content", ""))[:3000],
        }
        for r in results
        if r.get("markdown") or r.get("content")
    ]

def gather(topics, api_key):
    """Search all topics, return combined results."""
    all_results = []
    for topic in topics:
        print(f"  [firecrawl] Searching: {topic}")
        results = search_topic(topic, api_key)
        all_results.extend(results)
        print(f"    Found {len(results)} articles")
    return all_results

if __name__ == "__main__":
    secrets_path = os.path.expanduser("~/.openclaw/secrets.json")
    with open(secrets_path) as f:
        secrets = json.load(f)
    results = gather(["agentic AI services", "network automation"], secrets["firecrawl"]["apiKey"])
    print(f"\nTotal: {len(results)} articles")
    for r in results[:3]:
        print(f"  - {r['title'][:80]}")
