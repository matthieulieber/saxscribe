from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

from .simplify import simplify_events


MATCH_ONSET_WINDOW_BEATS = 0.85
MATCH_PITCH_TOLERANCE = 0.80
UNMATCHED_COST = 0.98


def _segment_onset(segment: dict) -> float:
    return float(segment.get("onset_beat", segment.get("start_beat", 0.0)))


def _overlap_ratio(event: dict, segment: dict) -> float:
    event_start = float(event["start_beat"])
    event_end = event_start + float(event["duration_beats"])
    segment_start = float(segment.get("start_beat", _segment_onset(segment)))
    segment_end = float(segment.get("end_beat", segment_start))
    overlap = max(0.0, min(event_end, segment_end) - max(event_start, segment_start))
    union = max(event_end, segment_end) - min(event_start, segment_start)
    return overlap / union if union > 1e-9 else 0.0


def match_candidate_to_audio(
    events: list[dict],
    transcription_evidence: dict,
    onset_window_beats: float = MATCH_ONSET_WINDOW_BEATS,
    pitch_tolerance: float = MATCH_PITCH_TOLERANCE,
) -> dict:
    """Globally assign score notes to measured pitch spans.

    A Hungarian assignment prevents two nearby score notes from claiming the
    same measured horn event. This follows the context-aware assignment
    principle from onset-snapping research, while using SaxScribe's local pYIN
    spans and onset peaks as the external audio anchor.
    """
    segments = [
        item
        for item in transcription_evidence.get("stable_pitch_segments", [])
        if item.get("measured_median_pitch") is not None
        and float(item.get("mean_voiced_probability", 0.0)) >= 0.45
        and float(item.get("end_beat", 0.0)) > float(item.get("start_beat", 0.0))
    ]
    if not events or not segments:
        return {
            "matches": [],
            "unmatched_event_indices": [int(item.get("index", i)) for i, item in enumerate(events)],
            "unmatched_segment_indices": list(range(len(segments))),
            "eligible_segment_count": len(segments),
        }

    event_count = len(events)
    segment_count = len(segments)
    event_groups: dict[int, list[int]] = {}
    segment_groups: dict[int, list[int]] = {}
    for position, event in enumerate(events):
        event_groups.setdefault(int(event["pitch"]), []).append(position)
    for position, segment in enumerate(segments):
        segment_groups.setdefault(int(round(float(segment["measured_median_pitch"]))), []).append(position)

    matches: list[dict] = []
    matched_events: set[int] = set()
    matched_segments: set[int] = set()
    for pitch, event_positions in event_groups.items():
        segment_positions = segment_groups.get(pitch, [])
        if not segment_positions:
            continue
        local_event_count = len(event_positions)
        local_segment_count = len(segment_positions)
        # One private dummy column per event lets the optimal assignment leave
        # any unsupported note unmatched instead of forcing a bad local match.
        costs = np.full(
            (local_event_count, local_segment_count + local_event_count),
            UNMATCHED_COST,
            dtype=float,
        )
        for local_event, event_position in enumerate(event_positions):
            event = events[event_position]
            event_pitch = float(event["pitch"])
            event_start = float(event["start_beat"])
            for local_segment, segment_position in enumerate(segment_positions):
                segment = segments[segment_position]
                pitch_error = abs(float(segment["measured_median_pitch"]) - event_pitch)
                onset_error = abs(_segment_onset(segment) - event_start)
                if pitch_error > pitch_tolerance or onset_error > onset_window_beats:
                    costs[local_event, local_segment] = 10.0
                    continue
                overlap = _overlap_ratio(event, segment)
                costs[local_event, local_segment] = (
                    0.52 * (onset_error / onset_window_beats)
                    + 0.30 * (pitch_error / pitch_tolerance)
                    + 0.18 * (1.0 - overlap)
                )
            costs[local_event, local_segment_count:] += np.arange(local_event_count) * 1e-7
            costs[local_event, local_segment_count + local_event] -= 1e-6

        rows, columns = linear_sum_assignment(costs)
        for local_event, column in zip(rows.tolist(), columns.tolist()):
            if column >= local_segment_count or costs[local_event, column] >= UNMATCHED_COST:
                continue
            event_position = event_positions[local_event]
            segment_position = segment_positions[column]
            event = events[event_position]
            segment = segments[segment_position]
            onset_error = _segment_onset(segment) - float(event["start_beat"])
            pitch_error = float(segment["measured_median_pitch"]) - float(event["pitch"])
            matches.append(
                {
                    "event_position": event_position,
                    "event_index": int(event.get("index", event_position)),
                    "segment_index": segment_position,
                    "pitch_error_cents": round(pitch_error * 100.0, 2),
                    "onset_error_beats": round(onset_error, 4),
                    "overlap_ratio": round(_overlap_ratio(event, segment), 4),
                    "segment": segment,
                }
            )
            matched_events.add(event_position)
            matched_segments.add(segment_position)

    return {
        "matches": sorted(matches, key=lambda item: item["event_position"]),
        "unmatched_event_indices": [
            int(event.get("index", position))
            for position, event in enumerate(events)
            if position not in matched_events
        ],
        "unmatched_segment_indices": [
            position for position in range(segment_count) if position not in matched_segments
        ],
        "eligible_segment_count": segment_count,
    }


