import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendContractTests(unittest.TestCase):
    def test_completed_job_has_lazy_musicxml_viewer(self):
        source = (ROOT / "frontend" / "src" / "App.jsx").read_text()
        self.assertIn("View generated sheet music", source)
        self.assertIn("import('opensheetmusicdisplay')", source)
        self.assertIn("renderer.load(musicXml)", source)
        self.assertIn("Playable lead sheet", source)
        self.assertIn("Detailed performance", source)
        self.assertIn("Download MusicXML", source)
        self.assertIn("Review playback speed", source)
        self.assertIn("coarsest standard grid that keeps distinct supported notes", source)
        self.assertNotIn("Cancel job", source)
        self.assertNotIn("<small>Separator</small>", source)
        self.assertNotIn("health.uvr_model", source)
        self.assertNotIn("health.lalal_ready", source)
        self.assertNotIn("health.ai_ready", source)

    def test_score_renderer_version_is_pinned(self):
        package = json.loads((ROOT / "frontend" / "package.json").read_text())
        self.assertEqual(package["dependencies"]["opensheetmusicdisplay"], "2.1.2")

    def test_public_api_hides_processing_internals(self):
        app_source = (ROOT / "backend" / "app.py").read_text()
        billing_source = (ROOT / "backend" / "saxscribe" / "billing.py").read_text()
        health_source = app_source.split('@app.get("/api/health")', 1)[1].split('@app.get("/api/billing/config")', 1)[0]
        self.assertNotIn('"uvr_model"', health_source)
        self.assertNotIn('"separation_provider"', health_source)
        self.assertNotIn('"reasoning_model"', health_source)
        self.assertIn('if key in PUBLIC_JOB_FIELDS', app_source)
        self.assertIn('if key in PUBLIC_RESULT_FIELDS', app_source)
        self.assertIn('"score_refinement",', app_source)
        self.assertIn('item.get("role") not in PUBLIC_FILE_ROLES', app_source)
        self.assertNotIn('"separator": "uvr"', billing_source)
        self.assertNotIn('"separator": "lalal"', billing_source)


if __name__ == "__main__":
    unittest.main()
