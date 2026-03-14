"""Twitter/X search for podcast sources via Nitter.

Searches for topic-relevant tweets via Nitter search RSS.
No manual account list needed — discovers content by topic automatically.
Can optionally also pull feeds from specific accounts if configured.
"""
import json
import os
import re
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

# Nitter instances with search support (tried in order)
NITTER_INSTANCES = [
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.woodland.cafe",
]


def _clean_html(text):
    """Strip HTML tags from text."""
    return re.sub(r'<[^>]+>', '', text)


def _parse_feed(xml_data, source_label="nitter"):
    """Parse an RSS feed and return items from the last 24h."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        return []

    results = []
    for item in root.iter("item"):
        title = item.findtext("title", "")
        link = item.findtext("link", "")
        description = item.findtext("description", "")
        pub_date = item.findtext("pubDate", "")

        if pub_date:
            try:
                dt = parsedate_to_datetime(pub_date)
                if dt < cutoff:
                    continue
            except (ValueError, TypeError):
                pass

        content = _clean_html(description)
        if content.strip():
            results.append({
                "source": "twitter",
                "title": title[:120] if title else "Tweet",
                "url": link,
                "content": content[:2000],
            })

    return results


def search_topic(topic):
    """Search Nitter for recent tweets about a topic.
    Returns list of source dicts.
    """
    encoded = urllib.parse.quote(topic)

    for base_url in NITTER_INSTANCES:
        search_url = f"{base_url}/search/rss?f=tweets&q={encoded}"
        req = urllib.request.Request(search_url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                xml_data = r.read()
            results = _parse_feed(xml_data)
            if results:
                return results
        except (urllib.error.URLError, OSError):
            continue

    return []


def fetch_account(account):
    """Fetch recent tweets from a specific account (optional)."""
    for base_url in NITTER_INSTANCES:
        rss_url = f"{base_url}/{account}/rss"
        req = urllib.request.Request(rss_url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                xml_data = r.read()
            return _parse_feed(xml_data)
        except (urllib.error.URLError, OSError):
            continue
    return []


def gather(topics, accounts=None):
    """Search Twitter/X for each topic, plus optional specific accounts.

    Args:
        topics: List of topic strings to search.
        accounts: Optional list of Twitter handles to also pull.

    Returns:
        List of source dicts.
    """
    all_results = []
    seen_urls = set()

    # Topic-based search (primary)
    for topic in topics:
        print(f"  [twitter] Searching: {topic}")
        results = search_topic(topic)
        for r in results:
            if r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                all_results.append(r)
        print(f"    Found {len(results)} tweets")

    # Optional: specific accounts
    if accounts:
        for account in accounts:
            print(f"  [twitter] Fetching @{account}")
            results = fetch_account(account)
            for r in results:
                if r["url"] not in seen_urls:
                    seen_urls.add(r["url"])
                    all_results.append(r)
            print(f"    Found {len(results)} tweets")

    return all_results


if __name__ == "__main__":
    results = gather(["network automation", "agentic AI"])
    print(f"\nTotal: {len(results)} tweets")
    for r in results[:5]:
        print(f"  - {r['title'][:80]}")
