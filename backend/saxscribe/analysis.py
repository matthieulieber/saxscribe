from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import librosa
import numpy as np
import pretty_midi


MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
PITCH_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
SIMPLE_GRID_BEATS = 0.5
ADVANCED_GRID_BEATS = 0.25
MIDI_INSTRUMENT_NAMES = {
    "concert": "Tenor Sax",
    "soprano": "Soprano Sax",
    "tenor": "Tenor Sax",
    "alto": "Alto Sax",
    "baritone": "Baritone Sax",
}


@dataclass
class AudioFacts:
    duration_seconds: float
    bpm: float
    tuning_cents: float
    concert_key: str
    mode: str
    key_confidence: float
    key_candidates: list[dict] = field(default_factory=list)
    meter: str = "4/4"

    def to_dict(self) -> dict:
        return asdict(self)


def analyze_audio(path: Path) -> AudioFacts:
    y, sr = librosa.load(path, sr=22050, mono=True)
    if not np.any(np.abs(y) > 1e-7):
        raise ValueError("The recording contains no detectable audio.")

    duration = float(librosa.get_duration(y=y, sr=sr))
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr, trim=False)
    bpm = float(np.asarray(tempo).reshape(-1)[0]) if np.size(tempo) else 120.0
    if not np.isfinite(bpm) or bpm < 35:
        bpm = 120.0
    while bpm > 210:
        bpm /= 2

    harmonic = librosa.effects.harmonic(y)
    chroma = librosa.feature.chroma_cqt(y=harmonic, sr=sr)
    weights = np.nan_to_num(chroma.mean(axis=1), nan=0.0)
    weights /= weights.sum() or 1.0

    candidates: list[tuple[float, int, str]] = []
    for tonic in range(12):
        candidates.append((_correlation(weights, np.roll(MAJOR_PROFILE, tonic)), tonic, "major"))
        candidates.append((_correlation(weights, np.roll(MINOR_PROFILE, tonic)), tonic, "minor"))
    candidates.sort(reverse=True)
    best, second = candidates[0], candidates[1]
    confidence = float(max(0.0, min(1.0, (best[0] - second[0] + 0.08) / 0.25)))

    tuning = float(librosa.estimate_tuning(y=harmonic, sr=sr) * 100.0)
    if not np.isfinite(tuning):
        tuning = 0.0

    return AudioFacts(
        duration_seconds=round(duration, 3),
        bpm=round(bpm, 2),
        tuning_cents=round(tuning, 1),
        concert_key=PITCH_NAMES[best[1]],
        mode=best[2],
        key_confidence=round(confidence, 3),
        key_candidates=[
            {
                "concert_key": PITCH_NAMES[tonic],
                "mode": candidate_mode,
                "score": round(float(score), 4),
            }
            for score, tonic, candidate_mode in candidates[:5]
        ],
    )


def _correlation(a: np.ndarray, b: np.ndarray) -> float:
    if np.std(a) == 0 or np.std(b) == 0:
        return -1.0
    return float(np.corrcoef(a, b)[0, 1])


def _snap_rhythm(value: float, grid: str) -> float:
    value = max(0.0, float(value))
    if grid == "triplet-eighth":
        return round(round(value * 3.0) / 3.0, 6)
    if grid == "swing-eighth":
        beat = int(np.floor(value))
        candidates = (float(beat), beat + 2.0 / 3.0, float(beat + 1))
        return round(min(candidates, key=lambda item: abs(item - value)), 6)
    return round(round(value * 4.0) / 4.0, 6)


