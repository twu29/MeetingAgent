def clean_transcript(raw_data: list[dict]) -> tuple[str, list[dict]]:
    turns: list[dict] = []

    for segment in raw_data:
        speaker = segment.get("participant", {}).get("name", "Unknown")
        words = segment.get("words", [])
        if not words:
            continue

        text = " ".join(w.get("text", "") for w in words).strip()
        if not text:
            continue

        start_seconds = words[0].get("start_timestamp", {}).get("relative", 0)
        minutes = int(start_seconds // 60)
        seconds = int(start_seconds % 60)
        timestamp = f"{minutes:02d}:{seconds:02d}"

        # Merge with previous turn if same speaker and gap <= 1.2s
        if turns:
            prev = turns[-1]
            prev_end = prev.get("_end_seconds", 0)
            gap = start_seconds - prev_end

            if prev["speaker"] == speaker and gap <= 1.2:
                prev["text"] += " " + text
                prev["_end_seconds"] = words[-1].get("end_timestamp", {}).get("relative", 0)
                continue

        end_seconds = words[-1].get("end_timestamp", {}).get("relative", 0)
        turns.append({
            "timestamp": timestamp,
            "speaker": speaker,
            "text": text,
            "_end_seconds": end_seconds,
        })

    # Build cleaned text and remove internal tracking field
    lines = []
    for turn in turns:
        lines.append(f"[{turn['timestamp']}] {turn['speaker']} — {turn['text']}")
        del turn["_end_seconds"]

    cleaned_text = "\n".join(lines)
    return cleaned_text, turns
