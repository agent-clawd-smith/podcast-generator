"""Gemini 2.5 Flash TTS for multi-speaker podcast audio."""
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
import urllib.error
from datetime import datetime, timezone

GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"

# Map speaker indices to Gemini TTS voices
VOICE_MAP = {
    0: "Aoede",    # Speaker 1 - warm, engaging
    1: "Charon",   # Speaker 2 - deeper, authoritative
    2: "Fenrir",   # Speaker 3 - bright, energetic
    3: "Kore",     # Speaker 4 - expressive, dynamic
}

# Cost logging
USAGE_LOG_PATH = os.path.expanduser("~/repos/llm-observability/usage-current-week.jsonl")  # shared budget tracking


def _log_cost(cost_usd, context=""):
    """Log TTS cost to shared budget system."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": "google/gemini-2.5-flash-tts",
        "provider": "google-direct",
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read": 0,
        "cache_write": 0,
        "total_tokens": 0,
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


def _parse_speaker_turns(script, speaker_count):
    """Parse script into speaker turns for multi-speaker TTS.
    Returns list of {"speaker_idx": int, "text": str}
    """
    turns = []
    current_speaker = 0
    current_text = []

    for line in script.split("\n"):
        line = line.strip()
        if not line:
            continue

        # Check for speaker tag: [SPEAKER_N]:
        match = re.match(r'\[SPEAKER_(\d+)\]:\s*(.*)', line)
        if match:
            # Save previous turn
            if current_text:
                turns.append({"speaker_idx": current_speaker, "text": " ".join(current_text)})
                current_text = []
            current_speaker = int(match.group(1)) - 1  # 0-indexed
            if current_speaker >= speaker_count:
                current_speaker = 0
            remaining = match.group(2).strip()
            if remaining:
                current_text.append(remaining)
        elif line:
            current_text.append(line)

    # Don't forget last turn
    if current_text:
        turns.append({"speaker_idx": current_speaker, "text": " ".join(current_text)})

    return turns


def _remap_to_two_speakers(turns, speaker_names):
    """Remap all speakers to exactly 2 voices for Gemini TTS.

    Groups speakers into two roles:
    - Speakers at even indices (0, 2) → Voice A
    - Speakers at odd indices (1, 3) → Voice B

    Returns (remapped_turns, speaker_a_name, speaker_b_name).
    """
    # Pick the two most-used speakers as the primary voices
    from collections import Counter
    counts = Counter(t["speaker_idx"] for t in turns)
    top_two = [idx for idx, _ in counts.most_common(2)]
    if len(top_two) < 2:
        top_two.append(0 if top_two[0] != 0 else 1)

    voice_a_idx, voice_b_idx = top_two[0], top_two[1]
    name_a = speaker_names.get(voice_a_idx, "Host")
    name_b = speaker_names.get(voice_b_idx, "Co-host")

    # Map all speakers: even-indexed → A, odd-indexed → B
    remapped = []
    for turn in turns:
        if turn["speaker_idx"] == voice_a_idx or turn["speaker_idx"] % 2 == 0:
            remapped.append({"speaker_idx": 0, "text": turn["text"]})
        else:
            remapped.append({"speaker_idx": 1, "text": turn["text"]})

    return remapped, name_a, name_b


def _chunk_turns(turns, max_chars=4000, min_chars=200):
    """Group turns into chunks respecting character limits.
    Returns list of turn lists.
    """
    chunks = []
    current_chunk = []
    current_chars = 0

    for turn in turns:
        turn_chars = len(turn["text"])
        if current_chars + turn_chars > max_chars and current_chunk and current_chars >= min_chars:
            chunks.append(current_chunk)
            current_chunk = []
            current_chars = 0
        current_chunk.append(turn)
        current_chars += turn_chars

    if current_chunk:
        # Merge tiny trailing chunk into previous
        if current_chars < min_chars and chunks:
            chunks[-1].extend(current_chunk)
        else:
            chunks.append(current_chunk)

    return chunks


def _build_multi_speaker_text(turns, speaker_names):
    """Build text with speaker labels for Gemini multi-speaker TTS.

    Gemini expects: "Speaker Name: dialogue text here"
    One line per speaker turn.
    """
    lines = []
    for turn in turns:
        name = speaker_names.get(turn["speaker_idx"], f"Speaker {turn['speaker_idx'] + 1}")
        lines.append(f"{name}: {turn['text']}")
    return "\n".join(lines)


def generate_audio_chunk(text, api_key, speaker_voice_configs):
    """Generate audio for a text chunk using Gemini TTS.
    Returns raw audio bytes or None on failure.
    """
    body = json.dumps({
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "response_modalities": ["AUDIO"],
            "speech_config": {
                "multi_speaker_voice_config": {
                    "speaker_voice_configs": speaker_voice_configs
                }
            }
        }
    }).encode()

    url = f"{GEMINI_API}?key={api_key}"
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            response = json.loads(r.read())
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode()[:500]
        except Exception:
            pass
        print(f"  [tts] Gemini API HTTP {e.code}: {error_body}")
        return None
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        print(f"  [tts] Gemini API error: {e}")
        return None

    # Extract audio data from response
    try:
        candidates = response.get("candidates", [])
        if not candidates:
            print(f"  [tts] No candidates in response: {json.dumps(response)[:300]}")
            return None
        parts = candidates[0].get("content", {}).get("parts", [])
        for part in parts:
            if "inlineData" in part:
                audio_b64 = part["inlineData"]["data"]
                mime = part["inlineData"].get("mimeType", "")
                audio_bytes = base64.b64decode(audio_b64)
                return (audio_bytes, mime)
        print(f"  [tts] No audio data in response parts")
    except (KeyError, IndexError) as e:
        print(f"  [tts] Error extracting audio: {e}")

    return None


def generate_audio(script, speaker_count, api_key, output_path, speaker_profiles=None):
    """Generate full podcast audio from script.

    Args:
        script: Full podcast script with [SPEAKER_N]: tags
        speaker_count: Number of speakers
        api_key: Google API key
        output_path: Path for final MP3 file
        speaker_profiles: Optional list of {"name": str, "style": str} dicts

    Returns:
        {"success": bool, "duration_seconds": int, "cost_usd": float} or None
    """
    print("  [tts] Parsing speaker turns...")
    turns = _parse_speaker_turns(script, speaker_count)
    if not turns:
        print("  [tts] No speaker turns found in script")
        return None

    print(f"  [tts] Found {len(turns)} speaker turns")

    # Build speaker name and voice map (always 2 speakers)
    name_a = speaker_profiles[0].get("name", "Host") if speaker_profiles else "Host"
    name_b = speaker_profiles[1].get("name", "Co-host") if speaker_profiles and len(speaker_profiles) > 1 else "Co-host"
    tts_names = {0: name_a, 1: name_b}
    voice_a = VOICE_MAP.get(0, "Aoede")
    voice_b = VOICE_MAP.get(1, "Charon")
    print(f"  [tts] Voices: {name_a}={voice_a}, {name_b}={voice_b}")

    speaker_voice_configs = [
        {"speaker": name_a, "voice_config": {"prebuilt_voice_config": {"voice_name": voice_a}}},
        {"speaker": name_b, "voice_config": {"prebuilt_voice_config": {"voice_name": voice_b}}},
    ]

    # Chunk turns for API limits
    chunks = _chunk_turns(turns)
    print(f"  [tts] Split into {len(chunks)} chunks")

    # Generate audio for each chunk
    wav_files = []
    tmpdir = tempfile.mkdtemp(prefix="podcast_tts_")

    try:
        for i, chunk_turns in enumerate(chunks):
            print(f"  [tts] Generating chunk {i+1}/{len(chunks)}...")
            text = _build_multi_speaker_text(chunk_turns, tts_names)
            chunk_result = generate_audio_chunk(text, api_key, speaker_voice_configs)

            # Retry once on failure
            if chunk_result is None:
                import time
                print(f"  [tts] Chunk {i+1} failed, retrying in 3s...")
                time.sleep(3)
                chunk_result = generate_audio_chunk(text, api_key, speaker_voice_configs)
            if chunk_result is None:
                print(f"  [tts] Chunk {i+1} failed after retry")
                return None

            audio_bytes, mime_type = chunk_result
            raw_path = os.path.join(tmpdir, f"chunk_{i:03d}.raw")
            with open(raw_path, "wb") as f:
                f.write(audio_bytes)

            # Convert raw PCM to WAV (Gemini returns audio/L16 big-endian PCM)
            wav_path = os.path.join(tmpdir, f"chunk_{i:03d}.wav")
            sample_rate = "24000"
            if "rate=" in mime_type:
                sample_rate = mime_type.split("rate=")[1].split(";")[0].strip()
            conv = subprocess.run(
                [FFMPEG, "-y", "-f", "s16le", "-ar", sample_rate, "-ac", "1",
                 "-i", raw_path, wav_path],
                capture_output=True, timeout=30,
            )
            if conv.returncode != 0:
                print(f"  [tts] PCM->WAV failed: {conv.stderr.decode()[:200]}")
                return None
            wav_files.append(wav_path)

        # Concatenate WAV files and convert to MP3
        if len(wav_files) == 1:
            result = subprocess.run(
                [FFMPEG, "-y", "-i", wav_files[0], "-codec:a", "libmp3lame", "-q:a", "2", output_path],
                capture_output=True, timeout=60,
            )
        else:
            concat_list = os.path.join(tmpdir, "concat.txt")
            with open(concat_list, "w") as f:
                for wf in wav_files:
                    f.write(f"file '{wf}'\n")
            result = subprocess.run(
                [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
                 "-codec:a", "libmp3lame", "-q:a", "2", output_path],
                capture_output=True, timeout=120,
            )

        if result.returncode != 0:
            print(f"  [tts] ffmpeg error: {result.stderr.decode()[:500]}")
            return None

        # Get duration
        probe = subprocess.run(
            [FFMPEG, "-i", output_path, "-f", "null", "-"],
            capture_output=True, timeout=30,
        )
        duration_seconds = 0
        duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+)", probe.stderr.decode())
        if duration_match:
            h, m, s = int(duration_match.group(1)), int(duration_match.group(2)), int(duration_match.group(3))
            duration_seconds = h * 3600 + m * 60 + s

        # Estimate TTS cost (~$0.01-0.05 based on character count)
        total_chars = sum(len(t["text"]) for t in turns)
        cost_usd = round(total_chars * 0.000015, 4)  # ~$15/M chars
        _log_cost(cost_usd, f"podcast: TTS {len(chunks)} chunks, {total_chars} chars")

        print(f"  [tts] Audio generated: {output_path}")
        print(f"  [tts] Duration: ~{duration_seconds}s, Cost: ${cost_usd}")

        return {
            "success": True,
            "duration_seconds": duration_seconds,
            "cost_usd": cost_usd,
        }

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    print("tts_generator.py — test mode")
    print("Use podcast-generate.py for full pipeline")
