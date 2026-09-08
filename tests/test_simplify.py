import unittest

from backend.saxscribe.simplify import simplify_events


def event(
    index,
    pitch,
    start,
    duration=0.28,
    *,
    grid="straight-sixteenth",
    confidence=0.9,
    level="high",
    non_horn=False,
):
    return {
        "index": index,
        "pitch": pitch,
        "start_beat": start,
        "duration_beats": duration,
        "performance_start_beat": start,
        "performance_duration_beats": duration,
        "velocity": 84,
        "rhythm_grid": grid,
        "confidence_score": confidence,
        "confidence_level": level,
        "possible_non_horn": non_horn,
    }


class SimplificationTests(unittest.TestCase):
    def test_repeated_true_triplets_are_preserved_without_melody_reduction(self):
        events = [
            event(index, 60 + index % 7, index / 3, 1 / 3, grid="triplet-eighth")
            for index in range(12)
        ]

        lead_sheet = simplify_events(events, bpm=120)

        self.assertEqual(len(lead_sheet), len(events))
        self.assertTrue(
            all(item["rhythm_grid"] == "triplet-eighth" for item in lead_sheet)
        )
        self.assertTrue(
            all(
                abs(item["start_beat"] * 3 - round(item["start_beat"] * 3)) < 0.001
                for item in lead_sheet
            )
        )

    def test_conservative_interpretation_uses_straight_grid_without_losing_notes(self):
        events = [
            event(index, 60 + index % 7, index / 3, 1 / 3, grid="triplet-eighth")
            for index in range(12)
        ]

        adaptive = simplify_events(events, bpm=120, interpretation="adaptive")
        conservative = simplify_events(events, bpm=120, interpretation="conservative")

        self.assertEqual(len(conservative), len(adaptive))
        self.assertEqual(len(conservative), len(events))
        self.assertTrue(
            all(item["rhythm_grid"] == "straight-sixteenth" for item in conservative)
        )
        self.assertTrue(
            all(
                abs(item["start_beat"] * 4 - round(item["start_beat"] * 4)) < 0.001
                for item in conservative
            )
        )

    def test_isolated_triplet_guess_uses_collision_safe_straight_grid(self):
        events = [
            event(index, 60 + index * 2, index / 3, 1 / 3, grid="triplet-eighth")
            for index in range(3)
        ]

        lead_sheet = simplify_events(events)

        self.assertTrue(
            all(item["rhythm_grid"] == "straight-sixteenth" for item in lead_sheet)
        )
        self.assertTrue(
            all(
                abs(item["start_beat"] * 4 - round(item["start_beat"] * 4)) < 0.001
                for item in lead_sheet
            )
        )

    def test_swing_is_written_as_straight_eighths_with_a_feel_marking(self):
        events = [
            event(0, 60, 0.0, 2 / 3, grid="swing-eighth"),
            event(1, 62, 2 / 3, 1 / 3, grid="swing-eighth"),
            event(2, 64, 1.0, 2 / 3, grid="swing-eighth"),
            event(3, 65, 5 / 3, 1 / 3, grid="swing-eighth"),
        ]

        lead_sheet = simplify_events(events)

        self.assertTrue(all(item["swing_marking"] for item in lead_sheet))
        self.assertTrue(
            all(item["rhythm_grid"] == "straight-eighth" for item in lead_sheet)
        )
        self.assertTrue(
            all(
                abs(item["start_beat"] * 2 - round(item["start_beat"] * 2)) < 0.001
                for item in lead_sheet
            )
        )

    def test_repeated_supported_sixteenth_anticipations_are_preserved(self):
        starts = [0.0, 0.25, 1.0, 1.25, 2.0, 2.25]
        events = [event(index, 60 + index, start) for index, start in enumerate(starts)]

        lead_sheet = simplify_events(events, bpm=105)

        self.assertTrue(
            all(item["rhythm_grid"] == "straight-sixteenth" for item in lead_sheet)
        )
        self.assertTrue(any(item["start_beat"] == 0.25 for item in lead_sheet))
        self.assertTrue(any(item["start_beat"] == 1.25 for item in lead_sheet))
        self.assertTrue(any(item["start_beat"] == 2.25 for item in lead_sheet))
        self.assertTrue(
            all(
                item["start_beat"] + item["duration_beats"]
                <= lead_sheet[index + 1]["start_beat"]
                for index, item in enumerate(lead_sheet[:-1])
            )
        )

    def test_dense_supported_sixteenth_run_is_not_erased_by_simplification(self):
        starts = [0, 0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75]
        events = [event(index, 60 + index, start) for index, start in enumerate(starts)]

        lead_sheet = simplify_events(events, bpm=105)

        self.assertEqual(len(lead_sheet), len(events))
        self.assertTrue(
            all(item["rhythm_grid"] == "straight-sixteenth" for item in lead_sheet)
        )
        self.assertTrue(
            all(
                abs(item["start_beat"] * 4 - round(item["start_beat"] * 4)) < 0.001
                for item in lead_sheet
            )
        )

    def test_musician_corrected_sax_excerpt_preserves_all_supported_notes(self):
        # Six sax phrases from the supplied correction. The four notes in bar
        # one and the first note in bar two were explicitly labelled non-sax
        # and are therefore not part of this reference sequence.
        reference = [
            (67, 6.5, .25), (70, 6.75, .25), (67, 7.0, .25),
            (63, 7.25, .375), (65, 7.625, .125), (63, 7.75, .25),
            (60, 8.0, .25), (58, 8.25, .25), (55, 8.5, .25),
            (58, 8.75, .25), (60, 9.0, .5), (63, 9.5, .5),
            (67, 10.0, .25), (70, 10.5, .5), (65, 11.25, .25),
            (70, 11.75, .25), (62, 12.0, 1.0), (60, 13.0, .5),
            (60, 13.5, 1.0), (60, 15.25, .25), (63, 15.5, .25),
            (60, 15.75, .25), (65, 16.0, .5), (63, 17.0, .5),
            (65, 17.5, .25), (67, 17.75, .25), (67, 18.75, .25),
            (65, 19.0, .25), (63, 19.25, .25), (60, 19.5, .25),
            (58, 19.75, .25), (63, 20.0, .5), (65, 20.5, .25),
            (62, 20.75, .25), (58, 21.5, .25), (55, 21.75, 1.0),
        ]
        events = [
            event(index, pitch, start, duration)
            for index, (pitch, start, duration) in enumerate(reference)
        ]

        lead_sheet = simplify_events(events, bpm=103)

        self.assertEqual(len(lead_sheet), 36)
        self.assertEqual(lead_sheet[0]["start_beat"], 6.5)
        self.assertEqual(
            {source for item in lead_sheet for source in item["source_indices"]},
            set(range(36)),
        )

    def test_supported_repeated_pitch_reattacks_are_not_merged(self):
        lead_sheet = simplify_events(
            [
                event(0, 67, 0, 0.5, confidence=0.92),
                event(1, 67, 0.5, 0.5, confidence=0.91),
                event(2, 69, 1, 1.0, confidence=0.9),
            ]
        )

        self.assertEqual([item["pitch"] for item in lead_sheet], [67, 67, 69])
        self.assertEqual(lead_sheet[0]["source_indices"], [0])
        self.assertEqual(lead_sheet[1]["source_indices"], [1])

    def test_small_performance_drift_snaps_without_changing_source_timing(self):
        events = [
            event(0, 60, 0.07, 0.39),
            event(1, 62, 0.56, 0.4),
            event(2, 64, 1.03, 0.9),
        ]

        lead_sheet = simplify_events(events)

        self.assertEqual([item["start_beat"] for item in lead_sheet], [0.0, 0.5, 1.0])
        self.assertEqual(
            [item["performance_start_beat"] for item in lead_sheet],
            [0.07, 0.56, 1.03],
        )
        self.assertEqual(
            [item["timing_adjustment_beats"] for item in lead_sheet],
            [-0.07, -0.06, -0.03],
        )

    def test_short_weak_fragment_is_removed(self):
        events = [
            event(0, 60, 0, 0.75, confidence=0.92),
            event(1, 73, 0.5, 0.2, confidence=0.2, level="low", non_horn=True),
            event(2, 64, 1, 0.75, confidence=0.88),
        ]

        lead_sheet = simplify_events(events)

        self.assertEqual([item["pitch"] for item in lead_sheet], [60, 64])

    def test_globally_weak_evidence_keeps_notes_for_human_review(self):
        events = [
            event(
                index,
                60 + index,
                index * 0.5,
                0.2,
                confidence=0.2 + index * 0.05,
                level="low",
                non_horn=True,
            )
            for index in range(4)
        ]

        lead_sheet = simplify_events(events)

        self.assertEqual(len(lead_sheet), 4)
        self.assertEqual([item["pitch"] for item in lead_sheet], [60, 61, 62, 63])

    def test_sparse_support_does_not_collapse_unverified_dense_run(self):
        events = [
            event(
                index,
                60 + index,
                index * 0.25,
                0.25,
                confidence=0.9 if index == 2 else 0.2,
                level="high" if index == 2 else "low",
                non_horn=index != 2,
            )
            for index in range(6)
        ]

        lead_sheet = simplify_events(events)

        self.assertEqual(len(lead_sheet), 6)
        self.assertTrue(
            all(item["rhythm_grid"] == "straight-sixteenth" for item in lead_sheet)
        )

    def test_adjacent_repeated_pitch_is_merged_and_keeps_weakest_confidence(self):
        lead_sheet = simplify_events(
            [
                event(0, 67, 0, 0.5, confidence=0.92),
                event(1, 67, 0.5, 0.5, confidence=0.31, level="low"),
                event(2, 69, 1, 1.0, confidence=0.9),
            ]
        )

        self.assertEqual([item["pitch"] for item in lead_sheet], [67, 69])
        self.assertEqual(lead_sheet[0]["duration_beats"], 1.0)
        self.assertEqual(lead_sheet[0]["confidence_level"], "low")
        self.assertEqual(lead_sheet[0]["source_indices"], [0, 1])


if __name__ == "__main__":
    unittest.main()
