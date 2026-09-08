from __future__ import annotations

import math
from statistics import median


STRAIGHT_QUANTA = (0.5, 0.25, 0.125, 0.0625)


def _nearest(value: float, quantum: float) -> float:
    return round(round(max(0.0, value) / quantum) * quantum, 6)


def _distance_to_grid(value: float, quantum: float) -> float:
    return abs(value - _nearest(value, quantum))


def _confidence_rank(item: dict) -> int:
    return {"low": 0, "medium": 1, "high": 2}.get(
        str(item.get("confidence_level", "high")), 1
    )


def _priority(item: dict) -> tuple:
    return (
        not bool(item.get("possible_non_horn", False)),
        _confidence_rank(item),
        float(item.get("confidence_score", 1.0)),
        float(item.get("performance_duration", 0.0)),
        int(item.get("velocity", 88)),
    )


def _supported(item: dict) -> bool:
    return (
        not bool(item.get("possible_non_horn", False))
        and str(item.get("confidence_level", "high")) != "low"
        and float(item.get("confidence_score", 1.0)) >= 0.45
    )


def _support_is_reliable(events: list[dict]) -> bool:
    if not events:
        return False
    supported_count = sum(_supported(event) for event in events)
    minimum_supported = min(
        3,
        max(1 if len(events) == 1 else 2, math.ceil(len(events) * 0.35)),
    )
    return (
        supported_count >= minimum_supported
        and supported_count / len(events) >= 0.35
    )


def _straight_grid_name(quantum: float) -> str:
    if quantum >= 0.5:
        return "straight-eighth"
    if quantum >= 0.25:
        return "straight-sixteenth"
    if quantum >= 0.125:
        return "straight-thirty-second"
    return "straight-sixty-fourth"


def _collision_safe_straight_quantum(events: list[dict]) -> float:
    """Use the coarsest standard grid that preserves supported attacks.

    Readability may change rhythmic spelling, but it must not silently turn
    two distinct, supported melody notes into one attack. Exact simultaneous
    detector events remain a monophony conflict and are resolved later.
    """
    use_supported_only = _support_is_reliable(events)
    starts = sorted(
        {
            round(float(event.get("performance_start_beat", event["start_beat"])), 6)
            for event in events
            if _supported(event) or not use_supported_only
        }
    )
    if len(starts) < 2:
        return STRAIGHT_QUANTA[0]
    for quantum in STRAIGHT_QUANTA:
        slots = [_nearest(value, quantum) for value in starts]
        if len(set(slots)) == len(starts):
            return quantum
    return STRAIGHT_QUANTA[-1]


def _straight_profile(events: list[dict], *, swing_marking: bool = False) -> dict:
    quantum = _collision_safe_straight_quantum(events)
    return {
        "quantum": quantum,
        "rhythm_grid": _straight_grid_name(quantum),
        "swing_marking": swing_marking,
        "preserve_syncopation": quantum < 0.5,
    }


def _choose_notation_profile(events: list[dict]) -> dict:
    """Choose the simplest grid still supported by raw performance timing."""
    source_grid = str(events[0].get("rhythm_grid", "straight-sixteenth"))
    supported_starts = [
        float(event.get("performance_start_beat", event["start_beat"]))
        for event in events
        if _supported(event)
    ]

    # Swing is conventionally written as straight eighths with a feel marking,
    # avoiding a page full of explicit triplet brackets.
    if source_grid == "swing-eighth":
        return _straight_profile(events, swing_marking=True)

    # Preserve true triplets only when several supported raw attacks fit thirds
    # materially better than eighths across more than one beat.
    if source_grid == "triplet-eighth" and len(supported_starts) >= 6:
        triplet_error = median(
            _distance_to_grid(value, 1.0 / 3.0) for value in supported_starts
        )
        eighth_error = median(
            _distance_to_grid(value, 0.5) for value in supported_starts
        )
        offbeat_thirds = [
            value
            for value in supported_starts
            if _distance_to_grid(value, 1.0 / 3.0) <= 0.08
            and _distance_to_grid(value, 0.5) >= 0.08
        ]
        required = max(4, math.ceil(len(supported_starts) * 0.3))
        triplet_cells: dict[int, set[int]] = {}
        for value in supported_starts:
            beat = int(math.floor(value + 1e-6))
            phase = value - beat
            nearest_phase = min(
                range(3), key=lambda slot: abs(phase - slot / 3.0)
            )
            if abs(phase - nearest_phase / 3.0) <= 0.08:
                triplet_cells.setdefault(beat, set()).add(nearest_phase)
        complete_triplet_beats = sum(
            phases == {0, 1, 2} for phases in triplet_cells.values()
        )
        if (
            len(offbeat_thirds) >= required
            and len({int(value) for value in offbeat_thirds}) >= 2
            and complete_triplet_beats >= 2
            and triplet_error + 0.02 < eighth_error * 0.72
        ):
            return {
                "quantum": 1.0 / 3.0,
                "rhythm_grid": "triplet-eighth",
                "swing_marking": False,
                "preserve_syncopation": False,
            }

    return _straight_profile(events)


def _merge_confidence(target: dict, source: dict) -> None:
    target["source_indices"].extend(source["source_indices"])
    target["possible_non_horn"] = target["possible_non_horn"] or source["possible_non_horn"]
    target["confidence_reasons"] = list(
        dict.fromkeys(target["confidence_reasons"] + source["confidence_reasons"])
    )
    target["confidence_flags"] = list(
        dict.fromkeys(target["confidence_flags"] + source["confidence_flags"])
    )
    if source["confidence_score"] < target["confidence_score"]:
        target["confidence_score"] = source["confidence_score"]
        target["confidence_level"] = source["confidence_level"]


