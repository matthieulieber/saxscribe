import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from xml.etree import ElementTree

from backend.saxscribe.export import (
    _notation_span,
    export_musicxml,
    normalize_monophonic_timeline,
)


class MusicXMLExportTests(unittest.TestCase):
    def test_collision_safe_subdivisions_survive_musicxml_timing_conversion(self):
        thirty_second = {
            "index": 0,
            "pitch": 60,
            "start_beat": 0.125,
            "duration_beats": 0.125,
        }
        sixty_fourth = {
            "index": 1,
            "pitch": 62,
            "start_beat": 0.0625,
            "duration_beats": 0.0625,
        }

        self.assertEqual(
            _notation_span(thirty_second, "straight-thirty-second"),
            (Fraction(1, 8), Fraction(1, 8)),
        )
        self.assertEqual(
            _notation_span(sixty_fourth, "straight-sixty-fourth"),
            (Fraction(1, 16), Fraction(1, 16)),
        )

    @staticmethod
    def _measure_lengths(path: Path) -> list[Fraction]:
        root = ElementTree.parse(path).getroot()
        divisions = 1
        lengths = []
        for measure in root.findall(".//part/measure"):
            attributes = measure.find("attributes")
            if attributes is not None and attributes.findtext("divisions"):
                divisions = int(attributes.findtext("divisions"))
            cursor = 0
            maximum = 0
            for child in measure:
                duration = child.findtext("duration")
                if not duration:
                    continue
                value = int(duration)
                if child.tag == "note" and child.find("chord") is None:
                    cursor += value
                elif child.tag == "forward":
                    cursor += value
                elif child.tag == "backup":
                    cursor -= value
                maximum = max(maximum, cursor)
            lengths.append(Fraction(maximum, divisions))
        return lengths

    def test_rounded_triplet_timings_are_exportable(self):
        cases = {
            "rounded-pair": [
                {"index": 0, "pitch": 60, "start_beat": 0.0, "duration_beats": 0.6667, "rhythm_grid": "swing-eighth"},
                {"index": 1, "pitch": 62, "start_beat": 0.6667, "duration_beats": 0.3333, "rhythm_grid": "swing-eighth"},
            ],
            "rounded-leading-gap": [
                {"index": 0, "pitch": 60, "start_beat": 0.3333, "duration_beats": 0.3333, "rhythm_grid": "triplet-eighth"},
                {"index": 1, "pitch": 62, "start_beat": 0.6667, "duration_beats": 0.6666, "rhythm_grid": "triplet-eighth"},
            ],
            "rounded-long-note": [
                {"index": 0, "pitch": 60, "start_beat": 0.0, "duration_beats": 5.6667, "rhythm_grid": "triplet-eighth"},
                {"index": 1, "pitch": 62, "start_beat": 5.6667, "duration_beats": 0.3333, "rhythm_grid": "triplet-eighth"},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            for name, events in cases.items():
                with self.subTest(name=name):
                    target = Path(directory) / f"{name}.musicxml"
                    export_musicxml(events, target, 100, "C", "major", "tenor", name, "")
                    xml = target.read_text(encoding="utf-8")
                    self.assertIn("<score-partwise", xml)
                    self.assertIn("<actual-notes>3</actual-notes>", xml)
                    self.assertNotIn("<tuplet ", xml)

    def test_sparse_triplets_keep_timing_without_fragile_brackets(self):
        events = [
            {"index": 0, "pitch": 60, "start_beat": 0, "duration_beats": 1 / 3, "rhythm_grid": "triplet-eighth"},
            {"index": 1, "pitch": 62, "start_beat": 2 / 3, "duration_beats": 1 / 3, "rhythm_grid": "triplet-eighth"},
            {"index": 2, "pitch": 64, "start_beat": 4 / 3, "duration_beats": 2 / 3, "rhythm_grid": "triplet-eighth"},
            {"index": 3, "pitch": 65, "start_beat": 7 / 3, "duration_beats": 1 / 3, "rhythm_grid": "triplet-eighth"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "sparse-triplets.musicxml"
            export_musicxml(events, target, 103, "F", "major", "tenor", "Sparse", "")
            root = ElementTree.parse(target).getroot()

        self.assertTrue(root.findall(".//time-modification"))
        self.assertFalse(root.findall(".//tuplet"))

    def test_blank_artist_does_not_export_music21_as_composer(self):
        events = [
            {"index": 0, "pitch": 60, "start_beat": 0, "duration_beats": 1, "rhythm_grid": "straight-eighth"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "no-composer.musicxml"
            export_musicxml(events, target, 100, "C", "major", "tenor", "Title", "")
            xml = target.read_text(encoding="utf-8")

        self.assertNotIn(">Music21<", xml)
        self.assertNotIn('creator type="composer"', xml)

    def test_rounded_straight_timing_is_snapped_before_export(self):
        events = [
            {"index": 0, "pitch": 60, "start_beat": 0.2501, "duration_beats": 0.4998, "rhythm_grid": "straight-sixteenth"},
            {"index": 1, "pitch": 62, "start_beat": 0.7499, "duration_beats": 0.2502, "rhythm_grid": "straight-sixteenth"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "straight.musicxml"
            export_musicxml(events, target, 100, "C", "major", "tenor", "Straight", "")
            self.assertTrue(target.exists())

    def test_tied_note_does_not_overlap_a_gap_filling_rest(self):
        events = [
            {"index": 0, "pitch": 60, "start_beat": 0.0, "duration_beats": 5.6667, "rhythm_grid": "triplet-eighth"},
            {"index": 1, "pitch": 62, "start_beat": 5.6667, "duration_beats": 0.3333, "rhythm_grid": "triplet-eighth"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "barline-tie.musicxml"
            export_musicxml(events, target, 100, "C", "major", "tenor", "Barline tie", "")
            self.assertEqual(self._measure_lengths(target), [Fraction(4), Fraction(4)])

    def test_overlapping_triplets_are_clipped_before_measure_export(self):
        events = [
            {"index": 0, "pitch": 60, "start_beat": 0, "duration_beats": 4, "rhythm_grid": "triplet-eighth"},
            {"index": 1, "pitch": 62, "start_beat": 4, "duration_beats": 2 / 3, "rhythm_grid": "triplet-eighth"},
            {"index": 2, "pitch": 64, "start_beat": 4 + 1 / 3, "duration_beats": 2 / 3, "rhythm_grid": "triplet-eighth"},
        ]

        normalized = normalize_monophonic_timeline(events)
        self.assertAlmostEqual(normalized[1]["duration_beats"], 1 / 3, places=5)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "overlapping-triplets.musicxml"
            export_musicxml(events, target, 100, "C", "major", "tenor", "Overlap", "")
            self.assertEqual(self._measure_lengths(target), [Fraction(4), Fraction(4)])

    def test_non_finite_timing_fails_with_event_context(self):
        events = [
            {"index": 17, "pitch": 60, "start_beat": 0.0, "duration_beats": float("nan")},
        ]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Event 17 contains non-finite notation timing"):
                export_musicxml(
                    events,
                    Path(directory) / "invalid.musicxml",
                    100,
                    "C",
                    "major",
                    "tenor",
                    "Invalid",
                    "",
                )

    def test_simple_notation_omits_automatic_slurs(self):
        events = [
            {"index": 0, "pitch": 60, "start_beat": 0, "duration_beats": 0.5, "rhythm_grid": "straight-eighth"},
            {"index": 1, "pitch": 62, "start_beat": 0.5, "duration_beats": 0.5, "rhythm_grid": "straight-eighth"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "simple.musicxml"
            export_musicxml(
                events, target, 100, "C", "major", "tenor", "Simple", "",
                detail_level="simple",
            )
            xml = target.read_text(encoding="utf-8")
        self.assertNotIn("<slur", xml)
        self.assertNotIn("Confidence: orange", xml)
        self.assertIn("<movement-title />", xml)

    def test_lead_sheet_writes_swing_as_a_feel_marking(self):
        events = [
            {
                "index": 0,
                "pitch": 60,
                "start_beat": 0,
                "duration_beats": 0.5,
                "rhythm_grid": "straight-eighth",
                "swing_marking": True,
            },
            {
                "index": 1,
                "pitch": 62,
                "start_beat": 0.5,
                "duration_beats": 0.5,
                "rhythm_grid": "straight-eighth",
                "swing_marking": True,
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "swing-lead-sheet.musicxml"
            export_musicxml(
                events,
                target,
                100,
                "C",
                "major",
                "tenor",
                "Swing lead sheet",
                "",
                detail_level="simple",
            )
            xml = target.read_text(encoding="utf-8")

        self.assertIn("Swing eighths", xml)
        self.assertNotIn("<actual-notes>3</actual-notes>", xml)