def _complexity(events: list[dict], beats_per_bar: float = 4.0) -> dict:
    if not events:
        return {
            "score": 0.0,
            "notes_per_bar": 0.0,
            "short_note_fraction": 0.0,
            "off_eighth_fraction": 0.0,
            "triplet_fraction": 0.0,
            "micro_gap_fraction": 0.0,
        }
    ordered = sorted(events, key=lambda item: float(item["start_beat"]))
    end_beat = max(
        float(item["start_beat"]) + float(item["duration_beats"])
        for item in ordered
    )
    bars = max(1.0, math.ceil(end_beat / max(beats_per_bar, 1.0)))
    notes_per_bar = len(ordered) / bars
    density_component = min(1.0, max(0.0, notes_per_bar - 4.0) / 6.0)
    short_fraction = sum(float(item["duration_beats"]) < 0.49 for item in ordered) / len(ordered)

    def on_eighth_grid(value: float) -> bool:
        return abs(value * 2.0 - round(value * 2.0)) <= 0.045

    off_eighth_fraction = sum(
        not on_eighth_grid(float(item["start_beat"]))
        or not on_eighth_grid(float(item["duration_beats"]))
        for item in ordered
    ) / len(ordered)
    triplet_fraction = sum(
        "triplet" in str(item.get("rhythm_grid", ""))
        or (
            not on_eighth_grid(float(item["start_beat"]))
            and abs(float(item["start_beat"]) * 3.0 - round(float(item["start_beat"]) * 3.0)) <= 0.05
        )
        for item in ordered
    ) / len(ordered)
    micro_gaps = 0
    for current, following in zip(ordered, ordered[1:]):
        gap = float(following["start_beat"]) - (
            float(current["start_beat"]) + float(current["duration_beats"])
        )
        if 0.0 < gap < 0.49:
            micro_gaps += 1
    micro_gap_fraction = micro_gaps / max(1, len(ordered) - 1)
    score = 100.0 * (
        0.30 * density_component
        + 0.25 * short_fraction
        + 0.22 * off_eighth_fraction
        + 0.15 * triplet_fraction
        + 0.08 * micro_gap_fraction
    )
    return {
        "score": round(score, 3),
        "notes_per_bar": round(notes_per_bar, 3),
        "short_note_fraction": round(short_fraction, 4),
        "off_eighth_fraction": round(off_eighth_fraction, 4),
        "triplet_fraction": round(triplet_fraction, 4),
        "micro_gap_fraction": round(micro_gap_fraction, 4),
    }