def infer_rhythm_profile(start_beats: list[float]) -> dict:
    """Choose straight, triplet, or swing subdivision from unquantized attacks.

    This is deliberately conservative: at least two attacks must favor a
    triplet subdivision over the straight sixteenth grid. A leading fractional
    offset is preserved as pickup timing, but is not claimed as a detected
    downbeat or anacrusis measure.
    """
    starts = [max(0.0, float(item)) for item in start_beats]
    if not starts:
        return {"grid": "straight-sixteenth", "grid_beats": 0.25, "pickup_offset_beats": 0.0}
    third_hits: list[float] = []
    for value in starts:
        phase = value % 1.0
        straight_error = min(abs(phase - candidate) for candidate in (0.0, 0.25, 0.5, 0.75, 1.0))
        third = min((1.0 / 3.0, 2.0 / 3.0), key=lambda candidate: abs(phase - candidate))
        if abs(phase - third) <= 0.11 and straight_error >= 0.07:
            third_hits.append(third)
    required = max(2, int(np.ceil(len(starts) * 0.2)))
    if len(third_hits) >= required:
        late_ratio = sum(abs(item - 2.0 / 3.0) < 0.05 for item in third_hits) / len(third_hits)
        grid = "swing-eighth" if late_ratio >= 0.65 else "triplet-eighth"
        grid_beats = round(1.0 / 3.0, 6)
    else:
        grid = "straight-sixteenth"
        grid_beats = 0.25
    first = _snap_rhythm(min(starts), grid)
    pickup = first if 0.0 < first < 1.0 else 0.0
    return {"grid": grid, "grid_beats": grid_beats, "pickup_offset_beats": round(pickup, 4)}


def _midi_program(selected_instrument: str) -> tuple[int, str]:
    name = MIDI_INSTRUMENT_NAMES.get(selected_instrument, "Tenor Sax")
    return pretty_midi.instrument_name_to_program(name), name


def clean_performance_midi(
    input_path: Path,
    output_path: Path,
    bpm: float,
    selected_instrument: str = "tenor",
) -> list[dict]:
    midi = pretty_midi.PrettyMIDI(str(input_path))
    notes = [n for inst in midi.instruments if not inst.is_drum for n in inst.notes]
    notes.sort(key=lambda n: (n.start, -n.velocity, -n.end))
    if not notes:
        raise ValueError("The sax transcriber produced no MIDI notes.")

    seconds_per_beat = 60.0 / max(bpm, 1.0)
    minimum = max(0.045, seconds_per_beat / 24)
    notes = [n for n in notes if n.end - n.start >= minimum]

    corrected: list[pretty_midi.Note] = []
    for original in notes:
        note = pretty_midi.Note(
            velocity=max(35, min(115, original.velocity)),
            pitch=max(24, min(108, original.pitch)),
            start=max(0.0, original.start),
            end=max(original.start + minimum, original.end),
        )
        if corrected:
            prev = corrected[-1]
            for shift in (0, -12, 12):
                candidate = note.pitch + shift
                if 24 <= candidate <= 108 and abs(candidate - prev.pitch) <= 7:
                    if abs(note.pitch - prev.pitch) > 12:
                        note.pitch = candidate
                    break
            if note.pitch == prev.pitch and note.start - prev.end <= 0.09:
                prev.end = max(prev.end, note.end)
                continue
            if note.start < prev.end:
                prev.end = max(prev.start + minimum, note.start)
                if prev.end - prev.start < minimum:
                    corrected.pop()
        corrected.append(note)

    profile = infer_rhythm_profile([note.start / seconds_per_beat for note in corrected])
    minimum_grid = float(profile["grid_beats"])
    for note in corrected:
        start_beat = _snap_rhythm(note.start / seconds_per_beat, profile["grid"])
        end_beat = _snap_rhythm(note.end / seconds_per_beat, profile["grid"])
        note.start = max(0.0, start_beat * seconds_per_beat)
        note.end = max(note.start + minimum_grid * seconds_per_beat, end_beat * seconds_per_beat)

    merged: list[pretty_midi.Note] = []
    for note in corrected:
        if merged and note.pitch == merged[-1].pitch and note.start <= merged[-1].end + minimum_grid * seconds_per_beat / 2:
            merged[-1].end = max(merged[-1].end, note.end)
        else:
            merged.append(note)

    result = pretty_midi.PrettyMIDI(initial_tempo=bpm)
    program, instrument_name = _midi_program(selected_instrument)
    instrument = pretty_midi.Instrument(program=program, name=instrument_name)
    instrument.notes.extend(merged)
    result.instruments.append(instrument)
    result.write(str(output_path))

    return [
        {
            "index": index,
            "pitch": note.pitch,
            "start_beat": round(note.start / seconds_per_beat, 4),
            "duration_beats": round((note.end - note.start) / seconds_per_beat, 4),
            "velocity": note.velocity,
            "rhythm_grid": profile["grid"],
            "rhythm_grid_beats": profile["grid_beats"],
            "pickup_offset_beats": profile["pickup_offset_beats"],
        }
        for index, note in enumerate(merged)
    ]


