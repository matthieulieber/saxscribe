from __future__ import annotations

import math
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from xml.etree import ElementTree

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


def _notation_quantum(rhythm_grid: str) -> Fraction:
    if rhythm_grid == "straight-eighth":
        return Fraction(1, 2)
    if rhythm_grid in {"swing-eighth", "triplet-eighth"}:
        return Fraction(1, 3)
    if rhythm_grid == "straight-thirty-second":
        return Fraction(1, 8)
    if rhythm_grid == "straight-sixty-fourth":
        return Fraction(1, 16)
    return Fraction(1, 4)


def _notation_span(event: dict, rhythm_grid: str) -> tuple[Fraction, Fraction]:
    """Convert rounded detector timing to exact, writable notation values.

    Pipeline JSON stores beat positions as decimal floats. In a triplet grid,
    values such as 0.3333 and 0.6667 are only approximations; passing those
    decimals to music21 can create an ``inexpressible`` duration. Snap the
    event's start and end to the declared grid and return exact Fractions so
    gap-filling rests and tied notes are expressible in MusicXML as well.
    """
    raw_start = float(event["start_beat"])
    raw_duration = float(event["duration_beats"])
    if not math.isfinite(raw_start) or not math.isfinite(raw_duration):
        raise ValueError(
            f"Event {event.get('index', '?')} contains non-finite notation timing."
        )
    quantum = _notation_quantum(rhythm_grid)

    def snap(value: float) -> Fraction:
        nonnegative = Fraction(str(max(0.0, value)))
        ticks = int(nonnegative / quantum + Fraction(1, 2))
        return ticks * quantum

    start = snap(raw_start)
    end = snap(raw_start + max(0.0, raw_duration))
    if end <= start:
        end = start + quantum
    return start, end - start


def _event_priority(event: dict, duration: Fraction) -> tuple:
    confidence_level = {"low": 0, "medium": 1, "high": 2}.get(
        str(event.get("confidence_level", "high")), 1
    )
    return (
        not bool(event.get("possible_non_horn", False)),
        confidence_level,
        float(event.get("confidence_score", 1.0)),
        duration,
        int(event.get("velocity", 88)),
        -int(event.get("index", 0)),
    )


def normalize_monophonic_timeline(events: list[dict]) -> list[dict]:
    """Snap, de-duplicate, and clip events into one non-overlapping line.

    Quantizing two consecutive attacks can move them onto the same grid point,
    or leave the first note extending beyond the second. music21 represents
    that accidental overlap as another voice; after rest filling it can create
    an overfull measure. Resolve the collision before MIDI or MusicXML export.
    """
    if not events:
        return []
    rhythm_grid = str(events[0].get("rhythm_grid", "straight-sixteenth"))
    by_start: dict[Fraction, tuple[dict, Fraction]] = {}
    for event in events:
        start, duration = _notation_span(event, rhythm_grid)
        candidate = (event, duration)
        existing = by_start.get(start)
        if existing is None or _event_priority(*candidate) > _event_priority(*existing):
            by_start[start] = candidate

    ordered = sorted(
        ((start, event, duration) for start, (event, duration) in by_start.items()),
        key=lambda item: item[0],
    )
    normalized: list[dict] = []
    for position, (start, event, duration) in enumerate(ordered):
        if position + 1 < len(ordered):
            next_start = ordered[position + 1][0]
            duration = min(duration, next_start - start)
        if duration <= 0:
            continue
        changed = dict(event)
        changed["start_beat"] = round(float(start), 6)
        changed["duration_beats"] = round(float(duration), 6)
        normalized.append(changed)
    return normalized


def _validate_musicxml_measure_lengths(path: Path) -> None:
    """Refuse a generated score containing an overfull or incomplete measure."""
    root = ElementTree.parse(path).getroot()
    divisions = 1
    beats = 4
    beat_type = 4
    for measure in root.findall(".//part/measure"):
        attributes = measure.find("attributes")
        if attributes is not None:
            divisions_text = attributes.findtext("divisions")
            if divisions_text:
                divisions = int(divisions_text)
            time = attributes.find("time")
            if time is not None:
                beats_text = time.findtext("beats")
                beat_type_text = time.findtext("beat-type")
                if beats_text and beat_type_text:
                    beats = int(beats_text)
                    beat_type = int(beat_type_text)

        cursor = 0
        maximum = 0
        for child in measure:
            duration_text = child.findtext("duration")
            if not duration_text:
                continue
            duration_value = int(duration_text)
            if child.tag == "note" and child.find("chord") is None:
                cursor += duration_value
            elif child.tag == "forward":
                cursor += duration_value
            elif child.tag == "backup":
                cursor -= duration_value
            maximum = max(maximum, cursor)

        expected = Fraction(beats * 4 * divisions, beat_type)
        actual = Fraction(maximum, 1)
        if actual > expected or (
            measure.get("implicit") != "yes" and actual != expected
        ):
            actual_beats = actual / divisions
            expected_beats = expected / divisions
            raise ValueError(
                f"Generated MusicXML measure {measure.get('number', '?')} has "
                f"{actual_beats} beats; expected {expected_beats}. Export stopped "
                "before returning a corrupt score."
            )


