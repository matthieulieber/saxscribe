from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from backend.saxscribe.separation import LalalSeparatorProvider, candidate_quality, compare_event_sets


class FakeResponse:
    def __init__(self, payload=None, body: bytes = b"", status_code: int = 200):
        self._payload = payload or {}
        self.raw = io.BytesIO(body)
        self.status_code = status_code
        self.ok = status_code < 400
        self.reason = "error" if not self.ok else "ok"
        self.text = ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(self.reason)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if url.endswith("/upload/"):
            return FakeResponse({"id": "source-1"})
        if url.endswith("/split/stem_separator/"):
            return FakeResponse({"task_id": "task-1"})
        if url.endswith("/check/"):
            return FakeResponse(
                {
                    "result": {
                        "task-1": {
                            "status": "success",
                            "result": {
                                "tracks": [
                                    {"type": "stem", "label": "wind", "url": "https://download.test/wind"},
                                    {"type": "back", "label": "no_wind", "url": "https://download.test/back"},
                                ]
                            },
                        }
                    }
                }
            )
        if url.endswith("/delete/"):
            return FakeResponse({})
        raise AssertionError(url)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return FakeResponse(body=b"RIFFfake-wav")


class SeparationTests(unittest.TestCase):
    def test_lalal_api_v1_wind_flow(self):
        session = FakeSession()
        provider = LalalSeparatorProvider(api_key="test-key", session=session, poll_seconds=1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "demo.m4a"
            source.write_bytes(b"audio")
            result = provider.separate(source, root, "My Demo.m4a")
            self.assertEqual(result.provider, "lalal")
            self.assertEqual(result.path.read_bytes(), b"RIFFfake-wav")
            split_call = next(call for call in session.calls if call[1].endswith("/split/stem_separator/"))
            self.assertEqual(split_call[2]["json"]["presets"]["stem"], "wind")
            self.assertEqual(split_call[2]["json"]["presets"]["splitter"], "phoenix")
            self.assertTrue(any(call[1].endswith("/delete/") for call in session.calls))

    def test_candidate_quality_is_conservative(self):
        evidence = {
            "candidate_note_count": 10,
            "high_confidence_note_count": 8,
            "high_confidence_pitch_agreement": 0.9,
        }
        self.assertEqual(candidate_quality(evidence), 0.855)

    def test_note_level_agreement(self):
        left = [
            {"start_beat": 1.0, "pitch": 60},
            {"start_beat": 2.0, "pitch": 62},
        ]
        right = [
            {"start_beat": 1.1, "pitch": 60},
            {"start_beat": 2.1, "pitch": 67},
        ]
        result = compare_event_sets(left, right)
        self.assertEqual(result["matched_notes"], 1)
        self.assertEqual(result["agreement"], 0.5)


if __name__ == "__main__":
    unittest.main()