def write_events_midi(
    events: list[dict],
    path: Path,
    bpm: float,
    selected_instrument: str = "tenor",
) -> None:
    seconds_per_beat = 60.0 / max(bpm, 1.0)
    midi = pretty_midi.PrettyMIDI(initial_tempo=bpm)
    program, instrument_name = _midi_program(selected_instrument)
    instrument = pretty_midi.Instrument(program=program, name=instrument_name)
    for event in events:
        start = max(0.0, float(event["start_beat"]) * seconds_per_beat)
        duration = max(0.0625, float(event["duration_beats"])) * seconds_per_beat
        instrument.notes.append(
            pretty_midi.Note(
                velocity=int(event.get("velocity", 88)),
                pitch=int(event["pitch"]),
                start=start,
                end=start + duration,
            )
        )
    midi.instruments.append(instrument)
    midi.write(str(path))


def simplify_events(events: list[dict], grid_beats: float = SIMPLE_GRID_BEATS) -> list[dict]:
    """Create a conservative, monophonic notation version on a coarser grid.

    The advanced event list already carries its selected straight, swing, or
    triplet grid. This pass does not invent or transpose pitches: it uses a
    coarse eighth-note grid for straight passages, preserves third-beat slots
    for swing/triplet passages, resolves tiny collisions, and joins adjacent
    repetitions of the same pitch.
    """
    if grid_beats <= 0:
        raise ValueError("The simplification grid must be greater than zero.")
    if not events:
        return []

    source_grid = str(events[0].get("rhythm_grid", "straight-sixteenth"))
    expressive_grid = source_grid if source_grid in {"swing-eighth", "triplet-eighth"} else None
    effective_grid = 1.0 / 3.0 if expressive_grid else grid_beats

    def snap(value: float) -> float:
        if expressive_grid:
            return _snap_rhythm(value, expressive_grid)
        ticks = int(max(0.0, value) / grid_beats + 0.5)
        return round(ticks * grid_beats, 6)

    candidates: list[dict] = []
    for event in events:
        original_start = max(0.0, float(event["start_beat"]))
        original_duration = max(0.0625, float(event["duration_beats"]))
        start = snap(original_start)
        end = snap(original_start + original_duration)
        if end <= start:
            end = round(start + effective_grid, 6)
        candidates.append(
            {
                "pitch": int(event["pitch"]),
                "start_beat": start,
                "end_beat": end,
                "velocity": int(event.get("velocity", 88)),
                "original_duration": original_duration,
                "confidence_score": float(event.get("confidence_score", 1.0)),
                "confidence_level": event.get("confidence_level", "high"),
                "confidence_reasons": list(event.get("confidence_reasons", [])),
                "confidence_flags": list(event.get("confidence_flags", [])),
                "possible_non_horn": bool(event.get("possible_non_horn", False)),
                "source_indices": [int(event.get("index", 0))],
                "rhythm_grid": source_grid,
                "rhythm_grid_beats": float(event.get("rhythm_grid_beats", effective_grid)),
                "pickup_offset_beats": float(event.get("pickup_offset_beats", 0.0)),
            }
        )

    # A scoop or detector fragment can land on the same simplified beat as the
    # principal note. Prefer the longer event instead of creating a chord.
    by_start: dict[float, dict] = {}
    for candidate in candidates:
        existing = by_start.get(candidate["start_beat"])
        if existing is None or (
            candidate["original_duration"], candidate["velocity"]
        ) > (
            existing["original_duration"], existing["velocity"]
        ):
            by_start[candidate["start_beat"]] = candidate

    ordered = sorted(by_start.values(), key=lambda item: (item["start_beat"], item["pitch"]))
    merged: list[dict] = []
    for candidate in ordered:
        if (
            merged
            and candidate["pitch"] == merged[-1]["pitch"]
            and candidate["start_beat"] <= merged[-1]["end_beat"] + effective_grid / 2
        ):
            merged[-1]["end_beat"] = max(merged[-1]["end_beat"], candidate["end_beat"])
            merged[-1]["velocity"] = max(merged[-1]["velocity"], candidate["velocity"])
            merged[-1]["source_indices"].extend(candidate["source_indices"])
            merged[-1]["possible_non_horn"] = (
                merged[-1]["possible_non_horn"] or candidate["possible_non_horn"]
            )
            merged[-1]["confidence_reasons"] = list(dict.fromkeys(
                merged[-1]["confidence_reasons"] + candidate["confidence_reasons"]
            ))
            merged[-1]["confidence_flags"] = list(dict.fromkeys(
                merged[-1]["confidence_flags"] + candidate["confidence_flags"]
            ))
            if candidate["confidence_score"] < merged[-1]["confidence_score"]:
                merged[-1]["confidence_score"] = candidate["confidence_score"]
                merged[-1]["confidence_level"] = candidate["confidence_level"]
        else:
            merged.append(dict(candidate))

    # Keep one playable sax note at a time. Unique starts are at least one grid
    # unit apart, so shortening an overlap never creates a microscopic note.
    for index in range(len(merged) - 1):
        next_start = merged[index + 1]["start_beat"]
        if merged[index]["end_beat"] > next_start:
            merged[index]["end_beat"] = next_start

    return [
        {
            "index": index,
            "pitch": item["pitch"],
            "start_beat": round(item["start_beat"], 4),
            "duration_beats": round(max(effective_grid, item["end_beat"] - item["start_beat"]), 4),
            "velocity": item["velocity"],
            "confidence_score": round(item["confidence_score"], 3),
            "confidence_level": item["confidence_level"],
            "confidence_reasons": item["confidence_reasons"],
            "confidence_flags": item["confidence_flags"],
            "possible_non_horn": item["possible_non_horn"],
            "source_indices": item["source_indices"],
            "rhythm_grid": item["rhythm_grid"],
            "rhythm_grid_beats": round(item["rhythm_grid_beats"], 6),
            "pickup_offset_beats": round(item["pickup_offset_beats"], 4),
        }
        for index, item in enumerate(merged)
    ]


