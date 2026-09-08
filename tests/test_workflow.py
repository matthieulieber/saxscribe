import unittest
import json
import tempfile
from pathlib import Path
from unittest import mock

from backend import app as backend_app
from backend.saxscribe.analysis import AudioFacts, write_events_midi
from backend.saxscribe.llm_review import ReviewResult
from backend.saxscribe import pipeline as pipeline_module
from backend.saxscribe.separation import StemCandidate
from backend.saxscribe.settings import settings


class WorkflowRegressionTests(unittest.TestCase):
    def test_local_launcher_waits_for_backend_health_before_starting_vite(self):
        source = (Path(__file__).parents[1] / "scripts" / "start-local.sh").read_text(encoding="utf-8")
        health_check = "http://127.0.0.1:8000/api/health"
        vite_start = "npm run dev"

        self.assertIn(health_check, source)
        self.assertIn("backend-startup.log", source)
        self.assertIn('kill -0 "$api_pid"', source)
        self.assertIn('[[ "$backend_ready" != true ]]', source)
        self.assertLess(source.index(health_check), source.index(vite_start))
        self.assertNotIn("--reload", source)

    def test_backend_persists_jobs_caps_both_uploads_and_exposes_cancel(self):
        source = (Path(__file__).parents[1] / "backend" / "app.py").read_text(encoding="utf-8")
        self.assertIn('path = settings.work_dir / job_id / "job.json"', source)
        self.assertIn("await _save_upload(original, original_path, max_bytes)", source)
        self.assertIn("await _save_upload(isolated, isolated_path, max_bytes)", source)
        self.assertIn('@app.post("/api/jobs/{job_id}/cancel")', source)

    def test_pipeline_calls_pdf_renderer_and_selected_midi_instrument(self):
        source = (Path(__file__).parents[1] / "backend" / "saxscribe" / "pipeline.py").read_text(encoding="utf-8")
        self.assertIn("render_pdf(simple_xml, simple_pdf)", source)
        self.assertIn("render_pdf(advanced_xml, advanced_pdf)", source)
        self.assertIn("write_events_midi(simple_events, simple_midi, selected_bpm, selected_instrument)", source)
        self.assertIn("effective_provider = separation_provider or settings.separation_provider", source)

    def test_enhanced_refinement_is_rendered_scored_and_bounded(self):
        pipeline = (Path(__file__).parents[1] / "backend" / "saxscribe" / "pipeline.py").read_text(encoding="utf-8")
        refinement = (Path(__file__).parents[1] / "backend" / "saxscribe" / "refinement.py").read_text(encoding="utf-8")
        frontend = (Path(__file__).parents[1] / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")

        self.assertGreaterEqual(pipeline.count("synthesize_candidate_audio("), 2)
        self.assertIn("refine_lead_sheet(", pipeline)
        self.assertGreaterEqual(pipeline.count("accept_refinement("), 2)
        self.assertIn("linear_sum_assignment", refinement)
        self.assertIn("maximum_ai_passes", pipeline)
        self.assertIn("Two-pass score check complete", frontend)
        self.assertIn("quality improves without adding clutter", frontend)

    def test_server_derives_paid_plan_features_instead_of_trusting_the_browser(self):
        source = (Path(__file__).parents[1] / "backend" / "app.py").read_text(encoding="utf-8")
        self.assertIn('plan: str = Form("free")', source)
        self.assertIn('separation_provider = "uvr"', source)
        self.assertIn('separation_provider = "lalal"', source)
        self.assertIn("use_ai = True", source)
        self.assertIn("verify_paid_enhanced_checkout", source)
        self.assertIn("claim_checkout_session", source)
        worker = (Path(__file__).parents[1] / "backend" / "cloud_worker.py").read_text(encoding="utf-8")
        self.assertIn('use_ai = plan == "enhanced"', worker)
        self.assertIn('separation_provider = "lalal" if plan == "enhanced" else "uvr"', worker)
        self.assertIn("release_checkout_session(job[\"payment_session_id\"], job_id)", worker)

    def test_completed_local_job_state_reloads_from_disk(self):
        previous = settings.work_dir
        with tempfile.TemporaryDirectory() as directory:
            work_dir = Path(directory)
            object.__setattr__(settings, "work_dir", work_dir)
            try:
                job_dir = work_dir / "abc123"
                job_dir.mkdir()
                (job_dir / "job.json").write_text(json.dumps({
                    "id": "abc123",
                    "status": "complete",
                    "stage": "complete",
                    "result": {"files": []},
                }), encoding="utf-8")
                backend_app.jobs.clear()
                backend_app.cancel_events.clear()
                backend_app._load_local_jobs()
                self.assertEqual(backend_app.jobs["abc123"]["status"], "complete")
            finally:
                backend_app.jobs.clear()
                backend_app.cancel_events.clear()
                object.__setattr__(settings, "work_dir", previous)

    def test_local_worker_prints_and_persists_a_failure_traceback(self):
        previous = settings.work_dir
        with tempfile.TemporaryDirectory() as directory:
            work_dir = Path(directory)
            job_id = "failed123"
            job_dir = work_dir / job_id
            job_dir.mkdir()
            object.__setattr__(settings, "work_dir", work_dir)
            backend_app.jobs[job_id] = {
                "id": job_id,
                "status": "queued",
                "stage": "exporting",
            }
            backend_app.cancel_events[job_id] = backend_app.threading.Event()
            try:
                with mock.patch.object(
                    backend_app,
                    "run_pipeline",
                    side_effect=ValueError("synthetic export failure"),
                ), self.assertLogs("uvicorn.error", level="ERROR") as captured:
                    backend_app._run(
                        job_id=job_id,
                        job_dir=job_dir,
                        original=job_dir / "input.aif",
                        youtube_url=None,
                        isolated=None,
                        title="",
                        artist="",
                        instrument="tenor",
                        use_ai=False,
                        separation_provider="uvr",
                        highlight_uncertain=True,
                        original_name="input.aif",
                        isolated_name=None,
                        cancel_event=backend_app.cancel_events[job_id],
                    )
                self.assertIn("failed during exporting", "\n".join(captured.output))
                error_log = (job_dir / "error.log").read_text(encoding="utf-8")
                self.assertIn("synthetic export failure", error_log)
                self.assertIn("Traceback", error_log)
                self.assertEqual(backend_app.jobs[job_id]["status"], "error")
            finally:
                backend_app.jobs.clear()
                backend_app.cancel_events.clear()
                object.__setattr__(settings, "work_dir", previous)

    def test_youtube_input_is_server_validated_and_worker_downloaded(self):
        source = (Path(__file__).parents[1] / "backend" / "app.py").read_text(encoding="utf-8")
        self.assertIn('youtube_url: str = Form("")', source)
        self.assertIn("normalize_youtube_url(requested_youtube_url)", source)
        self.assertIn("if not youtube_authorized", source)
        self.assertIn('"youtube_authorized_at": _now()', source)
        self.assertIn("has_upload == bool(requested_youtube_url)", source)
        worker = (Path(__file__).parents[1] / "backend" / "cloud_worker.py").read_text(encoding="utf-8")
        self.assertIn("download_youtube_audio(", worker)
        self.assertIn("release_checkout_session", worker)

    def test_enhanced_pipeline_runs_bounded_lead_sheet_feedback_pass(self):
        events = [
            {"index": 0, "pitch": 60, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 88, "rhythm_grid": "straight-eighth"},
            {"index": 1, "pitch": 62, "start_beat": 1.0, "duration_beats": 1.0, "velocity": 88, "rhythm_grid": "straight-eighth"},
        ]
        evidence = {
            "notes": [
                {"index": 0, "candidate_pitch": 60, "start_beat": 0, "measured_median_pitch": 60.01, "mean_voiced_probability": .95, "voiced_frame_fraction": .95, "nearest_stem_onset_delta_seconds": 0.01, "relative_stem_energy": 1.0},
                {"index": 1, "candidate_pitch": 62, "start_beat": 1, "measured_median_pitch": 62.02, "mean_voiced_probability": .94, "voiced_frame_fraction": .94, "nearest_stem_onset_delta_seconds": 0.01, "relative_stem_energy": 1.0},
            ],
            "stable_pitch_segments": [
                {"start_beat": 0, "end_beat": .9, "onset_beat": .01, "measured_median_pitch": 60.01, "mean_voiced_probability": .95},
                {"start_beat": 1, "end_beat": 1.9, "onset_beat": 1.01, "measured_median_pitch": 62.02, "mean_voiced_probability": .94},
                {"start_beat": 2, "end_beat": 2.8, "onset_beat": 2.01, "measured_median_pitch": 64.01, "mean_voiced_probability": .91},
            ],
        }
        first_review = ReviewResult(
            used=True,
            summary="First pass complete.",
            researched_song={},
            selected_context={},
            corrections=[],
            sources=[],
            audio_comparison=[],
            models={},
        )
        second_review = ReviewResult(
            used=True,
            summary="No safe second-pass change.",
            researched_song={},
            selected_context={},
            corrections=[],
            sources=[],
            audio_comparison=[],
            models={},
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original.wav"
            isolated = root / "isolated.wav"
            original.write_bytes(b"source")
            isolated.write_bytes(b"stem")

            def evaluated_candidate(candidate, original_path, outputs, bpm, instrument, cancel_check):
                raw = outputs / "candidate-raw.mid"
                cleaned = outputs / "candidate-clean.mid"
                write_events_midi(events, raw, bpm, instrument)
                write_events_midi(events, cleaned, bpm, instrument)
                return {
                    "candidate": StemCandidate("uploaded", candidate.path, {}),
                    "raw_path": raw,
                    "cleaned_path": cleaned,
                    "events": events,
                    "evidence": evidence,
                    "quality": .82,
                }

            with mock.patch.object(
                pipeline_module,
                "analyze_audio",
                return_value=AudioFacts(2.0, 120.0, 0.0, "C", "major", .8),
            ), mock.patch.object(
                pipeline_module,
                "_evaluate_stem_candidate",
                side_effect=evaluated_candidate,
            ), mock.patch.object(
                pipeline_module,
                "review",
                return_value=first_review,
            ), mock.patch.object(
                pipeline_module,
                "refine_lead_sheet",
                return_value=second_review,
            ) as refine_mock, mock.patch.object(
                pipeline_module,
                "render_pdf",
                return_value=False,
            ):
                result = pipeline_module.run_pipeline(
                    root,
                    original,
                    isolated,
                    "Test",
                    "Artist",
                    "tenor",
                    True,
                    True,
                    lambda *args: None,
                )

            self.assertEqual(refine_mock.call_count, 1, result["score_refinement"])
            self.assertEqual(result["score_refinement"]["passes_run"], 2)
            self.assertEqual(result["score_refinement"]["accepted_revisions"], 0)
            self.assertTrue(any(item["role"] == "musicxml" for item in result["files"]))


if __name__ == "__main__":
    unittest.main()