def simplify_events(
    events: list[dict],
    bpm: float = 120.0,
    interpretation: str = "adaptive",
) -> list[dict]:
    """Normalize rhythmic spelling without deleting a supported melody."""
    if not math.isfinite(bpm) or bpm <= 0:
        raise ValueError("Tempo must be greater than zero for lead-sheet interpretation.")
    if interpretation not in {"adaptive", "conservative"}:
        raise ValueError("Lead-sheet interpretation must be adaptive or conservative.")
    if not events:
        return []

    detected_profile = _choose_notation_profile(events)
    profile = detected_profile if interpretation == "adaptive" else _straight_profile(
        events,
        swing_marking=bool(detected_profile["swing_marking"]),
    )
    quantum = float(profile["quantum"])
    allow_artifact_removal = _support_is_reliable(events)
    candidates: list[dict] = []
    for event in events:
        performance_start = max(
            0.0, float(event.get("performance_start_beat", event["start_beat"]))
        )
        performance_duration = max(
            0.0625,
            float(event.get("performance_duration_beats", event["duration_beats"])),
        )
        start = _nearest(performance_start, quantum)
        end = _nearest(performance_start + performance_duration, quantum)
        if end <= start:
            end = round(start + quantum, 6)
        candidates.append(
            {
                "pitch": int(event["pitch"]),
                "start_beat": start,
                "end_beat": end,
                "velocity": int(event.get("velocity", 88)),
                "performance_start": performance_start,
                "performance_duration": performance_duration,
                "confidence_score": float(event.get("confidence_score", 1.0)),
                "confidence_level": str(event.get("confidence_level", "high")),
                "confidence_reasons": list(event.get("confidence_reasons", [])),
                "confidence_flags": list(event.get("confidence_flags", [])),
                "possible_non_horn": bool(event.get("possible_non_horn", False)),
                "supported": _supported(event),
                "source_indices": [int(event.get("index", 0))],
                "source_rhythm_grid": str(event.get("rhythm_grid", "straight-sixteenth")),
                "pickup_offset_beats": float(event.get("pickup_offset_beats", 0.0)),
            }
        )

    by_start: dict[float, dict] = {}
    for candidate in candidates:
        existing = by_start.get(candidate["start_beat"])
        if existing is None or _priority(candidate) > _priority(existing):
            by_start[candidate["start_beat"]] = candidate
    slotted = sorted(by_start.values(), key=lambda item: item["start_beat"])

    def is_short_weak_artifact(item: dict) -> bool:
        return (
            allow_artifact_removal
            and not item["supported"]
            and item["performance_duration"] <= 0.35
            and (
                item["possible_non_horn"]
                or item["confidence_level"] == "low"
                or item["confidence_score"] < 0.30
            )
        )

    def joins_supported_repeat(item: dict) -> bool:
        return any(
            other is not item
            and other["pitch"] == item["pitch"]
            and not is_short_weak_artifact(other)
            and abs(other["start_beat"] - item["start_beat"]) <= quantum
            for other in slotted
        )

    reduced = [
        item
        for item in slotted
        if not is_short_weak_artifact(item) or joins_supported_repeat(item)
    ]
    if not reduced and slotted:
        # A weak evidence pass is not permission to erase the entire melody.
        reduced = [max(slotted, key=_priority)]

    ordered = sorted(reduced, key=lambda item: (item["start_beat"], item["pitch"]))
    merged: list[dict] = []
    for candidate in ordered:
        if (
            merged
            and candidate["pitch"] == merged[-1]["pitch"]
            and candidate["start_beat"] <= merged[-1]["end_beat"] + quantum / 2
            and (not candidate["supported"] or not merged[-1]["supported"])
        ):
            merged[-1]["end_beat"] = max(merged[-1]["end_beat"], candidate["end_beat"])
            merged[-1]["velocity"] = max(merged[-1]["velocity"], candidate["velocity"])
            _merge_confidence(merged[-1], candidate)
            merged[-1]["supported"] = merged[-1]["supported"] or candidate["supported"]
        else:
            merged.append(dict(candidate))

    for index in range(len(merged) - 1):
        next_start = merged[index + 1]["start_beat"]
        if merged[index]["end_beat"] > next_start:
            merged[index]["end_beat"] = next_start

    minimum_output_duration = quantum
    return [
        {
            "index": index,
            "pitch": item["pitch"],
            "start_beat": round(item["start_beat"], 4),
            "duration_beats": round(
                max(minimum_output_duration, item["end_beat"] - item["start_beat"]),
                4,
            ),
            "performance_start_beat": round(item["performance_start"], 4),
            "performance_duration_beats": round(item["performance_duration"], 4),
            "timing_adjustment_beats": round(item["start_beat"] - item["performance_start"], 4),
            "velocity": item["velocity"],
            "confidence_score": round(item["confidence_score"], 3),
            "confidence_level": item["confidence_level"],
            "confidence_reasons": item["confidence_reasons"],
            "confidence_flags": item["confidence_flags"],
            "possible_non_horn": item["possible_non_horn"],
            "source_indices": item["source_indices"],
            "rhythm_grid": profile["rhythm_grid"],
            "rhythm_grid_beats": round(quantum, 6),
            "swing_marking": bool(profile["swing_marking"]),
            "source_rhythm_grid": item["source_rhythm_grid"],
            "pickup_offset_beats": round(item["pickup_offset_beats"], 4),
        }
        for index, item in enumerate(merged)
    ]