def read_midi_events(path: Path, bpm: float) -> list[dict]:
    """Return a stable, JSON-safe view of a performance MIDI file."""
    midi = pretty_midi.PrettyMIDI(str(path))
    seconds_per_beat = 60.0 / max(float(bpm), 1.0)
    notes = [note for item in midi.instruments if not item.is_drum for note in item.notes]
    notes.sort(key=lambda note: (note.start, note.pitch, note.end))
    return [
        {
            "index": index,
            "pitch": int(item.pitch),
            "start_seconds": round(float(item.start), 4),
            "end_seconds": round(float(item.end), 4),
            "start_beat": round(float(item.start) / seconds_per_beat, 4),
            "duration_beats": round(float(item.end - item.start) / seconds_per_beat, 4),
            "velocity": int(item.velocity),
        }
        for index, item in enumerate(notes)
    ]


def _compress_pyin_segments(
    f0: np.ndarray,
    voiced_flag: np.ndarray,
    voiced_probability: np.ndarray,
    frame_times: np.ndarray,
    seconds_per_beat: float,
) -> list[dict]:
    """Compress frame-level pYIN output into stable spans usable as guards."""
    midi = librosa.hz_to_midi(f0)
    active = np.isfinite(midi) & np.asarray(voiced_flag, dtype=bool) & (np.nan_to_num(voiced_probability) >= 0.45)
    groups: list[list[int]] = []
    current: list[int] = []
    for index in np.flatnonzero(active):
        if not current:
            current = [int(index)]
            continue
        contiguous = int(index) <= current[-1] + 2
        stable = abs(float(midi[index]) - float(np.nanmedian(midi[current]))) <= 0.85
        if contiguous and stable:
            current.append(int(index))
        else:
            groups.append(current)
            current = [int(index)]
    if current:
        groups.append(current)

    frame_step = float(np.median(np.diff(frame_times))) if len(frame_times) > 1 else 0.012
    segments: list[dict] = []
    for group in groups:
        if len(group) < 3:
            continue
        start = float(frame_times[group[0]])
        end = float(frame_times[group[-1]] + frame_step)
        if end - start < 0.055:
            continue
        segments.append(
            {
                "start_seconds": round(start, 4),
                "end_seconds": round(end, 4),
                "start_beat": round(start / seconds_per_beat, 4),
                "end_beat": round(end / seconds_per_beat, 4),
                "measured_median_pitch": round(float(np.nanmedian(midi[group])), 3),
                "mean_voiced_probability": round(float(np.nanmean(voiced_probability[group])), 3),
                "frame_count": len(group),
            }
        )
    return segments


