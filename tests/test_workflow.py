import unittest
import json
import tempfile
from pathlib import Path

from backend import app as backend_app
from backend.saxscribe.settings import settings


class WorkflowRegressionTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