def evaluate_candidate(events: list[dict], transcription_evidence: dict) -> dict:
    assignment = match_candidate_to_audio(events, transcription_evidence)
    matches = assignment["matches"]
    candidate_count = len(events)
    segment_count = int(assignment["eligible_segment_count"])
    matched_count = len(matches)
    precision = matched_count / candidate_count if candidate_count else 0.0
    recall = matched_count / segment_count if segment_count else 0.0
    if matches:
        pitch = sum(max(0.0, 1.0 - abs(item["pitch_error_cents"]) / 80.0) for item in matches) / matched_count
        onset = sum(max(0.0, 1.0 - abs(item["onset_error_beats"]) / MATCH_ONSET_WINDOW_BEATS) for item in matches) / matched_count
        overlap = sum(float(item["overlap_ratio"]) for item in matches) / matched_count
    else:
        pitch = onset = overlap = 0.0
    fidelity = 100.0 * (
        0.30 * precision
        + 0.15 * recall
        + 0.25 * pitch
        + 0.20 * onset
        + 0.10 * overlap
    )
    complexity = _complexity(events)
    # Lead-sheet usefulness is deliberately not pure reconstruction fidelity.
    # A modest complexity penalty prevents refinement from restoring every
    # ornamental fragment and timing fluctuation in the performance.
    objective = fidelity - 0.30 * float(complexity["score"])

    mismatches: list[dict] = []
    by_event = {int(item["event_index"]): item for item in matches}
    for position, event in enumerate(events):
        index = int(event.get("index", position))
        match = by_event.get(index)
        if match is None:
            mismatches.append(
                {
                    "kind": "unsupported_score_note",
                    "event_index": index,
                    "pitch": int(event["pitch"]),
                    "start_beat": round(float(event["start_beat"]), 4),
                    "severity": 1.0,
                }
            )
            continue
        if abs(float(match["pitch_error_cents"])) > 35:
            mismatches.append(
                {
                    "kind": "pitch_mismatch",
                    "event_index": index,
                    "pitch": int(event["pitch"]),
                    "start_beat": round(float(event["start_beat"]), 4),
                    "error_cents": match["pitch_error_cents"],
                    "severity": min(1.0, abs(float(match["pitch_error_cents"])) / 100.0),
                }
            )
        if abs(float(match["onset_error_beats"])) > 0.20:
            mismatches.append(
                {
                    "kind": "onset_mismatch",
                    "event_index": index,
                    "pitch": int(event["pitch"]),
                    "start_beat": round(float(event["start_beat"]), 4),
                    "error_beats": match["onset_error_beats"],
                    "severity": min(1.0, abs(float(match["onset_error_beats"])) / MATCH_ONSET_WINDOW_BEATS),
                }
            )
        if float(match["overlap_ratio"]) < 0.45:
            mismatches.append(
                {
                    "kind": "duration_mismatch",
                    "event_index": index,
                    "pitch": int(event["pitch"]),
                    "start_beat": round(float(event["start_beat"]), 4),
                    "overlap_ratio": match["overlap_ratio"],
                    "severity": 1.0 - float(match["overlap_ratio"]),
                }
            )
    segments = [
        item
        for item in transcription_evidence.get("stable_pitch_segments", [])
        if item.get("measured_median_pitch") is not None
        and float(item.get("mean_voiced_probability", 0.0)) >= 0.45
        and float(item.get("end_beat", 0.0)) > float(item.get("start_beat", 0.0))
    ]
    for segment_index in assignment["unmatched_segment_indices"]:
        segment = segments[segment_index]
        mismatches.append(
            {
                "kind": "possible_missing_note",
                "measured_pitch": round(float(segment["measured_median_pitch"]), 3),
                "start_beat": round(_segment_onset(segment), 4),
                "end_beat": round(float(segment["end_beat"]), 4),
                "severity": round(float(segment.get("mean_voiced_probability", 0.5)), 3),
            }
        )
    mismatches.sort(key=lambda item: float(item.get("severity", 0.0)), reverse=True)
    minimum_anchor_matches = min(
        3,
        max(1 if candidate_count == 1 else 2, math.ceil(candidate_count * 0.35)),
    )
    reliable_audio_anchor = bool(
        candidate_count
        and segment_count
        and matched_count >= minimum_anchor_matches
        and precision >= 0.35
    )
    return {
        "objective_score": round(objective, 3),
        "fidelity_score": round(fidelity, 3),
        "complexity_score": complexity["score"],
        "reliable_audio_anchor": reliable_audio_anchor,
        "candidate_note_count": candidate_count,
        "matched_note_count": matched_count,
        "measured_segment_count": segment_count,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "pitch_score": round(pitch, 4),
        "onset_score": round(onset, 4),
        "duration_overlap_score": round(overlap, 4),
        "complexity": complexity,
        "mismatches": mismatches[:48],
    }


def accept_refinement(
    baseline: dict,
    proposal: dict,
    minimum_objective_gain: float = 0.75,
    maximum_complexity_increase: float = 1.5,
) -> tuple[bool, str]:
    if not baseline.get("reliable_audio_anchor") or not proposal.get("reliable_audio_anchor"):
        return False, "The local audio anchor is too weak to verify this revision."
    complexity_change = float(proposal["complexity_score"]) - float(baseline["complexity_score"])
    if complexity_change > maximum_complexity_increase:
        return False, "The revision made the lead sheet materially more complex."
    if float(proposal["fidelity_score"]) + 0.5 < float(baseline["fidelity_score"]):
        return False, "The revision reduced agreement with the measured horn line."
    gain = float(proposal["objective_score"]) - float(baseline["objective_score"])
    if gain < minimum_objective_gain:
        return False, "The revision did not produce a measurable improvement."
    return True, f"Objective score improved by {gain:.2f} points within the complexity budget."