def build_transcription_evidence(
    original_path: Path,
    wind_path: Path,
    events: list[dict],
    bpm: float,
) -> dict:
    """Compare candidate MIDI notes with the original mix and isolated horn.

    The LLM receives these measurements as its note-level authority. The audio-model
    call adds a qualitative second opinion, but does not replace pitch tracking.
    """
    sample_rate = 22050
    hop_length = 256
    original, _ = librosa.load(original_path, sr=sample_rate, mono=True)
    wind, _ = librosa.load(wind_path, sr=sample_rate, mono=True)
    if not np.any(np.abs(wind) > 1e-7):
        raise ValueError("The isolated horn recording contains no detectable audio.")

    f0, voiced_flag, voiced_probability = librosa.pyin(
        wind,
        fmin=librosa.note_to_hz("C2"),
        fmax=librosa.note_to_hz("C7"),
        sr=sample_rate,
        frame_length=2048,
        hop_length=hop_length,
    )
    frame_times = librosa.times_like(f0, sr=sample_rate, hop_length=hop_length)
    stem_onsets = librosa.onset.onset_detect(
        y=wind,
        sr=sample_rate,
        hop_length=hop_length,
        units="time",
        backtrack=True,
    )
    mix_onsets = librosa.onset.onset_detect(
        y=original,
        sr=sample_rate,
        hop_length=hop_length,
        units="time",
        backtrack=True,
    )

    seconds_per_beat = 60.0 / max(float(bpm), 1.0)
    pitch_segments = _compress_pyin_segments(
        f0,
        voiced_flag,
        voiced_probability,
        frame_times,
        seconds_per_beat,
    )
    note_evidence: list[dict] = []
    for event in events:
        start = float(event["start_beat"]) * seconds_per_beat
        end = start + float(event["duration_beats"]) * seconds_per_beat
        mask = (frame_times >= start) & (frame_times < end)
        event_f0 = f0[mask]
        event_voiced = voiced_flag[mask]
        event_probability = voiced_probability[mask]
        valid = np.isfinite(event_f0)
        measured_midi = float(np.nanmedian(librosa.hz_to_midi(event_f0[valid]))) if np.any(valid) else None
        pitch_error = measured_midi - int(event["pitch"]) if measured_midi is not None else None
        sample_start = max(0, int(start * sample_rate))
        original_end = min(len(original), int(end * sample_rate))
        wind_end = min(len(wind), int(end * sample_rate))
        original_window = original[sample_start:original_end]
        wind_window = wind[sample_start:wind_end]
        original_window_rms = float(np.sqrt(np.mean(np.square(original_window)))) if len(original_window) else 0.0
        wind_window_rms = float(np.sqrt(np.mean(np.square(wind_window)))) if len(wind_window) else 0.0
        stem_to_mix_ratio = wind_window_rms / max(original_window_rms, 1e-8)

        def nearest_delta(onsets: np.ndarray) -> float | None:
            if not len(onsets):
                return None
            return float(onsets[int(np.argmin(np.abs(onsets - start)))] - start)

        note_evidence.append(
            {
                "index": int(event["index"]),
                "candidate_pitch": int(event["pitch"]),
                "start_seconds": round(start, 4),
                "end_seconds": round(end, 4),
                "start_beat": round(float(event["start_beat"]), 4),
                "duration_beats": round(float(event["duration_beats"]), 4),
                "measured_median_pitch": round(measured_midi, 3) if measured_midi is not None else None,
                "pitch_error_cents": round(pitch_error * 100.0, 1) if pitch_error is not None else None,
                "voiced_frame_fraction": round(float(np.mean(event_voiced)), 3) if np.any(mask) else 0.0,
                "mean_voiced_probability": round(float(np.nanmean(event_probability)), 3) if np.any(mask) else 0.0,
                "nearest_stem_onset_delta_seconds": round(nearest_delta(stem_onsets), 4) if len(stem_onsets) else None,
                "nearest_mix_onset_delta_seconds": round(nearest_delta(mix_onsets), 4) if len(mix_onsets) else None,
                "stem_to_mix_rms_ratio": round(stem_to_mix_ratio, 5),
            }
        )

    positive_energy_ratios = [
        float(item["stem_to_mix_rms_ratio"])
        for item in note_evidence
        if float(item["stem_to_mix_rms_ratio"]) > 1e-6
    ]
    reference_energy_ratio = float(np.median(positive_energy_ratios)) if positive_energy_ratios else None
    for item in note_evidence:
        ratio = float(item["stem_to_mix_rms_ratio"])
        item["relative_stem_energy"] = (
            round(ratio / reference_energy_ratio, 4)
            if reference_energy_ratio and reference_energy_ratio > 1e-8
            else None
        )

    common = min(len(original), len(wind))
    envelope_correlation = None
    if common:
        original_rms = librosa.feature.rms(y=original[:common], hop_length=512).reshape(-1)
        wind_rms = librosa.feature.rms(y=wind[:common], hop_length=512).reshape(-1)
        envelope_size = min(len(original_rms), len(wind_rms))
        if envelope_size > 2 and np.std(original_rms[:envelope_size]) and np.std(wind_rms[:envelope_size]):
            envelope_correlation = float(np.corrcoef(original_rms[:envelope_size], wind_rms[:envelope_size])[0, 1])

    reliable = [
        item for item in note_evidence
        if item["pitch_error_cents"] is not None and item["mean_voiced_probability"] >= 0.55
    ]
    within_half_semitone = [item for item in reliable if abs(item["pitch_error_cents"]) <= 50]
    return {
        "method": "librosa.pyin plus onset, envelope, and relative stem-energy comparison",
        "authority": "Local pitch/onset measurements are authoritative; audio-model observations are qualitative.",
        "original_duration_seconds": round(len(original) / sample_rate, 3),
        "wind_duration_seconds": round(len(wind) / sample_rate, 3),
        "mix_to_wind_envelope_correlation": round(envelope_correlation, 4) if envelope_correlation is not None else None,
        "median_note_stem_to_mix_rms_ratio": round(reference_energy_ratio, 5) if reference_energy_ratio is not None else None,
        "stem_onset_count": int(len(stem_onsets)),
        "mix_onset_count": int(len(mix_onsets)),
        "candidate_note_count": len(events),
        "high_confidence_note_count": len(reliable),
        "high_confidence_pitch_agreement": round(len(within_half_semitone) / len(reliable), 4) if reliable else 0.0,
        "notes": note_evidence,
        "stable_pitch_segments": pitch_segments,
    }


