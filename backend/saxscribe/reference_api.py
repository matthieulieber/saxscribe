"""Private local reference library, separate from expiring transcription jobs."""
import json
import re
import shutil
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .reference import WRITTEN_TO_CONCERT, compare_pitches, midi_pitches
from .settings import settings


def local_only():
    if settings.runtime_mode != "local":
        raise HTTPException(404, "Reference library is available in the local app only.")


router = APIRouter(prefix="/api/references", dependencies=[Depends(local_only)])


def folder(case_id):
    if not re.fullmatch(r"[a-f0-9]{32}", case_id):
        raise HTTPException(404, "Reference not found.")
    path = settings.reference_dir / case_id
    if not (path / "manifest.json").is_file():
        raise HTTPException(404, "Reference not found.")
    return path


async def save(upload, target, limit):
    size = 0
    with target.open("wb") as handle:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, "Reference file exceeds the upload limit.")
            handle.write(chunk)
    if not size:
        raise HTTPException(400, "Reference files must not be empty.")


@router.get("")
def listing():
    return [json.loads(p.read_text()) for p in sorted(settings.reference_dir.glob("*/manifest.json"))]


@router.post("")
async def create(title: str = Form(...), instrument: str = Form("tenor"),
                 audio: UploadFile = File(...), score: UploadFile = File(...)):
    if instrument not in WRITTEN_TO_CONCERT or not title.strip() or len(title) > 200:
        raise HTTPException(400, "Provide a title and supported instrument.")
    from pathlib import Path
    suffix = Path(audio.filename or "").suffix.lower()
    if suffix not in {".aif", ".aiff", ".wav", ".mp3", ".m4a", ".flac", ".ogg"}:
        raise HTTPException(400, "Choose an audio recording.")
    case_id = uuid.uuid4().hex
    path = settings.reference_dir / case_id
    path.mkdir(parents=True)
    try:
        await save(audio, path / ("audio" + suffix), settings.max_upload_mb * 1024 * 1024)
        await save(score, path / "score.pdf", 20 * 1024 * 1024)
        with (path / "score.pdf").open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise HTTPException(400, "The score must be a PDF.")
        manifest = {"id": case_id, "title": title.strip(), "instrument": instrument,
                    "audio": "audio" + suffix, "status": "awaiting_verified_midi"}
        (path / "manifest.json").write_text(json.dumps(manifest, indent=2))
        return manifest
    except Exception:
        shutil.rmtree(path, ignore_errors=True)
        raise


@router.get("/{case_id}/files/{name}")
def download(case_id: str, name: str):
    path = folder(case_id)
    manifest = json.loads((path / "manifest.json").read_text())
    if name not in {manifest["audio"], "score.pdf"}:
        raise HTTPException(404, "File not found.")
    return FileResponse(path / name)


@router.post("/{case_id}/notes")
async def notes(case_id: str, midi: UploadFile = File(...), pitch_basis: str = Form(...),
                verified: bool = Form(False)):
    path = folder(case_id)
    manifest = json.loads((path / "manifest.json").read_text())
    if not verified or pitch_basis not in {"written", "concert"}:
        raise HTTPException(400, "Confirm the MIDI matches the PDF and select its pitch basis.")
    temp = path / (uuid.uuid4().hex + ".mid")
    try:
        await save(midi, temp, 2 * 1024 * 1024)
        pitches = midi_pitches(temp, manifest["instrument"] if pitch_basis == "written" else "concert")
        manifest.update(status="ready", pitches=pitches, pitch_basis=pitch_basis)
        replacement = path / (uuid.uuid4().hex + ".json")
        replacement.write_text(json.dumps(manifest, indent=2))
        replacement.replace(path / "manifest.json")
        return manifest
    except (ValueError, OSError, EOFError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        temp.unlink(missing_ok=True)


@router.post("/{case_id}/compare/{job_id}")
def compare(case_id: str, job_id: str):
    path = folder(case_id)
    manifest = json.loads((path / "manifest.json").read_text())
    if manifest["status"] != "ready":
        raise HTTPException(409, "Add verified reference MIDI before comparing.")
    if not re.fullmatch(r"[a-f0-9]{12}", job_id):
        raise HTTPException(404, "Job not found.")
    job_path = settings.work_dir / job_id
    job_file = job_path / "job.json"
    if not job_file.is_file():
        raise HTTPException(404, "Job not found or expired.")
    job = json.loads(job_file.read_text())
    if job.get("status") != "complete":
        raise HTTPException(409, "Finish transcription first.")
    reports = {}
    for item in job.get("result", {}).get("files", []):
        if item["role"] in {"simple_midi", "advanced_midi"}:
            try:
                # Pipeline MIDI is concert pitch; only MusicXML is transposed.
                pitches = midi_pitches(job_path / "outputs" / item["name"])
                reports[item["role"]] = compare_pitches(manifest["pitches"], pitches)
            except (ValueError, OSError) as exc:
                raise HTTPException(400, str(exc)) from exc
    if not reports:
        raise HTTPException(409, "No transcription MIDI is available.")
    result = {"job_id": job_id, "created_at": datetime.now(timezone.utc).isoformat(), "reports": reports}
    (path / ("evaluation-" + uuid.uuid4().hex + ".json")).write_text(json.dumps(result, indent=2))
    return result
