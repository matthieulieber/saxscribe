import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from backend.saxscribe.refinement import (
    accept_refinement,
    evaluate_candidate,
    evidence_for_candidate,
    match_candidate_to_audio,
    select_lead_sheet_candidate,
    synthesize_candidate_audio,
)


def evidence(*segments):
    return {
        "stable_pitch_segments": [
            {
                "start_beat": start,
                "end_beat": end,
                "onset_beat": onset,
                "measured_median_pitch": pitch,
                "mean_voiced_probability": probability,
            }
            for start, end, onset, pitch, probability in segments
        ]
    }


class ScoreRefinementTests(unittest.TestCase):
    def test_global_assignment_never_reuses_one_audio_event(self):
        events = [
            {"index": 0, "pitch": 60, "start_beat": 0.0, "duration_beats": 0.5},
            {"index": 1, "pitch": 60, "start_beat": 0.2, "duration_beats": 0.5},
        ]
        measured = evidence((0.05, 0.6, 0.08, 60.02, 0.95))

        assignment = match_candidate_to_audio(events, measured)

        self.assertEqual(len(assignment["matches"]), 1)
        self.assertEqual(len(assignment["unmatched_event_indices"]), 1)

    def test_audio_supported_candidate_scores_above_wrong_pitch(self):
        measured = evidence(
            (0.0, 0.9, 0.02, 60.02, 0.95),
            (1.0, 1.9, 1.03, 62.01, 0.92),
        )
        correct = [
            {"index": 0, "pitch": 60, "start_beat": 0, "duration_beats": 1, "rhythm_grid": "straight-eighth"},
            {"index": 1, "pitch": 62, "start_beat": 1, "duration_beats": 1, "rhythm_grid": "straight-eighth"},
        ]
        wrong = [dict(item, pitch=item["pitch"] + 5) for item in correct]

        correct_score = evaluate_candidate(correct, measured)
        wrong_score = evaluate_candidate(wrong, measured)

        self.assertGreater(correct_score["objective_score"], wrong_score["objective_score"])
        self.assertEqual(correct_score["matched_note_count"], 2)
        self.assertEqual(wrong_score["matched_note_count"], 0)

    def test_sparse_evidence_is_not_called_a_reliable_audio_anchor(self):
        measured = evidence((0.0, 0.9, 0.02, 60.02, 0.95))
        candidate = [
            {
                "index": index,
                "pitch": 60 + index,
                "start_beat": index,
                "duration_beats": 1,
                "rhythm_grid": "straight-eighth",
            }
            for index in range(6)
        ]

        score = evaluate_candidate(candidate, measured)

        self.assertEqual(score["matched_note_count"], 1)
        self.assertFalse(score["reliable_audio_anchor"])

    def test_complexity_budget_rejects_detail_reintroduced_by_ai(self):
        baseline = {
            "reliable_audio_anchor": True,
            "objective_score": 70.0,
            "fidelity_score": 82.0,
            "complexity_score": 18.0,
        }
        detailed = {
            "reliable_audio_anchor": True,
            "objective_score": 72.0,
            "fidelity_score": 89.0,
            "complexity_score": 24.0,
        }

        accepted, reason = accept_refinement(baseline, detailed)

        self.assertFalse(accepted)
        self.assertIn("more complex", reason)

    def test_measurable_gain_is_accepted_and_small_gain_is_rejected(self):
        baseline = {
            "reliable_audio_anchor": True,
            "objective_score": 70.0,
            "fidelity_score": 82.0,
            "complexity_score": 18.0,
        }
        better = {
            "reliable_audio_anchor": True,
            "objective_score": 72.0,
            "fidelity_score": 84.0,
            "complexity_score": 18.5,
        }
        same = dict(better, objective_score=70.2)

        self.assertTrue(accept_refinement(baseline, better)[0])
        self.assertFalse(accept_refinement(baseline, same)[0])

    def test_candidate_evidence_is_reindexed_for_safe_correction_guards(self):
        events = [{"index": 7, "pitch": 60, "start_beat": 0, "duration_beats": 1}]
        measured = evidence((0.0, 1.0, 0.02, 60.03, 0.93))

        remapped = evidence_for_candidate(events, measured, 120)

        self.assertEqual(remapped["notes"][0]["index"], 7)
        self.assertAlmostEqual(remapped["notes"][0]["measured_median_pitch"], 60.03)

    def test_symbolic_candidate_renders_as_nonempty_reference_wav(self):
        events = [
            {"index": 0, "pitch": 60, "start_beat": 0, "duration_beats": 1, "velocity": 90},
            {"index": 1, "pitch": 64, "start_beat": 1, "duration_beats": 1, "velocity": 84},
        ]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "candidate.wav"
            synthesize_candidate_audio(events, target, 120, duration_seconds=1.5)
            with wave.open(str(target), "rb") as handle:
                frames = handle.readframes(handle.getnframes())
                self.assertEqual(handle.getframerate(), 16000)
                self.assertGreater(handle.getnframes(), 16000)

        self.assertNotEqual(set(frames), {0})

    def test_candidate_selector_requires_stronger_fidelity_to_override_detected_grid(self):
        events = [
            {
                "index": index,
                "pitch": 60 + index,
                "start_beat": index * 0.25,
                "duration_beats": 0.25,
                "performance_start_beat": index * 0.25,
                "performance_duration_beats": 0.25,
                "confidence_score": .9,
                "confidence_level": "high",
                "rhythm_grid": "straight-sixteenth",
            }
            for index in range(8)
        ]
        adaptive_score = {
            "fidelity_score": 82.0,
            "complexity_score": 31.0,
            "objective_score": 72.7,
        }
        conservative_score = {
            "fidelity_score": 84.0,
            "complexity_score": 22.0,
            "objective_score": 77.4,
        }

        with mock.patch(
            "backend.saxscribe.refinement.evaluate_candidate",
            side_effect=[adaptive_score, conservative_score],
        ):
            selected, report = select_lead_sheet_candidate(events, 110, {})

        self.assertEqual(report["selected"], "conservative")
        self.assertTrue(
            all(item["rhythm_grid"] == "straight-sixteenth" for item in selected)
        )

    def test_candidate_selector_does_not_trade_supported_notes_for_complexity(self):
        events = [
            {
                "index": index,
                "pitch": 60 + index,
                "start_beat": index / 3,
                "duration_beats": 1 / 3,
                "performance_start_beat": index / 3,
                "performance_duration_beats": 1 / 3,
                "confidence_score": .9,
                "confidence_level": "high",
                "rhythm_grid": "triplet-eighth",
            }
            for index in range(6)
        ]
        adaptive_score = {
            "fidelity_score": 90.0,
            "complexity_score": 35.0,
            "objective_score": 79.5,
        }
        conservative_score = {
            "fidelity_score": 89.0,
            "complexity_score": 20.0,
            "objective_score": 83.0,
        }

        with mock.patch(
            "backend.saxscribe.refinement.evaluate_candidate",
            side_effect=[adaptive_score, conservative_score],
        ):
            selected, report = select_lead_sheet_candidate(events, 110, {})

        self.assertEqual(report["selected"], "adaptive")
        self.assertTrue(all(item["rhythm_grid"] == "triplet-eighth" for item in selected))


if __name__ == "__main__":
    unittest.main()