def annotate_event_confidence(
    events: list[dict],
    transcription_evidence: dict,
    comparison_events: list[dict] | None = None,
) -> list[dict]:
    """Attach conservative note-level confidence without claiming source identity.

    pYIN can verify pitch stability but cannot distinguish a sax from a sung
    note. A note is therefore called possible non-horn leakage only when the
    wind stem is unusually weak or an independent separator does not confirm it.
    """
    evidence_notes = list(transcription_evidence.get("notes", []))
    pitch_segments = list(transcription_evidence.get("stable_pitch_segments", []))
    annotated: list[dict] = []
    for event in events:
        changed = dict(event)
        start = float(event["start_beat"])
        pitch = int(event["pitch"])
        choices = [
            item for item in evidence_notes
            if abs(float(item.get("start_beat", -99)) - start) <= 0.5
        ]
        evidence = min(
            choices,
            key=lambda item: (
                abs(float(item.get("start_beat", -99)) - start),
                abs(int(item.get("candidate_pitch", pitch)) - pitch),
            ),
        ) if choices else None
        if event.get("ai_correction", {}).get("action") == "insert":
            segment_choices = [
                item for item in pitch_segments
                if abs(float(item.get("measured_median_pitch", -99)) - pitch) <= 0.7
                and float(item.get("start_beat", -99)) <= start + 0.25
                and float(item.get("end_beat", -99)) >= start - 0.1
            ]
            if segment_choices:
                segment = min(segment_choices, key=lambda item: abs(float(item["start_beat"]) - start))
                evidence = {
                    "measured_median_pitch": segment["measured_median_pitch"],
                    "mean_voiced_probability": segment.get("mean_voiced_probability", 0),
                    "voiced_frame_fraction": 1.0,
                    "nearest_stem_onset_delta_seconds": 0.0,
                    "relative_stem_energy": None,
                }

        flags: list[str] = []
        reasons: list[str] = []
        possible_non_horn = False
        if evidence is None:
            score = 0.15
            flags.append("no_local_measurement")
            reasons.append("No matching local pitch measurement was available.")
        else:
            measured_pitch = evidence.get("measured_median_pitch")
            if measured_pitch is None:
                pitch_error_cents = None
                pitch_component = 0.0
                flags.append("no_stable_pitch")
                reasons.append("The horn stem has no stable measured pitch here.")
            else:
                pitch_error_cents = (float(measured_pitch) - pitch) * 100.0
                absolute_error = abs(pitch_error_cents)
                if absolute_error <= 35:
                    pitch_component = 1.0
                elif absolute_error <= 70:
                    pitch_component = 0.72
                    flags.append("pitch_borderline")
                    reasons.append(f"Measured pitch differs by {absolute_error:.0f} cents.")
                elif absolute_error <= 120:
                    pitch_component = 0.32
                    flags.append("pitch_mismatch")
                    reasons.append(f"Measured pitch differs by {absolute_error:.0f} cents.")
                else:
                    pitch_component = 0.0
                    flags.append("pitch_mismatch")
                    reasons.append(f"Measured pitch differs by {absolute_error:.0f} cents.")

            voiced_probability = max(0.0, min(1.0, float(evidence.get("mean_voiced_probability") or 0.0)))
            voiced_fraction = max(0.0, min(1.0, float(evidence.get("voiced_frame_fraction") or 0.0)))
            onset_delta = evidence.get("nearest_stem_onset_delta_seconds")
            if onset_delta is None:
                onset_component = 0.35
                flags.append("onset_unmeasured")
                reasons.append("No clear horn-stem attack was measured.")
            else:
                onset_error = abs(float(onset_delta))
                if onset_error <= 0.08:
                    onset_component = 1.0
                elif onset_error <= 0.16:
                    onset_component = 0.68
                elif onset_error <= 0.30:
                    onset_component = 0.35
                    flags.append("onset_uncertain")
                    reasons.append(f"The closest horn-stem attack is {onset_error:.2f}s away.")
                else:
                    onset_component = 0.1
                    flags.append("onset_uncertain")
                    reasons.append(f"The closest horn-stem attack is {onset_error:.2f}s away.")

            if voiced_probability < 0.55:
                flags.append("weak_voicing_probability")
                reasons.append("Pitch-tracking certainty is weak in the horn stem.")
            if voiced_fraction < 0.5:
                flags.append("low_voiced_coverage")
                reasons.append("Only part of the note has stable pitched audio.")

            score = (
                0.50 * pitch_component
                + 0.25 * voiced_probability
                + 0.15 * voiced_fraction
                + 0.10 * onset_component
            )
            relative_energy = evidence.get("relative_stem_energy")
            if relative_energy is not None and float(relative_energy) < 0.18:
                score *= 0.78
                possible_non_horn = True
                flags.append("weak_wind_stem_energy")
                reasons.append("The sound is unusually weak in the wind stem versus the original; possible leakage or non-horn audio.")

        if comparison_events is not None:
            confirmed = any(
                abs(float(candidate["start_beat"]) - start) <= 0.25
                and abs(int(candidate["pitch"]) - pitch) <= 1
                for candidate in comparison_events
            )
            if not confirmed:
                score *= 0.82
                possible_non_horn = True
                flags.append("separator_disagreement")
                reasons.append("A second separator did not confirm this note; possible leakage or separation artifact.")

        if possible_non_horn:
            score = min(score, 0.76)
        score = round(max(0.0, min(1.0, score)), 3)
        level = "high" if score >= 0.78 else "medium" if score >= 0.52 else "low"
        if level == "medium" and not reasons:
            reasons.append("Audio support is incomplete; review this note by ear.")
        if level == "low" and not reasons:
            reasons.append("Audio support is weak; this note may be an artifact.")
        changed.update(
            confidence_score=score,
            confidence_level=level,
            confidence_reasons=list(dict.fromkeys(reasons)),
            confidence_flags=list(dict.fromkeys(flags)),
            possible_non_horn=possible_non_horn,
        )
        annotated.append(changed)
    return annotated


def summarize_event_confidence(events: list[dict]) -> dict:
    counts = {"high": 0, "medium": 0, "low": 0}
    possible_non_horn = 0
    for event in events:
        level = event.get("confidence_level", "high")
        counts[level if level in counts else "low"] += 1
        possible_non_horn += int(bool(event.get("possible_non_horn", False)))
    return {
        "total": len(events),
        **counts,
        "uncertain": counts["medium"] + counts["low"],
        "possible_non_horn": possible_non_horn,
    }
