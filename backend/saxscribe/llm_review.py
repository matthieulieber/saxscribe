from __future__ import annotations

import base64
import json
import math
import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI

from .analysis import AudioFacts


class AIReviewError(RuntimeError):
    """An enabled OpenAI verification stage failed."""

    def __init__(self, stage: str, error: Exception | str):
        detail = _error_detail(error)
        super().__init__(f"AI verification failed during {stage}: {detail}")
        self.stage = stage
        self.detail = detail


@dataclass
class ReviewResult:
    used: bool
    summary: str
    researched_song: dict
    selected_context: dict
    corrections: list[dict]
    sources: list[dict]
    audio_comparison: list[dict]
    models: dict

    def to_dict(self) -> dict:
        return asdict(self)


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "researched_song": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "matched_recording": {"type": "string"},
                "concert_key": {"type": "string"},
                "mode": {"type": "string", "enum": ["major", "minor", "unknown"]},
                "bpm": {"type": "number"},
                "meter": {"type": "string"},
                "confidence": {"type": "number"},
            },
            "required": ["matched_recording", "concert_key", "mode", "bpm", "meter", "confidence"],
        },
        "selected_context": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "concert_key": {"type": "string"},
                "mode": {"type": "string", "enum": ["major", "minor"]},
                "bpm": {"type": "number"},
                "meter": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["concert_key", "mode", "bpm", "meter", "reason"],
        },
        "corrections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "index": {"type": "integer"},
                    "action": {"type": "string", "enum": ["delete", "change", "insert"]},
                    "pitch": {"type": "integer"},
                    "start_beat": {"type": "number"},
                    "duration_beats": {"type": "number"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["index", "action", "pitch", "start_beat", "duration_beats", "confidence", "reason"],
            },
        },
    },
    "required": ["summary", "researched_song", "selected_context", "corrections"],
}


def review(
    *,
    title: str,
    artist: str,
    facts: AudioFacts,
    original_path: Path,
    wind_path: Path,
    raw_events: list[dict],
    cleaned_events: list[dict],
    draft_xml_path: Path,
    transcription_evidence: dict,
    model: str,
    audio_model: str,
    audio_chunk_seconds: int,
    audio_max_chunks: int,
    client: OpenAI | None = None,
) -> ReviewResult:
    """Run the enabled web, audio, and structured evidence review.

    This deliberately uses separate API calls. Web search and strict structured
    output are not coupled, so one tool mode cannot silently invalidate the other.
    """
    if not os.getenv("OPENAI_API_KEY") and client is None:
        raise AIReviewError("preflight", "OPENAI_API_KEY is missing. Add it to .env and restart SaxScribe.")
    client = client or OpenAI(timeout=240.0, max_retries=2)

    research_text, sources = _research_song(client, title, artist, facts, model)
    audio_comparison = _compare_audio(
        client=client,
        original_path=original_path,
        wind_path=wind_path,
        duration_seconds=facts.duration_seconds,
        cleaned_events=cleaned_events,
        transcription_evidence=transcription_evidence,
        audio_model=audio_model,
        chunk_seconds=audio_chunk_seconds,
        max_chunks=audio_max_chunks,
    )
    xml_text = draft_xml_path.read_text(encoding="utf-8")
    data = _structured_review(
        client=client,
        title=title,
        artist=artist,
        facts=facts,
        raw_events=raw_events,
        cleaned_events=cleaned_events,
        xml_text=xml_text,
        transcription_evidence=transcription_evidence,
        research_text=research_text,
        audio_comparison=audio_comparison,
        model=model,
    )
    _validate_result(data, cleaned_events)
    return ReviewResult(
        used=True,
        summary=data["summary"],
        researched_song=data["researched_song"],
        selected_context=data["selected_context"],
        corrections=data["corrections"],
        sources=sources,
        audio_comparison=audio_comparison,
        models={"reasoning": model, "audio": audio_model},
    )


