from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from music21 import clef, dynamics, expressions, instrument, key, meter, metadata, note, spanner, stream, tempo


# Tuple values are: music21 instrument, concert-to-written semitones, label,
# lowest written MIDI note, highest written MIDI note. Tenor is a major ninth
# above concert pitch, not merely a major second like soprano.
INSTRUMENTS = {
    "concert": (instrument.SopranoSaxophone, 0, "Concert saxophone", None, None),
    "soprano": (instrument.SopranoSaxophone, 2, "B-flat soprano saxophone", 58, 90),
    "tenor": (instrument.TenorSaxophone, 14, "B-flat tenor saxophone", 58, 90),
    "alto": (instrument.AltoSaxophone, 9, "E-flat alto saxophone", 58, 90),
    "baritone": (instrument.BaritoneSaxophone, 21, "E-flat baritone saxophone", 57, 90),
}

CONFIDENCE_COLORS = {
    "medium": "#D97706",
    "low": "#C2413B",
}


def _dynamic_name(velocity: int) -> str:
    if velocity < 58:
        return "p"
    if velocity < 74:
        return "mp"
    if velocity < 96:
        return "mf"
    return "f"


def normalize_events_to_written_range(
    events: list[dict], selected_instrument: str
) -> tuple[list[dict], list[dict]]:
    """Octave-fold impossible detector outliers into the instrument's written range."""
    _, semitones, _, written_low, written_high = INSTRUMENTS.get(
        selected_instrument, INSTRUMENTS["tenor"]
    )
    normalized: list[dict] = []
    adjustments: list[dict] = []
    for event in events:
        changed = dict(event)
        original_pitch = int(event["pitch"])
        pitch = original_pitch
        if written_low is not None and written_high is not None:
            while pitch + semitones < written_low:
                pitch += 12
            while pitch + semitones > written_high:
                pitch -= 12
        changed["pitch"] = pitch
        normalized.append(changed)
        if pitch != original_pitch:
            adjustments.append(
                {
                    "index": int(event["index"]),
                    "from_concert_midi": original_pitch,
                    "to_concert_midi": pitch,
                    "written_midi": pitch + semitones,
                    "octaves": (pitch - original_pitch) // 12,
                }
            )
    return normalized, adjustments


def validate_written_range(events: list[dict], selected_instrument: str) -> None:
    _, semitones, label, written_low, written_high = INSTRUMENTS.get(
        selected_instrument, INSTRUMENTS["tenor"]
    )
    if written_low is None or written_high is None:
        return
    invalid = [
        (int(event["index"]), int(event["pitch"]) + semitones)
        for event in events
        if not written_low <= int(event["pitch"]) + semitones <= written_high
    ]
    if invalid:
        details = ", ".join(f"event {index}: MIDI {pitch}" for index, pitch in invalid[:8])
        raise ValueError(f"{label} contains notes outside its written range: {details}")


def export_musicxml(
    events: list[dict],
    path: Path,
    bpm: float,
    concert_key: str,
    mode: str,
    selected_instrument: str,
    title: str,
    artist: str,
    meter_value: str = "4/4",
    highlight_uncertain: bool = True,
) -> None:
    instrument_class, semitones, label, _, _ = INSTRUMENTS.get(
        selected_instrument, INSTRUMENTS["tenor"]
    )
    validate_written_range(events, selected_instrument)
    score = stream.Score(id="SaxScribe")
    score.metadata = metadata.Metadata()
    score.metadata.title = title or "Sax transcription"
    score.metadata.composer = artist or ""

    part = stream.Part(id="Saxophone")
    part.partName = label
    part.insert(0, instrument_class())
    part.insert(0, clef.TrebleClef())
    part.insert(0, tempo.MetronomeMark(number=round(bpm)))
    part.insert(0, meter.TimeSignature(meter_value))

    concert = key.Key(concert_key, mode)
    written = concert.transpose(semitones)
    part.insert(0, written)

    has_uncertain_notes = any(
        event.get("confidence_level") in CONFIDENCE_COLORS for event in events
    )
    if highlight_uncertain and has_uncertain_notes:
        legend = expressions.TextExpression(
            "Confidence: orange = review by ear; red = weak support / possible artifact"
        )
        legend.placement = "above"
        legend.style.fontSize = 9
        part.insert(0, legend)

    rhythm_grid = str(events[0].get("rhythm_grid", "straight-sixteenth")) if events else "straight-sixteenth"
    if rhythm_grid == "swing-eighth":
        swing = expressions.TextExpression("Swing eighths")
        swing.placement = "above"
        part.insert(0, swing)

    written_notes: list[tuple[dict, note.Note]] = []
    last_dynamic: str | None = None
    last_dynamic_beat = -99.0
    for event in events:
        item = note.Note()
        item.pitch.midi = int(event["pitch"]) + semitones
        minimum_length = 1.0 / 3.0 if rhythm_grid in {"swing-eighth", "triplet-eighth"} else 0.25
        item.quarterLength = max(minimum_length, float(event["duration_beats"]))
        if highlight_uncertain:
            color = CONFIDENCE_COLORS.get(event.get("confidence_level"))
            if color:
                item.style.color = color
        start = float(event["start_beat"])
        dynamic_name = _dynamic_name(int(event.get("velocity", 88)))
        if last_dynamic is None or (dynamic_name != last_dynamic and start - last_dynamic_beat >= 2.0):
            part.insert(start, dynamics.Dynamic(dynamic_name))
            last_dynamic = dynamic_name
            last_dynamic_beat = start
        part.insert(start, item)
        written_notes.append((event, item))

    # Add short, readable phrase slurs. Long automatic slurs are misleading,
    # so groups stop at four notes, rests, strong leaps, and bar boundaries.
    phrase: list[note.Note] = []
    beats_per_bar = float(meter.TimeSignature(meter_value).barDuration.quarterLength)
    for index, (event, item) in enumerate(written_notes):
        if not phrase:
            phrase = [item]
        if index + 1 >= len(written_notes):
            if len(phrase) >= 2:
                part.insert(0, spanner.Slur(phrase))
            break
        next_event, next_item = written_notes[index + 1]
        end = float(event["start_beat"]) + float(event["duration_beats"])
        gap = float(next_event["start_beat"]) - end
        leap = abs(int(next_event["pitch"]) - int(event["pitch"]))
        crosses_bar = int(float(event["start_beat"]) / beats_per_bar) != int(float(next_event["start_beat"]) / beats_per_bar)
        continue_phrase = gap <= 0.13 and leap <= 9 and not crosses_bar and len(phrase) < 4
        if continue_phrase:
            phrase.append(next_item)
        else:
            if len(phrase) >= 2:
                part.insert(0, spanner.Slur(phrase))
            phrase = [next_item]

    part.makeMeasures(inPlace=True)
    part.makeRests(fillGaps=True, inPlace=True)
    part.makeTies(inPlace=True)
    score.insert(0, part)
    score.write("musicxml", fp=str(path))


def render_pdf(musicxml_path: Path, pdf_path: Path) -> bool:
    candidates = [
        shutil.which("musescore"),
        shutil.which("mscore"),
        "/Applications/MuseScore 4.app/Contents/MacOS/mscore",
    ]
    executable = next((item for item in candidates if item and Path(item).exists()), None)
    if not executable:
        return False
    try:
        completed = subprocess.run(
            [str(executable), "-o", str(pdf_path), str(musicxml_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and pdf_path.exists()
