import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

import pretty_midi

from backend.saxscribe.analysis import (
    annotate_event_confidence,
    clean_performance_midi,
    infer_rhythm_profile,
    simplify_events,
    summarize_event_confidence,
    write_events_midi,
)
from backend.saxscribe.export import (
    INSTRUMENTS,
    export_musicxml,
    normalize_events_to_written_range,
    validate_written_range,
)
from backend.saxscribe.llm_review import apply_safe_corrections
from backend.saxscribe.pipeline import (
    _pick_target_stem_output,
    _resolve_wind_model,
    _sha256,
    _uvr_output_filename,
    _verify_wind_model,
    JobCancelled,
    separate_wind,
)


class CleanupTests(unittest.TestCase):
    def test_output_name_is_derived_from_current_source_and_label(self):
        self.assertEqual(
            _uvr_output_filename("session take 03.m4a", "Wind Inst"),
            "1_session take 03_(Wind Inst).wav",
        )

    def test_verifies_exact_uvr_checkpoint_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "wind.pth"
            model.write_bytes(b"exact checkpoint")
            expected = _sha256(model)
            self.assertEqual(_verify_wind_model(model, expected), expected)
            with self.assertRaisesRegex(RuntimeError, "checksum is wrong"):
                _verify_wind_model(model, "0" * 64)

    def test_requires_exact_uvr_wind_model(self):
        class ExactCatalogue:
            def list_supported_model_files(self):
                return {"VR": [{"filename": "17_HP-Wind_Inst-UVR.pth"}]}

        class WrongCatalogue:
            def list_supported_model_files(self):
                return {"VR": [{"filename": "some-other-wind-model.pth"}]}

        self.assertEqual(_resolve_wind_model(ExactCatalogue()), "17_HP-Wind_Inst-UVR.pth")
        with self.assertRaises(RuntimeError):
            _resolve_wind_model(WrongCatalogue())

    def test_selects_woodwinds_not_no_woodwinds(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            accompaniment = output_dir / "song_(No Woodwinds)_17_HP-Wind_Inst-UVR.wav"
            horns = output_dir / "song_(Woodwinds)_17_HP-Wind_Inst-UVR.wav"
            accompaniment.touch()
            horns.touch()

            selected = _pick_target_stem_output(
                [accompaniment.name, horns.name], output_dir, "Woodwinds"
            )
            self.assertEqual(selected, horns)

    def test_rejects_complementary_stem(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            accompaniment = output_dir / "song_(No Woodwinds)_17_HP-Wind_Inst-UVR.wav"
            accompaniment.touch()
            with self.assertRaisesRegex(RuntimeError, "No Woodwinds"):
                _pick_target_stem_output([accompaniment.name], output_dir, "Woodwinds")

    def test_separator_matches_working_uvr_gui_settings(self):
        calls = {}

        class FakeSeparator:
            def __init__(self, **kwargs):
                calls["kwargs"] = kwargs
                self.torch_device_cpu = "cpu"
                self.torch_device = "mps"
                self.torch_device_mps = "mps"
                self.onnx_execution_provider = ["CoreMLExecutionProvider"]
                self.model_instance = type("Model", (), {
                    "primary_stem_name": "No Woodwinds",
                    "secondary_stem_name": "Woodwinds",
                    "torch_device_cpu": "cpu",
                    "torch_device": "mps",
                    "torch_device_mps": "mps",
                    "onnx_execution_provider": ["CoreMLExecutionProvider"],
                    "model_params": type("Params", (), {"param": {"band": {1: {"res_type": "kaiser_best"}}}})(),
                })()
                calls["separator"] = self

            def list_supported_model_files(self):
                return {"VR": [{"filename": "17_HP-Wind_Inst-UVR.pth"}]}

            def load_model(self, model_filename):
                calls["model_filename"] = model_filename

            def separate(self, source, custom_output_names=None):
                output_dir = Path(calls["kwargs"]["output_dir"])
                accompaniment = output_dir / "no-wind-reference-(No Woodwinds).wav"
                result = output_dir / "1_input_(Wind Inst).wav"
                accompaniment.write_bytes(b"no woodwinds")
                result.write_bytes(b"woodwinds")
                calls["source"] = source
                calls["custom_output_names"] = custom_output_names
                return [accompaniment.name, result.name]

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "outputs"
            output_dir.mkdir()
            source = Path(directory) / "input.m4a"
            source.write_bytes(b"audio")
            result = separate_wind(source, output_dir, separator_factory=FakeSeparator)

            self.assertEqual(result, output_dir / "1_input_(Wind Inst).wav")
            self.assertEqual(result.read_bytes(), b"woodwinds")
            self.assertTrue((output_dir / "separation-manifest.json").exists())
            self.assertEqual(calls["model_filename"], "17_HP-Wind_Inst-UVR.pth")
            self.assertEqual(calls["kwargs"]["output_single_stem"], "Woodwinds")
            self.assertEqual(calls["custom_output_names"]["Woodwinds"], "1_input_(Wind Inst)")
            self.assertEqual(calls["kwargs"]["output_format"], "WAV")
            self.assertEqual(calls["kwargs"]["sample_rate"], 44100)
            self.assertTrue(calls["kwargs"]["use_soundfile"])
            self.assertEqual(calls["kwargs"]["normalization_threshold"], 1.0)
            self.assertEqual(calls["kwargs"]["vr_params"]["window_size"], 512)
            self.assertEqual(calls["kwargs"]["vr_params"]["aggression"], 5)
            self.assertTrue(calls["kwargs"]["vr_params"]["high_end_process"])
            self.assertEqual(calls["separator"].torch_device, "cpu")
            self.assertIsNone(calls["separator"].torch_device_mps)
            self.assertEqual(calls["separator"].model_instance.torch_device, "cpu")
            self.assertIsNone(calls["separator"].model_instance.torch_device_mps)
            self.assertEqual(calls["separator"].model_instance.model_params.param["band"][1]["res_type"], "polyphase")

    def test_uvr_child_process_is_terminated_on_cancel(self):
        class FakeProcess:
            returncode = None

            def __init__(self):
                self.terminated = False

            def poll(self):
                return None

            def terminate(self):
                self.terminated = True
                self.returncode = -15

            def wait(self, timeout=None):
                return self.returncode

        process = FakeProcess()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.wav"
            source.write_bytes(b"audio")
            with mock.patch("backend.saxscribe.pipeline._verify_wind_model"), \
                 mock.patch("backend.saxscribe.pipeline.subprocess.Popen", return_value=process):
                with self.assertRaises(JobCancelled):
                    separate_wind(source, root, cancel_check=lambda: True)
        self.assertTrue(process.terminated)

    def test_removes_fragments_and_enforces_monophony(self):
        midi = pretty_midi.PrettyMIDI(initial_tempo=120)
        inst = pretty_midi.Instrument(66)
        inst.notes.extend([
            pretty_midi.Note(90, 60, 0.0, 0.5),
            pretty_midi.Note(90, 91, 0.51, 0.52),
            pretty_midi.Note(90, 72, 0.55, 1.0),
        ])
        midi.instruments.append(inst)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mid"
            target = Path(directory) / "target.mid"
            midi.write(str(source))
            events = clean_performance_midi(source, target, 120)
            self.assertTrue(target.exists())
            self.assertEqual(len(events), 2)
            self.assertLessEqual(abs(events[1]["pitch"] - events[0]["pitch"]), 12)

    def test_simple_version_uses_eighth_note_grid_and_drops_short_collision(self):
        advanced = [
            {"index": 0, "pitch": 60, "start_beat": 0.0, "duration_beats": 0.125, "velocity": 70},
            {"index": 1, "pitch": 62, "start_beat": 0.1, "duration_beats": 0.65, "velocity": 90},
            {"index": 2, "pitch": 64, "start_beat": 0.62, "duration_beats": 0.2, "velocity": 84},
        ]
        snapshot = deepcopy(advanced)

        simple = simplify_events(advanced)

        self.assertEqual(advanced, snapshot)
        self.assertEqual([event["pitch"] for event in simple], [62, 64])
        self.assertTrue(all((event["start_beat"] * 2).is_integer() for event in simple))
        self.assertTrue(all((event["duration_beats"] * 2).is_integer() for event in simple))
        self.assertTrue(all(event["duration_beats"] >= 0.5 for event in simple))

    def test_simple_version_merges_adjacent_repeated_pitch(self):
        simple = simplify_events([
            {"index": 0, "pitch": 67, "start_beat": 0.0, "duration_beats": 0.25, "velocity": 80},
            {"index": 1, "pitch": 67, "start_beat": 0.5, "duration_beats": 0.25, "velocity": 88},
            {"index": 2, "pitch": 69, "start_beat": 1.0, "duration_beats": 1.0, "velocity": 86},
        ])

        self.assertEqual(len(simple), 2)
        self.assertEqual(simple[0]["pitch"], 67)
        self.assertEqual(simple[0]["duration_beats"], 1.0)
        self.assertLessEqual(
            simple[0]["start_beat"] + simple[0]["duration_beats"],
            simple[1]["start_beat"],
        )

    def test_simple_version_preserves_the_weakest_merged_confidence(self):
        simple = simplify_events([
            {
                "index": 0,
                "pitch": 67,
                "start_beat": 0.0,
                "duration_beats": 0.5,
                "velocity": 80,
                "confidence_score": 0.92,
                "confidence_level": "high",
                "confidence_reasons": [],
                "confidence_flags": [],
            },
            {
                "index": 1,
                "pitch": 67,
                "start_beat": 0.5,
                "duration_beats": 0.5,
                "velocity": 80,
                "confidence_score": 0.31,
                "confidence_level": "low",
                "confidence_reasons": ["Weak audio support."],
                "confidence_flags": ["weak_support"],
            },
        ])

        self.assertEqual(len(simple), 1)
        self.assertEqual(simple[0]["confidence_level"], "low")
        self.assertEqual(simple[0]["confidence_score"], 0.31)
        self.assertEqual(simple[0]["source_indices"], [0, 1])

    def test_note_confidence_uses_local_audio_evidence(self):
        events = [
            {"index": 0, "pitch": 60, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 88},
            {"index": 1, "pitch": 64, "start_beat": 1.0, "duration_beats": 1.0, "velocity": 88},
        ]
        evidence = {
            "notes": [
                {
                    "index": 0,
                    "candidate_pitch": 60,
                    "start_beat": 0.0,
                    "measured_median_pitch": 60.03,
                    "mean_voiced_probability": 0.94,
                    "voiced_frame_fraction": 0.9,
                    "nearest_stem_onset_delta_seconds": 0.02,
                    "relative_stem_energy": 1.1,
                },
                {
                    "index": 1,
                    "candidate_pitch": 64,
                    "start_beat": 1.0,
                    "measured_median_pitch": 66.0,
                    "mean_voiced_probability": 0.2,
                    "voiced_frame_fraction": 0.2,
                    "nearest_stem_onset_delta_seconds": 0.4,
                    "relative_stem_energy": 0.1,
                },
            ]
        }

        annotated = annotate_event_confidence(events, evidence)
        summary = summarize_event_confidence(annotated)

        self.assertEqual(annotated[0]["confidence_level"], "high")
        self.assertEqual(annotated[1]["confidence_level"], "low")
        self.assertTrue(annotated[1]["possible_non_horn"])
        self.assertIn("pitch_mismatch", annotated[1]["confidence_flags"])
        self.assertEqual(summary, {"total": 2, "high": 1, "medium": 0, "low": 1, "uncertain": 1, "possible_non_horn": 1})

    def test_second_separator_disagreement_is_highlighted_not_called_vocal(self):
        events = [{"index": 0, "pitch": 60, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 88}]
        evidence = {"notes": [{
            "index": 0,
            "candidate_pitch": 60,
            "start_beat": 0.0,
            "measured_median_pitch": 60.0,
            "mean_voiced_probability": 0.95,
            "voiced_frame_fraction": 0.95,
            "nearest_stem_onset_delta_seconds": 0.01,
            "relative_stem_energy": 1.0,
        }]}

        annotated = annotate_event_confidence(events, evidence, comparison_events=[])

        self.assertEqual(annotated[0]["confidence_level"], "medium")
        self.assertTrue(annotated[0]["possible_non_horn"])
        self.assertIn("separator_disagreement", annotated[0]["confidence_flags"])
        self.assertNotIn("vocal", " ".join(annotated[0]["confidence_reasons"]).lower())

    def test_musicxml_colors_uncertain_notes_and_adds_legend(self):
        events = [
            {"index": 0, "pitch": 46, "start_beat": 0, "duration_beats": 1, "confidence_level": "medium"},
            {"index": 1, "pitch": 48, "start_beat": 1, "duration_beats": 1, "confidence_level": "low"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "confidence.musicxml"
            export_musicxml(events, target, 100, "G", "minor", "tenor", "Test", "", "4/4")
            xml = target.read_text(encoding="utf-8")

        self.assertIn('color="#D97706"', xml)
        self.assertIn('color="#C2413B"', xml)
        self.assertIn("Confidence: orange = review by ear", xml)

    def test_musicxml_confidence_highlighting_can_be_disabled(self):
        events = [{
            "index": 0,
            "pitch": 46,
            "start_beat": 0,
            "duration_beats": 1,
            "confidence_level": "low",
        }]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "plain.musicxml"
            export_musicxml(
                events,
                target,
                100,
                "G",
                "minor",
                "tenor",
                "Test",
                "",
                "4/4",
                highlight_uncertain=False,
            )
            xml = target.read_text(encoding="utf-8")

        self.assertNotIn('color="#C2413B"', xml)
        self.assertNotIn("Confidence: orange = review by ear", xml)

    def test_rejects_unsafe_llm_pitch_jump(self):
        events = [{"index": 0, "pitch": 60, "start_beat": 0, "duration_beats": 1, "velocity": 88}]
        corrections = [{"index": 0, "action": "change", "pitch": 72, "start_beat": 0, "duration_beats": 1, "confidence": .99, "reason": "test"}]
        self.assertEqual(apply_safe_corrections(events, corrections)[0]["pitch"], 60)

    def test_allows_audio_supported_octave_repair(self):
        events = [{"index": 0, "pitch": 60, "start_beat": 0, "duration_beats": 1, "velocity": 88}]
        corrections = [{"index": 0, "action": "change", "pitch": 72, "start_beat": 0, "duration_beats": 1, "confidence": .99, "reason": "pYIN supports C5"}]
        evidence = {"notes": [{
            "index": 0,
            "measured_median_pitch": 72.04,
            "mean_voiced_probability": .91,
            "voiced_frame_fraction": .88,
        }]}
        self.assertEqual(apply_safe_corrections(events, corrections, evidence)[0]["pitch"], 72)

    def test_inserts_only_a_pyin_supported_missing_note(self):
        events = [
            {"index": 0, "pitch": 60, "start_beat": 0, "duration_beats": .5, "velocity": 88},
            {"index": 1, "pitch": 64, "start_beat": 1.5, "duration_beats": .5, "velocity": 88},
        ]
        correction = {"index": -1, "action": "insert", "pitch": 62, "start_beat": .75, "duration_beats": .5, "confidence": .95, "reason": "stable missing D"}
        supported = {"stable_pitch_segments": [{
            "start_beat": .7,
            "end_beat": 1.3,
            "measured_median_pitch": 62.1,
            "mean_voiced_probability": .9,
        }]}
        inserted = apply_safe_corrections(events, [correction], supported)
        rejected = apply_safe_corrections(events, [correction], {"stable_pitch_segments": []})
        self.assertEqual([item["pitch"] for item in inserted], [60, 62, 64])
        self.assertEqual([item["pitch"] for item in rejected], [60, 64])

    def test_long_delete_requires_weak_local_horn_evidence(self):
        events = [{"index": 0, "pitch": 60, "start_beat": 0, "duration_beats": 2, "velocity": 88}]
        correction = {"index": 0, "action": "delete", "pitch": 60, "start_beat": 0, "duration_beats": 2, "confidence": .95, "reason": "leakage"}
        weak = {"notes": [{
            "index": 0,
            "measured_median_pitch": None,
            "mean_voiced_probability": .1,
            "voiced_frame_fraction": .1,
        }]}
        strong = {"notes": [{
            "index": 0,
            "measured_median_pitch": 60,
            "mean_voiced_probability": .9,
            "voiced_frame_fraction": .9,
        }]}
        self.assertEqual(apply_safe_corrections(events, [correction], weak), [])
        self.assertEqual(len(apply_safe_corrections(events, [correction], strong)), 1)

    def test_detects_swing_and_triplet_grids_conservatively(self):
        self.assertEqual(infer_rhythm_profile([0, .67, 1, 1.66])["grid"], "swing-eighth")
        self.assertEqual(infer_rhythm_profile([0, .33, .67, 1, 1.33])["grid"], "triplet-eighth")
        self.assertEqual(infer_rhythm_profile([0, .25, .5, .75, 1])["grid"], "straight-sixteenth")

    def test_final_midi_uses_selected_sax_program(self):
        events = [{"index": 0, "pitch": 60, "start_beat": 0, "duration_beats": 1, "velocity": 88}]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "alto.mid"
            write_events_midi(events, target, 100, "alto")
            midi = pretty_midi.PrettyMIDI(str(target))
        self.assertEqual(midi.instruments[0].program, pretty_midi.instrument_name_to_program("Alto Sax"))

    def test_musicxml_adds_swing_dynamics_and_short_slur(self):
        events = [
            {"index": 0, "pitch": 60, "start_beat": 0, "duration_beats": 2 / 3, "velocity": 68, "rhythm_grid": "swing-eighth"},
            {"index": 1, "pitch": 62, "start_beat": 2 / 3, "duration_beats": 1 / 3, "velocity": 70, "rhythm_grid": "swing-eighth"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "expressive.musicxml"
            export_musicxml(events, target, 100, "C", "major", "alto", "Test", "", "4/4")
            xml = target.read_text(encoding="utf-8")
        self.assertIn("Swing eighths", xml)
        self.assertIn("<dynamics", xml)
        self.assertIn("<slur", xml)
        self.assertIn('type="start"', xml)

    def test_tenor_is_written_a_major_ninth_above_concert_pitch(self):
        self.assertEqual(INSTRUMENTS["tenor"][1], 14)
        events = [{"index": 0, "pitch": 44, "start_beat": 0, "duration_beats": 1}]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "tenor.musicxml"
            export_musicxml(events, target, 100, "G", "minor", "tenor", "Test", "", "4/4")
            xml = target.read_text(encoding="utf-8")
            self.assertIn("<chromatic>-2</chromatic>", xml)
            self.assertIn("<octave-change>-1</octave-change>", xml)
            self.assertIn("<sign>G</sign>", xml)
            self.assertIn("<line>2</line>", xml)
            self.assertIn("<step>B</step>", xml)
            self.assertIn("<alter>-1</alter>", xml)
            self.assertIn("<octave>3</octave>", xml)

    def test_low_tenor_outlier_is_octave_folded_into_written_range(self):
        events = [{"index": 3, "pitch": 32, "start_beat": 0, "duration_beats": 1}]
        normalized, adjustments = normalize_events_to_written_range(events, "tenor")
        self.assertEqual(normalized[0]["pitch"], 44)
        self.assertEqual(normalized[0]["pitch"] + INSTRUMENTS["tenor"][1], 58)
        self.assertEqual(adjustments[0]["octaves"], 1)
        validate_written_range(normalized, "tenor")

    def test_export_refuses_impossible_written_tenor_note(self):
        events = [{"index": 0, "pitch": 32, "start_beat": 0, "duration_beats": 1}]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "outside its written range"):
                export_musicxml(
                    events,
                    Path(directory) / "invalid.musicxml",
                    100,
                    "G",
                    "minor",
                    "tenor",
                    "Test",
                    "",
                    "4/4",
                )


if __name__ == "__main__":
    unittest.main()