def _research_song(client: OpenAI, title: str, artist: str, facts: AudioFacts, model: str) -> tuple[str, list[dict]]:
    if not title.strip() and not artist.strip():
        return "No title or artist was supplied, so no recording-specific web research was attempted.", []
    prompt = f"""
Research the exact recorded version below. Find reputable evidence for concert key/mode, tempo, and meter. Distinguish studio, live, remastered, sped-up, or cover versions. Treat crowdsourced key/BPM sites as cross-checks, not authority. Return a concise evidence memo and cite every web claim.

Title: {title or '(unknown)'}
Artist: {artist or '(unknown)'}
Measurements from the uploaded original mix: {json.dumps(facts.to_dict(), separators=(',', ':'))}
"""
    try:
        response = client.responses.create(model=model, tools=[{"type": "web_search"}], input=prompt)
        output_text = (getattr(response, "output_text", None) or "").strip()
        if not output_text:
            raise ValueError("The web-research call returned no text.")
        return output_text, _extract_citations(response)
    except Exception as exc:
        raise AIReviewError("song research", exc) from exc


def _compare_audio(
    *,
    client: OpenAI,
    original_path: Path,
    wind_path: Path,
    duration_seconds: float,
    cleaned_events: list[dict],
    transcription_evidence: dict,
    audio_model: str,
    chunk_seconds: int,
    max_chunks: int,
) -> list[dict]:
    if not shutil.which("ffmpeg"):
        raise AIReviewError("audio preparation", "FFmpeg is missing. Run scripts/setup-mac.sh again.")
    chunk_seconds = max(15, min(int(chunk_seconds), 90))
    chunk_count = max(1, int(math.ceil(float(duration_seconds) / chunk_seconds)))
    if chunk_count > max_chunks:
        maximum = chunk_seconds * max_chunks
        raise AIReviewError(
            "audio preparation",
            f"The recording is {duration_seconds:.1f}s, but AI audio review is limited to {maximum}s. "
            "Upload a shorter passage or increase OPENAI_AUDIO_MAX_CHUNKS.",
        )

    observations: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="saxscribe-ai-") as directory:
        temp_dir = Path(directory)
        for chunk_index in range(chunk_count):
            start = chunk_index * chunk_seconds
            duration = min(chunk_seconds, max(0.1, duration_seconds - start))
            original_wav = temp_dir / f"original-{chunk_index}.wav"
            wind_wav = temp_dir / f"wind-{chunk_index}.wav"
            _make_review_wav(original_path, original_wav, start, duration)
            _make_review_wav(wind_path, wind_wav, start, duration)
            end = start + duration
            local_notes = [
                item for item in transcription_evidence.get("notes", [])
                if start - 0.05 <= float(item.get("start_seconds", -1)) < end + 0.05
            ]
            prompt = (
                f"You are hearing two synchronized clips from {start:.2f}s to {end:.2f}s. "
                "The first is the ORIGINAL MIX; the second is the ISOLATED WIND/HORN STEM. "
                "Compare the sax line across them and assess separation leakage, missing phrases, obvious octave errors, "
                "attacks/rests, meter/downbeat clues, and whether the candidate note sequence follows the audible sax. "
                "Do not claim sample-accurate pitch measurements; the local pYIN table is the numeric authority. "
                "Return a concise text memo with time ranges and concrete discrepancies.\n"
                f"Local note evidence for this interval: {json.dumps(local_notes, separators=(',', ':'))}"
            )
            content = [
                {"type": "text", "text": prompt + "\nORIGINAL MIX follows:"},
                {"type": "input_audio", "input_audio": {"data": _encoded(original_wav), "format": "wav"}},
                {"type": "text", "text": "ISOLATED WIND/HORN STEM follows:"},
                {"type": "input_audio", "input_audio": {"data": _encoded(wind_wav), "format": "wav"}},
            ]
            try:
                completion = client.chat.completions.create(
                    model=audio_model,
                    modalities=["text"],
                    messages=[{"role": "user", "content": content}],
                    max_completion_tokens=1200,
                )
                message = completion.choices[0].message
                memo = (getattr(message, "content", None) or "").strip()
                if not memo and getattr(message, "audio", None):
                    memo = (getattr(message.audio, "transcript", None) or "").strip()
                if not memo:
                    raise ValueError("The audio comparison call returned no text.")
            except Exception as exc:
                raise AIReviewError(f"audio comparison (chunk {chunk_index + 1}/{chunk_count})", exc) from exc
            observations.append(
                {
                    "chunk": chunk_index + 1,
                    "start_seconds": round(start, 3),
                    "end_seconds": round(end, 3),
                    "memo": memo,
                }
            )
    return observations


