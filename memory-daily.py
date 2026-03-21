#!/usr/bin/env python3
"""Daily podcast memory extractor — runs before podcast generation.

Reads yesterday's transcript, extracts memorable moments, appends to memory file.
Runs at 6:15 AM daily (15 min before podcast generation at 6:30 AM).
"""
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

# Paths
EPISODES_DIR = os.path.join(os.path.dirname(__file__), "episodes")
MEMORY_FILE = os.path.join(os.path.dirname(__file__), "podcast-memory.md")
SECRETS_PATH = os.path.expanduser("~/.openclaw/secrets.json")
USAGE_LOG_PATH = os.path.expanduser("~/repos/llm-observability/usage-current-week.jsonl")

# API config
OPENROUTER_API = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "anthropic/claude-sonnet-4"  # Cheaper than Opus for extraction


def load_secrets():
    with open(SECRETS_PATH) as f:
        return json.load(f)


def log_cost(input_tokens, output_tokens, cost_usd):
    """Log LLM cost to shared budget system."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": MODEL,
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
        "session_file": "podcast-memory-daily",
        "context": "podcast: daily memory extraction",
    }
    try:
        os.makedirs(os.path.dirname(USAGE_LOG_PATH), exist_ok=True)
        with open(USAGE_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except (IOError, OSError):
        pass


def extract_memory(transcript, api_key):
    """Extract memorable moments from yesterday's transcript."""
    
    prompt = f"""Review this podcast transcript and extract memorable moments that should carry forward to future episodes.

TRANSCRIPT:
{transcript}

Extract and organize:
1. **Speaker Personality Moments** — specific reactions, opinions, or communication styles worth maintaining
2. **Running Themes/Debates** — topics that sparked interesting discussion between speakers
3. **Callbacks Worth Keeping** — apologies, corrections, promises to follow up
4. **Notable One-offs** — surprising reactions, strong opinions, unique insights

Format as brief bullet points. Be selective — only capture what's genuinely memorable and worth referencing in future episodes.

Keep it under 300 words total."""

    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 800,
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
        with urllib.request.urlopen(req, timeout=60) as r:
            response = json.loads(r.read())
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        print(f"ERROR: API request failed: {e}")
        return None

    content = response["choices"][0]["message"]["content"]
    usage = response.get("usage", {})
    input_tokens = usage.get("prompt_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0)
    
    # Sonnet-4 pricing: ~$3/M input, ~$15/M output
    cost_usd = (input_tokens / 1_000_000 * 3) + (output_tokens / 1_000_000 * 15)
    log_cost(input_tokens, output_tokens, cost_usd)

    return {
        "content": content,
        "cost_usd": cost_usd,
        "tokens": input_tokens + output_tokens,
    }


def main():
    print("=== Podcast Memory Daily Extraction ===\n")
    
    # Get yesterday's date
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    transcript_path = os.path.join(EPISODES_DIR, f"{yesterday}.txt")
    
    # Check if transcript exists
    if not os.path.exists(transcript_path):
        print(f"No transcript found for {yesterday} — skipping memory extraction")
        return
    
    print(f"Reading transcript: {yesterday}")
    with open(transcript_path) as f:
        transcript = f.read()
    
    word_count = len(transcript.split())
    print(f"  Transcript: {word_count} words")
    
    # Extract memories
    secrets = load_secrets()
    api_key = secrets.get("openrouter", {}).get("apiKey", "")
    if not api_key:
        print("ERROR: No OpenRouter API key found")
        return
    
    print("  Extracting memorable moments...")
    result = extract_memory(transcript, api_key)
    
    if not result:
        print("ERROR: Memory extraction failed")
        return
    
    print(f"  Extracted {len(result['content'].split())} words (${result['cost_usd']:.4f})")
    
    # Append to memory file
    memory_entry = f"\n\n---\n## {yesterday}\n\n{result['content']}\n"
    
    with open(MEMORY_FILE, "a") as f:
        f.write(memory_entry)
    
    print(f"  Appended to {MEMORY_FILE}")
    print(f"\n✓ Complete! Cost: ${result['cost_usd']:.4f}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