def _make_musescore_compatible(path: Path, artist: str) -> None:
    """Remove optional markup that makes MuseScore reject valid timing.

    music21 can emit explicit tuplet bracket start/stop elements around sparse
    triplet notes and rests.  The underlying ``time-modification`` values are
    sufficient to preserve the rhythmic duration, while those visual spanners
    can describe an incomplete group that MuseScore reports as corruption.
    Keep the exact triplet timing and omit only the fragile bracket markup.

    A blank composer also needs explicit handling: a fresh music21 Metadata
    object otherwise exports ``Music21`` as the composer name.
    """
    tree = ElementTree.parse(path)
    root = tree.getroot()

    if not artist.strip():
        identification = root.find("identification")
        if identification is not None:
            for creator in list(identification.findall("creator")):
                if creator.get("type") == "composer":
                    identification.remove(creator)

    for xml_note in root.findall(".//note"):
        notations = xml_note.find("notations")
        if notations is None:
            continue
        for tuplet in list(notations.findall("tuplet")):
            notations.remove(tuplet)
        if not list(notations):
            xml_note.remove(notations)

    ElementTree.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


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
    detail_level: str = "advanced",
) -> None:
    events = normalize_monophonic_timeline(events)
    instrument_class, semitones, label, _, _ = INSTRUMENTS.get(
        selected_instrument, INSTRUMENTS["tenor"]
    )
    validate_written_range(events, selected_instrument)
    score = stream.Score(id="SaxScribe")
    score.metadata = metadata.Metadata()
    score.metadata.title = title or "Sax transcription"
    # music21 otherwise repeats the work title as a movement title, which the
    # browser renderer displays as a redundant subtitle.
    score.metadata.movementName = ""
    # Setting an empty value prevents music21's default ``Music21`` credit;
    # the compatibility pass removes the resulting empty creator element.
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
    if highlight_uncertain and has_uncertain_notes and detail_level != "simple":
        legend = expressions.TextExpression(
            "Confidence: orange = review by ear; red = weak"
        )
        legend.placement = "above"
        legend.style.fontSize = 9
        part.insert(0, legend)

    rhythm_grid = str(events[0].get("rhythm_grid", "straight-sixteenth")) if events else "straight-sixteenth"
    if rhythm_grid == "swing-eighth" or (
        events and bool(events[0].get("swing_marking", False))
    ):
        swing = expressions.TextExpression("Swing eighths")
        swing.placement = "above"
        part.insert(0, swing)

    written_notes: list[tuple[dict, note.Note, Fraction, Fraction]] = []
    last_dynamic: str | None = None
    last_dynamic_beat = -99.0
    for event in events:
        start, duration = _notation_span(event, rhythm_grid)
        item = note.Note()
        item.pitch.midi = int(event["pitch"]) + semitones
        item.quarterLength = duration
        if highlight_uncertain:
            color = CONFIDENCE_COLORS.get(event.get("confidence_level"))
            if color:
                item.style.color = color
        start_beat = float(start)
        dynamic_name = _dynamic_name(int(event.get("velocity", 88)))
        if last_dynamic is None or (
            detail_level != "simple"
            and dynamic_name != last_dynamic
            and start_beat - last_dynamic_beat >= 2.0
        ):
            part.insert(start, dynamics.Dynamic(dynamic_name))
            last_dynamic = dynamic_name
            last_dynamic_beat = start_beat
        part.insert(start, item)
        written_notes.append((event, item, start, duration))

    # Add short, readable phrase slurs. Long automatic slurs are misleading,
    # so groups stop at four notes, rests, strong leaps, and bar boundaries.
    if detail_level != "simple":
        phrase: list[note.Note] = []
        beats_per_bar = float(meter.TimeSignature(meter_value).barDuration.quarterLength)
        for index, (event, item, start, duration) in enumerate(written_notes):
            if not phrase:
                phrase = [item]
            if index + 1 >= len(written_notes):
                if len(phrase) >= 2:
                    part.insert(0, spanner.Slur(phrase))
                break
            next_event, next_item, next_start, _ = written_notes[index + 1]
            end = start + duration
            gap = float(next_start - end)
            leap = abs(int(next_event["pitch"]) - int(event["pitch"]))
            crosses_bar = int(float(start) / beats_per_bar) != int(float(next_start) / beats_per_bar)
            continue_phrase = gap <= 0.13 and leap <= 9 and not crosses_bar and len(phrase) < 4
            if continue_phrase:
                phrase.append(next_item)
            else:
                if len(phrase) >= 2:
                    part.insert(0, spanner.Slur(phrase))
                phrase = [next_item]

    part.makeMeasures(inPlace=True)
    part.makeTies(inPlace=True)
    # Split notes across barlines before filling gaps. Reversing this order
    # inserts a rest beneath the tied continuation and creates an overfull,
    # MuseScore-corrupt measure.
    part.makeRests(fillGaps=True, inPlace=True)
    part.splitAtDurations(recurse=True)
    score.insert(0, part)
    score.write("musicxml", fp=str(path))
    _make_musescore_compatible(path, artist)
    _validate_musicxml_measure_lengths(path)


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