def _structured_review(
    *,
    client: OpenAI,
    title: str,
    artist: str,
    facts: AudioFacts,
    raw_events: list[dict],
    cleaned_events: list[dict],
    xml_text: str,
    transcription_evidence: dict,
    research_text: str,
    audio_comparison: list[dict],
    model: str,
) -> dict:
    prompt = f"""
You are the final verifier for a monophonic sax transcription. Reproduce the evidence-based manual workflow: compare the original-mix measurements, isolated-stem measurements, raw MIDI, deterministic cleaned MIDI, draft MusicXML, recording-specific web research, and audio-model listening memos.

Evidence priority:
1. Local pYIN pitch frames and onset measurements from the isolated stem.
2. Beat/tempo/harmonic measurements from the original mix.
3. Agreement between raw MIDI, cleaned MIDI, and MusicXML.
4. Audio-model memos, which are qualitative only.
5. Web facts, only when they clearly match the exact recording.

Make conservative note corrections only when confidence is at least 0.80. Never invent notes from a chord chart or web result. Preserve expressive syncopation. Change/delete corrections must reference an existing cleaned-event index. An insert correction must use index -1 and must be supported by a stable pYIN pitch segment in a genuine gap. Omit unchanged notes. Large pitch or octave changes are allowed only when the local measured pitch supports the proposed concert pitch. Long-note deletion is allowed only when local evidence shows weak or absent horn support. Use concert pitch in corrections. Select key/mode/BPM/meter for the uploaded recording; do not confuse the written transposed sax key with concert key.

Song: {json.dumps({'title': title, 'artist': artist})}
Original-mix analysis: {json.dumps(facts.to_dict(), separators=(',', ':'))}
Web research: {research_text}
Audio comparisons: {json.dumps(audio_comparison, separators=(',', ':'))}
Raw transcription MIDI events: {json.dumps(raw_events, separators=(',', ':'))}
Deterministic cleaned MIDI events: {json.dumps(cleaned_events, separators=(',', ':'))}
Local original/stem/note comparison: {json.dumps(transcription_evidence, separators=(',', ':'))}
Draft MusicXML (exact text):
{xml_text}
"""
    try:
        response = client.responses.create(
            model=model,
            input=prompt,
            text={"format": {"type": "json_schema", "name": "sax_review", "strict": True, "schema": SCHEMA}},
        )
        output_text = (getattr(response, "output_text", None) or "").strip()
        if not output_text:
            raise ValueError("The structured review returned no text.")
        return json.loads(output_text)
    except Exception as exc:
        raise AIReviewError("structured MIDI/MusicXML synthesis", exc) from exc


def _make_review_wav(source: Path, target: Path, start: float, duration: float) -> None:
    completed = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(source),
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        timeout=180,
    )
    if completed.returncode != 0 or not target.exists():
        raise AIReviewError("audio preparation", completed.stdout[-1200:] or "FFmpeg produced no review clip.")


