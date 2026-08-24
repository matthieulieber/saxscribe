import json
import os
import shutil
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from backend.saxscribe.analysis import AudioFacts
from backend.saxscribe.llm_review import AIReviewError, review


STRUCTURED = {
    "summary": "Audio, MIDI, and MusicXML agree after one conservative review.",
    "researched_song": {
        "matched_recording": "Test Artist — Test Song",
        "concert_key": "G",
        "mode": "minor",
        "bpm": 103,
        "meter": "4/4",
        "confidence": 0.9,
    },
    "selected_context": {
        "concert_key": "G",
        "mode": "minor",
        "bpm": 103,
        "meter": "4/4",
        "reason": "The upload measurements and matching recording evidence agree.",
    },
    "corrections": [],
}


class FakeResponse:
    def __init__(self, text, payload=None):
        self.output_text = text
        self._payload = payload or {}

    def model_dump(self):
        return self._payload


class FakeResponses:
    def __init__(self, structured_text=None):
        self.calls = []
        self.structured_text = structured_text or json.dumps(STRUCTURED)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("tools"):
            return FakeResponse(
                "The matching recording is reported around 103 BPM in G minor.",
                {
                    "output": [{
                        "content": [{
                            "annotations": [{
                                "type": "url_citation",
                                "url": "https://example.com/song",
                                "title": "Song evidence",
                            }]
                        }]
                    }]
                },
            )
        return FakeResponse(self.structured_text)


class FakeChatCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content="The stem follows the audible sax phrase; no obvious octave mismatch.", audio=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    def __init__(self, structured_text=None):
        self.responses = FakeResponses(structured_text)
        self.chat = SimpleNamespace(completions=FakeChatCompletions())


def write_silence(path: Path):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\0\0" * 4000)


class AIReviewTests(unittest.TestCase):
    def _fixtures(self, directory: str):
        root = Path(directory)
        original = root / "original.wav"
        wind = root / "wind.wav"
        xml = root / "draft.musicxml"
        write_silence(original)
        write_silence(wind)
        xml.write_text("<score-partwise version=\"4.0\"><part-list/></score-partwise>", encoding="utf-8")
        events = [{"index": 0, "pitch": 67, "start_beat": 0, "duration_beats": 1, "velocity": 88}]
        evidence = {
            "notes": [{
                "index": 0,
                "candidate_pitch": 67,
                "start_seconds": 0,
                "end_seconds": 0.5,
                "pitch_error_cents": 4,
                "mean_voiced_probability": 0.91,
            }]
        }
        facts = AudioFacts(0.25, 103, 0, "G", "minor", 0.8)
        return original, wind, xml, events, evidence, facts

    def test_missing_key_is_a_hard_failure(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(AIReviewError, "OPENAI_API_KEY is missing"):
                review(
                    title="", artist="", facts=AudioFacts(1, 120, 0, "C", "major", 0.5),
                    original_path=Path("original.wav"), wind_path=Path("wind.wav"),
                    raw_events=[], cleaned_events=[], draft_xml_path=Path("draft.xml"),
                    transcription_evidence={}, model="gpt-5-mini", audio_model="gpt-audio-1.5",
                    audio_chunk_seconds=60, audio_max_chunks=8,
                )

    def test_required_review_sends_both_recordings_and_synthesizes_all_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            original, wind, xml, events, evidence, facts = self._fixtures(directory)
            client = FakeClient()

            def copy_review_audio(source, target, start, duration):
                shutil.copyfile(source, target)

            with mock.patch("backend.saxscribe.llm_review.shutil.which", return_value="ffmpeg"), \
                 mock.patch("backend.saxscribe.llm_review._make_review_wav", side_effect=copy_review_audio):
                result = review(
                    title="Test Song", artist="Test Artist", facts=facts,
                    original_path=original, wind_path=wind,
                    raw_events=events, cleaned_events=events, draft_xml_path=xml,
                    transcription_evidence=evidence, model="gpt-5-mini", audio_model="gpt-audio-1.5",
                    audio_chunk_seconds=60, audio_max_chunks=8, client=client,
                )

            self.assertTrue(result.used)
            self.assertEqual(result.sources[0]["url"], "https://example.com/song")
            self.assertEqual(len(client.responses.calls), 2)
            audio_call = client.chat.completions.calls[0]
            parts = audio_call["messages"][0]["content"]
            self.assertEqual(sum(part["type"] == "input_audio" for part in parts), 2)
            structured_prompt = client.responses.calls[-1]["input"]
            self.assertIn("Raw transcription MIDI events", structured_prompt)
            self.assertIn("Draft MusicXML (exact text)", structured_prompt)
            self.assertIn("Local original/stem/note comparison", structured_prompt)

    def test_malformed_ai_result_stops_instead_of_falling_back(self):
        with tempfile.TemporaryDirectory() as directory:
            original, wind, xml, events, evidence, facts = self._fixtures(directory)
            client = FakeClient(structured_text="not json")
            with mock.patch("backend.saxscribe.llm_review._compare_audio", return_value=[{"memo": "ok"}]):
                with self.assertRaisesRegex(AIReviewError, "structured MIDI/MusicXML synthesis"):
                    review(
                        title="Test Song", artist="Test Artist", facts=facts,
                        original_path=original, wind_path=wind,
                        raw_events=events, cleaned_events=events, draft_xml_path=xml,
                        transcription_evidence=evidence, model="gpt-5-mini", audio_model="gpt-audio-1.5",
                        audio_chunk_seconds=60, audio_max_chunks=8, client=client,
                    )

    def test_frontend_ai_review_is_optional_and_defaults_off(self):
        source = (Path(__file__).parents[1] / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertIn("const [useAi, setUseAi] = useState(false)", source)
        self.assertIn("const [highlightUncertain, setHighlightUncertain] = useState(true)", source)
        self.assertIn("Extra transcription check", source)
        self.assertIn("Adds processing time and API cost", source)
        self.assertIn("audio excerpts are sent to OpenAI", source)
        self.assertIn("data.append('use_ai', String(useAi))", source)
        self.assertIn("data.append('highlight_uncertain', String(highlightUncertain))", source)
        self.assertIn("Most notes lack support from the horn stem", source)
        self.assertIn("It can add a missing note or repair an octave only when local pYIN measurements support", source)
        self.assertIn("saxscribe-active-job", source)
        self.assertIn("Cancel job", source)
        self.assertLess(source.index("id: 'comparing'"), source.index("id: 'cleaning'"))


if __name__ == "__main__":
    unittest.main()
