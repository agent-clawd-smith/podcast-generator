#!/usr/bin/env python3
"""Weekly podcast memory consolidation — runs Sunday 4 AM.

Reads accumulated memory entries, consolidates by:
- Removing verbatim duplicates
- Identifying repeated themes (frequency = significance)
- Distilling personality traits
- Keeping significant one-offs

Rewrites podcast-memory.md as curated, compact version (~500-1000 words).
Backs up old version before overwriting.
"""
import json
import os
import sys
import urllib.request
import urllib.error
import shutil
from datetime import datetime, timezone

# Paths
MEMORY_FILE = os.path.join(os.path.dirname(__file__), "podcast-memory.md")
BACKUP_DIR = os.path.join(os.path.dirname(__file__), "memory-backups")
SECRETS_PATH = os.path.expanduser("~/.openclaw/secrets.json")
USAGE_LOG_PATH = os.path.expanduser("~/repos/llm-observability/usage-current-week.jsonl")

# API config
OPENROUTER_API = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "anthropic/claude-sonnet-4"  # Good balance of cost/quality


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
        "session_file": "podcast-memory-weekly",
        "context": "podcast: weekly memory consolidation",
    }
    try:
        os.makedirs(os.path.dirname(USAGE_LOG_PATH), exist_ok=True)
        with open(USAGE_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except (IOError, OSError):
        pass


def consolidate_memory(current_memory, api_key):
    """Consolidate accumulated memory entries into curated version."""
    
    prompt = f"""You are consolidating podcast memory entries from the past week. The goal is to maintain continuity and personality across episodes while keeping the memory file compact and actionable.

CURRENT MEMORY FILE:
{current_memory}

Consolidate this into a curated memory file (~500-1000 words) that includes:

1. **Speaker Personalities** — Distilled traits that emerged (Alex's enthusiasm, Sam's skepticism, etc.)
2. **Running Themes** — Topics that came up multiple times (FREQUENCY = SIGNIFICANCE)
   - Explicitly note: "X came up 3x this week → becoming a recurring theme"
3. **Active Callbacks** — Apologies, corrections, or promises worth honoring
4. **Notable One-offs** — Significant reactions/insights worth keeping

CRITICAL RULES:
- Remove verbatim duplicates
- When a theme appears multiple times, note the frequency
- Keep only what adds value to future episodes
- Target 500-1000 words (be ruthless with noise)
- Format with clear headers and bullet points

Output the consolidated memory file content ONLY (no preamble)."""

    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 2500,
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
        with urllib.request.urlopen(req, timeout=120) as r:
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
    print("=== Podcast Memory Weekly Consolidation ===\n")
    
    # Check if memory file exists
    if not os.path.exists(MEMORY_FILE):
        print("No memory file found — nothing to consolidate")
        return
    
    # Read current memory
    with open(MEMORY_FILE) as f:
        current_memory = f.read()
    
    current_words = len(current_memory.split())
    print(f"Current memory file: {current_words} words")
    
    if current_words < 200:
        print("Memory file too small to consolidate — skipping")
        return
    
    # Create backup
    os.makedirs(BACKUP_DIR, exist_ok=True)
    backup_name = f"podcast-memory-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.md"
    backup_path = os.path.join(BACKUP_DIR, backup_name)
    shutil.copy2(MEMORY_FILE, backup_path)
    print(f"Backup created: {backup_path}")
    
    # Consolidate
    secrets = load_secrets()
    api_key = secrets.get("openrouter", {}).get("apiKey", "")
    if not api_key:
        print("ERROR: No OpenRouter API key found")
        return
    
    print("Consolidating memory...")
    result = consolidate_memory(current_memory, api_key)
    
    if not result:
        print("ERROR: Consolidation failed")
        return
    
    consolidated_words = len(result['content'].split())
    print(f"Consolidated: {current_words} → {consolidated_words} words (${result['cost_usd']:.4f})")
    
    # Alert if memory is bloated
    if consolidated_words > 1500:
        print(f"⚠️  WARNING: Consolidated memory is {consolidated_words} words (target <1500)")
        print("   Consider more aggressive distillation in future consolidations")
    
    # Write consolidated version with header
    header = f"""# Podcast Memory & Continuity

This file maintains continuity across podcast episodes. Updated daily with memorable moments, consolidated weekly.

Last consolidation: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
Backup: {backup_name}

---

"""
    
    with open(MEMORY_FILE, "w") as f:
        f.write(header + result['content'])
    
    print(f"Memory file updated: {MEMORY_FILE}")
    print(f"\n✓ Complete! Cost: ${result['cost_usd']:.4f}")
    print(f"  Reduction: {current_words - consolidated_words} words removed")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