def _encoded(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _extract_citations(response: Any) -> list[dict]:
    payload = response.model_dump() if hasattr(response, "model_dump") else response
    sources: list[dict] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") == "url_citation":
                citation = value.get("url_citation") if isinstance(value.get("url_citation"), dict) else value
                url = citation.get("url")
                if url:
                    sources.append({"title": citation.get("title") or url, "url": url})
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    unique: list[dict] = []
    seen: set[str] = set()
    for item in sources:
        if item["url"] not in seen:
            seen.add(item["url"])
            unique.append(item)
    return unique


def _validate_result(data: dict, events: list[dict]) -> None:
    valid_indices = {int(item["index"]) for item in events}
    for correction in data.get("corrections", []):
        action = correction.get("action")
        if action == "insert" and int(correction["index"]) != -1:
            raise AIReviewError("result validation", "Insert corrections must use index -1.")
        if action != "insert" and int(correction["index"]) not in valid_indices:
            raise AIReviewError("result validation", f"Correction references unknown event index {correction['index']}.")
        if not 0.0 <= float(correction["confidence"]) <= 1.0:
            raise AIReviewError("result validation", "Correction confidence is outside 0–1.")


def _error_detail(error: Exception | str) -> str:
    if isinstance(error, str):
        detail = error
    else:
        status = getattr(error, "status_code", None)
        request_id = getattr(error, "request_id", None)
        detail = str(error) or error.__class__.__name__
        prefix = " / ".join(item for item in [f"HTTP {status}" if status else "", f"request {request_id}" if request_id else ""] if item)
        if prefix:
            detail = f"{prefix}: {detail}"
    key = os.getenv("OPENAI_API_KEY")
    if key:
        detail = detail.replace(key, "[redacted]")
    return detail[:1800]


def _note_evidence(transcription_evidence: dict, index: int) -> dict | None:
    return next(
        (item for item in transcription_evidence.get("notes", []) if int(item.get("index", -999)) == index),
        None,
    )


def _pitch_is_supported(
    pitch: int,
    start: float,
    duration: float,
    transcription_evidence: dict,
    event_index: int | None = None,
) -> bool:
    end = start + duration
    for segment in transcription_evidence.get("stable_pitch_segments", []):
        overlap = max(0.0, min(end, float(segment.get("end_beat", -1))) - max(start, float(segment.get("start_beat", -1))))
        segment_duration = max(0.0, float(segment.get("end_beat", 0)) - float(segment.get("start_beat", 0)))
        if (
            abs(float(segment.get("measured_median_pitch", -99)) - pitch) <= 0.70
            and float(segment.get("mean_voiced_probability", 0)) >= 0.55
            and overlap >= min(0.12, max(0.06, min(duration, segment_duration) * 0.35))
        ):
            return True
    if event_index is None:
        return False
    item = _note_evidence(transcription_evidence, event_index)
    return bool(
        item
        and item.get("measured_median_pitch") is not None
        and abs(float(item["measured_median_pitch"]) - pitch) <= 0.70
        and float(item.get("mean_voiced_probability", 0)) >= 0.55
        and float(item.get("voiced_frame_fraction", 0)) >= 0.45
    )


def _long_delete_is_supported(event: dict, transcription_evidence: dict) -> bool:
    item = _note_evidence(transcription_evidence, int(event["index"]))
    if not item:
        return False
    probability = float(item.get("mean_voiced_probability", 0) or 0)
    coverage = float(item.get("voiced_frame_fraction", 0) or 0)
    energy = item.get("relative_stem_energy")
    no_stable_pitch = item.get("measured_median_pitch") is None
    weak_energy = energy is not None and float(energy) < 0.18
    return (no_stable_pitch and probability < 0.35 and coverage < 0.35) or (
        weak_energy and probability < 0.50 and coverage < 0.50
    )


def apply_safe_corrections(
    events: list[dict],
    corrections: list[dict],
    transcription_evidence: dict | None = None,
    return_audit: bool = False,
) -> list[dict] | tuple[list[dict], list[dict]]:
    evidence = transcription_evidence or {}
    audit: list[dict] = []
    for item in corrections:
        if float(item.get("confidence", 0)) < 0.80:
            audit.append({"correction": dict(item), "status": "rejected", "reason": "AI confidence is below 0.80."})
    accepted = [item for item in corrections if float(item.get("confidence", 0)) >= 0.80]
    by_index = {
        int(item["index"]): item
        for item in accepted
        if item.get("action") in {"change", "delete"}
    }
    result: list[dict] = []
    for event in events:
        correction = by_index.get(int(event["index"]))
        if not correction:
            result.append(dict(event))
            continue
        if correction["action"] == "delete":
            if event["duration_beats"] <= 0.25 or _long_delete_is_supported(event, evidence):
                audit.append({"correction": dict(correction), "status": "applied", "reason": "Deletion passed local audio guards."})
                continue
            audit.append({"correction": dict(correction), "status": "rejected", "reason": "A long note cannot be deleted without weak or absent horn evidence."})
            result.append(dict(event))
            continue
        pitch = int(correction["pitch"])
        start = float(correction["start_beat"])
        duration = float(correction["duration_beats"])
        large_pitch_change = abs(pitch - int(event["pitch"])) > 2
        if (
            (large_pitch_change and not _pitch_is_supported(
                pitch,
                start,
                duration,
                evidence,
                event_index=int(event["index"]),
            ))
            or abs(start - float(event["start_beat"])) > 0.25
            or abs(duration - float(event["duration_beats"])) > 1.0
        ):
            audit.append({"correction": dict(correction), "status": "rejected", "reason": "The proposed pitch or timing is not supported by local guards."})
            result.append(dict(event))
            continue
        changed = dict(event)
        changed.update(
            pitch=pitch,
            start_beat=max(0.0, start),
            duration_beats=max(0.125, duration),
            ai_correction={"action": "change", "reason": correction.get("reason", "")},
        )
        audit.append({"correction": dict(correction), "status": "applied", "reason": "Change passed local pitch and timing guards."})
        result.append(changed)

    for correction in accepted:
        if correction.get("action") != "insert" or float(correction.get("confidence", 0)) < 0.85:
            if correction.get("action") == "insert":
                audit.append({"correction": dict(correction), "status": "rejected", "reason": "Insertions require AI confidence of at least 0.85."})
            continue
        pitch = int(correction["pitch"])
        start = float(correction["start_beat"])
        duration = float(correction["duration_beats"])
        if not (24 <= pitch <= 108 and start >= 0 and 0.125 <= duration <= 4.0):
            audit.append({"correction": dict(correction), "status": "rejected", "reason": "Inserted note pitch, start, or duration is outside safe bounds."})
            continue
        if not _pitch_is_supported(pitch, start, duration, evidence):
            audit.append({"correction": dict(correction), "status": "rejected", "reason": "No stable pYIN span supports the inserted note."})
            continue
        end = start + duration
        overlaps = any(
            min(end, float(event["start_beat"]) + float(event["duration_beats"]))
            - max(start, float(event["start_beat"])) > 0.05
            for event in result
        )
        if overlaps:
            audit.append({"correction": dict(correction), "status": "rejected", "reason": "The proposed insertion overlaps an accepted monophonic note."})
            continue
        result.append(
            {
                "index": -1,
                "pitch": pitch,
                "start_beat": start,
                "duration_beats": duration,
                "velocity": 84,
                "rhythm_grid": events[0].get("rhythm_grid", "straight-sixteenth") if events else "straight-sixteenth",
                "rhythm_grid_beats": events[0].get("rhythm_grid_beats", 0.25) if events else 0.25,
                "pickup_offset_beats": events[0].get("pickup_offset_beats", 0.0) if events else 0.0,
                "ai_correction": {"action": "insert", "reason": correction.get("reason", "")},
            }
        )
        audit.append({"correction": dict(correction), "status": "applied", "reason": "Insertion passed stable-pitch and gap guards."})
    result = sorted(result, key=lambda item: item["start_beat"])
    for index, event in enumerate(result):
        event["index"] = index
    return (result, audit) if return_audit else result
