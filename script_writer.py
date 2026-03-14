"""Podcast script generation using Claude Opus via OpenRouter."""
import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone

OPENROUTER_API = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "anthropic/claude-opus-4"

# Cost logging - reuse pattern from polymarket
USAGE_LOG_PATH = os.path.expanduser("~/repos/llm-observability/usage-current-week.jsonl")  # shared budget tracking

def _log_cost(input_tokens, output_tokens, cost_usd, context=""):
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
        "session_file": "daily-podcast",
        "context": context[:150],
    }
    try:
        os.makedirs(os.path.dirname(USAGE_LOG_PATH), exist_ok=True)
        with open(USAGE_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except (IOError, OSError) as e:
        print(f"  [cost] Warning: failed to log: {e}")


def generate_script(stories, speaker_profiles, max_duration_minutes=18, api_key=""):
    """Generate a podcast script from ranked stories.

    Args:
        stories: List of {"title", "content", "url", "source"} dicts (pre-ranked)
        speaker_profiles: List of {"name", "style"} dicts
        max_duration_minutes: Target duration
        api_key: OpenRouter API key

    Returns:
        {"script": str, "title": str, "summary": str, "cost_usd": float}
    """
    # Gemini TTS supports exactly 2 voices — cap speakers at 2
    speaker_profiles = speaker_profiles[:2]

    # Build speaker tag instructions
    speaker_tags = []
    speaker_descriptions = []
    for i, sp in enumerate(speaker_profiles):
        tag = f"SPEAKER_{i+1}"
        speaker_tags.append(f"[{tag}] = {sp['name']}")
        speaker_descriptions.append(f"- {sp['name']}: {sp['style']}")

    # Target word count based on duration (~150 words/min speaking pace)
    target_words = int(max_duration_minutes * 150)

    # Build story summaries for the prompt
    story_block = ""
    for i, s in enumerate(stories, 1):
        story_block += f"\n### Story {i}: {s['title']}\nSource: {s.get('source', 'unknown')} | {s.get('url', '')}\n{s['content'][:1500]}\n"

    system_prompt = f"""You are a podcast script writer for a daily tech news briefing called "The Network & AI Brief".

Speaker tags (use these EXACTLY):
{chr(10).join(speaker_tags)}

Speaker personalities:
{chr(10).join(speaker_descriptions)}

Rules:
- Write a natural, conversational podcast script
- Use speaker tags at the start of each speaking turn: [SPEAKER_1]: text here
- Cover the most important stories first, with natural transitions
- Include brief analysis and implications, not just news summaries
- Add light banter between speakers but keep it professional
- Start with a brief intro/greeting, end with a sign-off
- Target approximately {target_words} words ({max_duration_minutes} minutes at speaking pace)
- Reference source URLs naturally (e.g., "according to a report from...")
- Do NOT use sound effects, music cues, or stage directions
- After the script, add a line "---SUMMARY---" followed by a 1-2 sentence summary of the episode suitable for a podcast listing. Describe the key themes and highlights conversationally — do NOT just list article titles."""

    user_prompt = f"""Write today's podcast script covering these stories:

{story_block}

Remember: ~{target_words} words, use [SPEAKER_1]/[SPEAKER_2]/etc. tags, conversational tone.
End with ---SUMMARY--- followed by a short episode description for the podcast feed."""

    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 8000,
        "temperature": 0.8,
    }).encode()

    req = urllib.request.Request(
        OPENROUTER_API,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/agent-clawd-smith/openclaw",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            response = json.loads(r.read())
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        print(f"  [script] OpenRouter API error: {e}")
        return None

    script = response["choices"][0]["message"]["content"]
    usage = response.get("usage", {})
    input_tokens = usage.get("prompt_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0)

    # Get cost from response or estimate
    cost_usd = 0.0
    if "usage" in response and "total_cost" in response["usage"]:
        cost_usd = response["usage"]["total_cost"]
    else:
        # Opus pricing: ~$15/M input, ~$75/M output
        cost_usd = (input_tokens / 1_000_000 * 15) + (output_tokens / 1_000_000 * 75)

    _log_cost(input_tokens, output_tokens, cost_usd, "podcast: script generation")

    # Extract title from script (first line after greeting usually)
    title = f"The Network & AI Brief — {datetime.now().strftime('%B %d, %Y')}"

    # Extract LLM-generated summary if present
    summary = ""
    if "---SUMMARY---" in script:
        parts = script.split("---SUMMARY---", 1)
        script = parts[0].rstrip()
        summary = parts[1].strip()
    if not summary:
        story_titles = [s["title"] for s in stories[:3]]
        summary = "Today: " + "; ".join(story_titles)

    return {
        "script": script,
        "title": title,
        "summary": summary[:500],
        "cost_usd": round(cost_usd, 4),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }

if __name__ == "__main__":
    print("script_writer.py — test mode")
    print("Use podcast-generate.py for full pipeline")