def select_lead_sheet_candidate(
    events: list[dict],
    bpm: float,
    transcription_evidence: dict,
) -> tuple[list[dict], dict]:
    """Choose between detected and straight-grid rhythmic spellings."""
    adaptive = simplify_events(events, bpm=bpm, interpretation="adaptive")
    conservative = simplify_events(events, bpm=bpm, interpretation="conservative")
    adaptive_evaluation = evaluate_candidate(adaptive, transcription_evidence)
    conservative_evaluation = evaluate_candidate(conservative, transcription_evidence)

    # Rhythmic simplicity is never allowed to win merely by deleting supported
    # notes. Prefer the detected musical grid unless a straight-grid candidate
    # has measurably stronger agreement with the audio anchor.
    if (
        float(conservative_evaluation["fidelity_score"])
        >= float(adaptive_evaluation["fidelity_score"]) + 1.5
        and float(conservative_evaluation["objective_score"])
        > float(adaptive_evaluation["objective_score"])
    ):
        selected_name = "conservative"
        selected = conservative
        reason = "The straight-grid interpretation had measurably stronger audio agreement."
    else:
        selected_name = "adaptive"
        selected = adaptive
        reason = "The detected musical grid was retained because simplification did not improve audio agreement."
    return selected, {
        "selected": selected_name,
        "reason": reason,
        "adaptive": adaptive_evaluation,
        "conservative": conservative_evaluation,
    }


def evidence_for_candidate(events: list[dict], transcription_evidence: dict, bpm: float) -> dict:
    """Re-index local evidence for a simplified candidate before guard checks."""
    assignment = match_candidate_to_audio(events, transcription_evidence)
    matches = {int(item["event_index"]): item for item in assignment["matches"]}
    seconds_per_beat = 60.0 / max(float(bpm), 1.0)
    notes: list[dict] = []
    for position, event in enumerate(events):
        index = int(event.get("index", position))
        match = matches.get(index)
        if match is None:
            notes.append(
                {
                    "index": index,
                    "candidate_pitch": int(event["pitch"]),
                    "start_beat": float(event["start_beat"]),
                    "measured_median_pitch": None,
                    "mean_voiced_probability": 0.0,
                    "voiced_frame_fraction": 0.0,
                    "nearest_stem_onset_delta_seconds": None,
                    "relative_stem_energy": None,
                }
            )
            continue
        segment = match["segment"]
        notes.append(
            {
                "index": index,
                "candidate_pitch": int(event["pitch"]),
                "start_beat": float(event["start_beat"]),
                "measured_median_pitch": float(segment["measured_median_pitch"]),
                "mean_voiced_probability": float(segment.get("mean_voiced_probability", 0.0)),
                "voiced_frame_fraction": 1.0,
                "nearest_stem_onset_delta_seconds": round(
                    float(match["onset_error_beats"]) * seconds_per_beat, 4
                ),
                "relative_stem_energy": None,
            }
        )
    remapped = dict(transcription_evidence)
    remapped["notes"] = notes
    remapped["candidate_note_count"] = len(events)
    remapped["assignment"] = {
        key: value for key, value in assignment.items() if key != "matches"
    }
    return remapped


def synthesize_candidate_audio(
    events: list[dict],
    path: Path,
    bpm: float,
    duration_seconds: float | None = None,
    sample_rate: int = 16000,
) -> Path:
    """Render the exact symbolic candidate as a neutral comparison signal."""
    seconds_per_beat = 60.0 / max(float(bpm), 1.0)
    event_end = max(
        (
            (float(item["start_beat"]) + float(item["duration_beats"]))
            * seconds_per_beat
            for item in events
        ),
        default=0.0,
    )
    total_seconds = max(0.25, float(duration_seconds or 0.0), event_end + 0.15)
    audio = np.zeros(int(math.ceil(total_seconds * sample_rate)), dtype=np.float32)
    for event in events:
        start = max(0, int(round(float(event["start_beat"]) * seconds_per_beat * sample_rate)))
        duration = max(0.04, float(event["duration_beats"]) * seconds_per_beat)
        stop = min(len(audio), start + int(round(duration * sample_rate)))
        if stop <= start:
            continue
        count = stop - start
        time = np.arange(count, dtype=np.float32) / sample_rate
        frequency = 440.0 * (2.0 ** ((int(event["pitch"]) - 69) / 12.0))
        tone = (
            np.sin(2.0 * np.pi * frequency * time)
            + 0.28 * np.sin(2.0 * np.pi * frequency * 2.0 * time)
            + 0.10 * np.sin(2.0 * np.pi * frequency * 3.0 * time)
        )
        attack = max(1, min(count, int(0.018 * sample_rate)))
        release = max(1, min(count, int(0.045 * sample_rate)))
        envelope = np.ones(count, dtype=np.float32)
        envelope[:attack] = np.linspace(0.0, 1.0, attack, endpoint=True)
        envelope[-release:] *= np.linspace(1.0, 0.0, release, endpoint=True)
        velocity = max(1, min(127, int(event.get("velocity", 88)))) / 127.0
        audio[start:stop] += tone * envelope * (0.18 + 0.22 * velocity)
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    if peak > 0.92:
        audio *= 0.92 / peak
    pcm = np.asarray(np.clip(audio, -1.0, 1.0) * 32767.0, dtype="<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())
    return path
