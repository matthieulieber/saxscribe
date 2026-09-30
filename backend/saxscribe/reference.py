"""Local reference recordings and conservative, sequence-only MIDI evaluation."""
from __future__ import annotations

from array import array
from pathlib import Path

WRITTEN_TO_CONCERT = {"concert": 0, "soprano": -2, "alto": -9, "tenor": -14, "baritone": -21}


def midi_pitches(path: Path, instrument: str = "concert") -> list[int]:
    import pretty_midi

    midi = pretty_midi.PrettyMIDI(str(path))
    notes = sorted((n for track in midi.instruments if not track.is_drum for n in track.notes),
                   key=lambda n: (n.start, n.pitch))
    if not notes or len(notes) > 2000:
        raise ValueError("Use a reference MIDI containing 1–2000 melody notes.")
    if any(a.end > b.start + 0.03 for a, b in zip(notes, notes[1:])):
        raise ValueError("Use a single melody MIDI without overlapping notes or accompaniment.")
    shift = WRITTEN_TO_CONCERT[instrument]
    pitches = [n.pitch + shift for n in notes]
    if any(p < 0 or p > 127 for p in pitches):
        raise ValueError("Reference pitches fall outside MIDI range after transposition.")
    return pitches


def compare_pitches(reference: list[int], candidate: list[int]) -> dict:
    """Global edit alignment; each note can match at most once.

    This deliberately does not claim onset, rhythm, or engraving accuracy.
    Rests, ties and tempo require a separate score/timing evaluation.
    """
    if not reference or not candidate or max(len(reference), len(candidate)) > 2000:
        raise ValueError("Both melodies must contain 1–2000 notes.")
    rows = [array("H", range(len(candidate) + 1))]
    for i, pitch in enumerate(reference, 1):
        row = array("H", [i])
        for j, other in enumerate(candidate, 1):
            row.append(min(rows[-1][j - 1] + (pitch != other), rows[-1][j] + 1, row[-1] + 1))
        rows.append(row)
    i, j = len(reference), len(candidate)
    counts = dict(matches=0, substitutions=0, octave_errors=0, missing=0, extra=0)
    differences = []
    while i or j:
        if i and j and rows[i][j] == rows[i-1][j-1] + (reference[i-1] != candidate[j-1]):
            if reference[i-1] == candidate[j-1]:
                counts["matches"] += 1
            else:
                counts["substitutions"] += 1
                counts["octave_errors"] += int(abs(reference[i-1] - candidate[j-1]) in (12, 24))
                differences.append({"reference_note": i, "candidate_note": j,
                                    "expected_pitch": reference[i-1], "actual_pitch": candidate[j-1]})
            i, j = i-1, j-1
        elif i and rows[i][j] == rows[i-1][j] + 1:
            counts["missing"] += 1
            differences.append({"reference_note": i, "candidate_note": None})
            i -= 1
        else:
            counts["extra"] += 1
            differences.append({"reference_note": None, "candidate_note": j})
            j -= 1
    return {**counts, "reference_notes": len(reference), "candidate_notes": len(candidate),
            "pitch_precision": counts["matches"] / len(candidate),
            "pitch_recall": counts["matches"] / len(reference),
            "edit_distance": rows[-1][-1], "differences": list(reversed(differences)),
            "scope": "Pitch sequence only; timing, rhythm, rests and engraving are not evaluated."}
