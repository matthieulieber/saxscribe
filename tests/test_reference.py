import unittest
import tempfile
import os
import json
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pretty_midi
from backend.saxscribe import reference_api
from backend.saxscribe.settings import Settings

from backend.saxscribe.reference import compare_pitches


class ReferenceEvaluationTests(unittest.TestCase):
    def test_alignment_counts_pitch_errors_without_claiming_rhythm_accuracy(self):
        report = compare_pitches([60, 62, 64, 65], [60, 63, 64])
        self.assertEqual(report["matches"], 2)
        self.assertEqual(report["substitutions"], 1)
        self.assertEqual(report["missing"], 1)
        self.assertIn("timing, rhythm", report["scope"])

    def test_octave_substitution_is_explicit(self):
        report = compare_pitches([60], [72])
        self.assertEqual(report["octave_errors"], 1)

    def test_reference_upload_attach_and_compare_concert_midi(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Settings(work_dir=root / 'jobs', reference_dir=root / 'references')
            app = FastAPI()
            app.include_router(reference_api.router)
            with patch.object(reference_api, 'settings', config), TestClient(app) as client:
                case = client.post('/api/references', data={'title': 'Etude', 'instrument': 'tenor'},
                    files={'audio': ('a.wav', b'audio'), 'score': ('a.pdf', b'%PDF-1.4')}).json()
                case_id = case['id']
                self.assertEqual(client.get(f'/api/references/{case_id}/files/score.pdf').status_code, 200)
                midi = pretty_midi.PrettyMIDI()
                voice = pretty_midi.Instrument(66)
                voice.notes.append(pretty_midi.Note(90, 74, 0, 1))
                midi.instruments.append(voice)
                source = root / 'written.mid'
                midi.write(str(source))
                response = client.post(f'/api/references/{case_id}/notes',
                    data={'verified': 'true', 'pitch_basis': 'written'}, files={'midi': ('a.mid', source.read_bytes())})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['pitches'], [60])
                job_id = 'abcdef123456'
                outputs = config.work_dir / job_id / 'outputs'
                outputs.mkdir(parents=True)
                voice.notes[0].pitch = 60
                midi.write(str(outputs / 'advanced.mid'))
                (outputs.parent / 'job.json').write_text(json.dumps({'status': 'complete', 'result': {'files': [
                    {'role': 'advanced_midi', 'name': 'advanced.mid'}]}}))
                report = client.post(f'/api/references/{case_id}/compare/{job_id}')
                self.assertEqual(report.status_code, 200, report.text)
                self.assertEqual(report.json()['reports']['advanced_midi']['matches'], 1)
                self.assertTrue(list((config.reference_dir / case_id).glob('evaluation-*.json')))

    def test_hosted_reference_access_is_disabled(self):
        app = FastAPI()
        app.include_router(reference_api.router)
        with patch.object(reference_api, 'settings', Settings(runtime_mode='gcp')), TestClient(app) as client:
            self.assertEqual(client.get('/api/references').status_code, 404)

    def test_job_cleanup_preserves_reference_library(self):
        from backend import app as backend_app
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            references = work / 'references'
            references.mkdir()
            expired_job = work / 'abcdef123456'
            expired_job.mkdir()
            os.utime(references, (1, 1))
            os.utime(expired_job, (1, 1))
            with patch.object(backend_app, 'settings', Settings(work_dir=work, reference_dir=references)):
                backend_app._prune()
            self.assertTrue(references.is_dir())
            self.assertFalse(expired_job.exists())


if __name__ == "__main__":
    unittest.main()
