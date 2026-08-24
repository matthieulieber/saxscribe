from __future__ import annotations

import re
import os
import json
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

from .saxscribe.pipeline import JobCancelled, run_pipeline  # noqa: E402
from .saxscribe.settings import settings  # noqa: E402
from .saxscribe import billing  # noqa: E402


app = FastAPI(title="SaxScribe API", version="0.12.0")
APP_BUILD = "0.12.0"
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["*"] ,
    allow_headers=["*"],
)

executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="saxscribe")
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
cancel_events: dict[str, threading.Event] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_name(name: str | None, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name or fallback).name).strip(".-")
    return cleaned or fallback


def _set_job(job_id: str, **changes) -> None:
    with jobs_lock:
        jobs[job_id].update(changes, updated_at=_now())
        _persist_job_locked(job_id)


def _persist_job_locked(job_id: str) -> None:
    path = settings.work_dir / job_id / "job.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(jobs[job_id], indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _load_local_jobs() -> None:
    with jobs_lock:
        for path in settings.work_dir.glob("*/job.json"):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            job_id = str(item.get("id") or path.parent.name)
            if item.get("status") in {"queued", "running", "cancelling"}:
                item.update(
                    status="error",
                    stage="error",
                    message="The local server restarted before this job finished. Start it again.",
                    error="Interrupted by a local server restart.",
                    updated_at=_now(),
                )
            jobs[job_id] = item
            cancel_events[job_id] = threading.Event()
            _persist_job_locked(job_id)


def _progress(job_id: str):
    def callback(stage: str, percent: int, message: str) -> None:
        _set_job(job_id, status="running", stage=stage, percent=percent, message=message)
    return callback


def _run(
    job_id: str,
    job_dir: Path,
    original: Path,
    isolated: Path | None,
    title: str,
    artist: str,
    instrument: str,
    use_ai: bool,
    separation_provider: str,
    highlight_uncertain: bool,
    original_name: str,
    isolated_name: str | None,
    cancel_event: threading.Event,
) -> None:
    try:
        result = run_pipeline(
            job_dir,
            original,
            isolated,
            title,
            artist,
            instrument,
            use_ai,
            highlight_uncertain,
            _progress(job_id),
            original_display_name=original_name,
            isolated_display_name=isolated_name,
            cancel_check=cancel_event.is_set,
            separation_provider=separation_provider,
        )
        _set_job(job_id, status="complete", stage="complete", percent=100, message="Your score is ready", result=result)
    except JobCancelled:
        _set_job(job_id, status="cancelled", stage="cancelled", percent=0, message="Processing cancelled")
    except Exception as exc:
        _set_job(job_id, status="error", stage="error", message=str(exc), error=str(exc))


def _prune() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.keep_jobs_hours)
    if not settings.work_dir.exists():
        return
    for path in settings.work_dir.iterdir():
        if path.is_dir() and datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff:
            shutil.rmtree(path, ignore_errors=True)


@app.on_event("startup")
def startup() -> None:
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    _prune()
    if settings.runtime_mode == "local":
        _load_local_jobs()


async def _save_upload(upload: UploadFile, target: Path, max_bytes: int) -> None:
    size = 0
    with target.open("wb") as handle:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                raise HTTPException(413, f"File exceeds {settings.max_upload_mb} MB.")
            handle.write(chunk)


@app.get("/api/health")
def health() -> dict:
    model_path = Path(settings.uvr_model_dir) / settings.uvr_model_name
    uvr_ready = model_path.is_file()
    lalal_ready = bool(settings.lalal_api_key)
    if settings.separation_provider == "uvr":
        separation_ready = uvr_ready
    elif settings.separation_provider == "lalal":
        separation_ready = lalal_ready
    else:
        separation_ready = uvr_ready or lalal_ready
    return {
        "ok": True,
        "build": APP_BUILD,
        "runtime_mode": settings.runtime_mode,
        "uvr_model": settings.uvr_model_name,
        "uvr_target_stem": settings.uvr_target_stem,
        "uvr_output_label": settings.uvr_output_label,
        "uvr_vr_window_size": settings.uvr_vr_window_size,
        "uvr_vr_aggression": settings.uvr_vr_aggression,
        "uvr_vr_high_end_process": settings.uvr_vr_high_end_process,
        "uvr_device": "cpu" if settings.uvr_force_cpu else "automatic",
        "uvr_resampler": settings.uvr_resampler,
        "strict_uvr_model": settings.separation_provider == "uvr",
        "uvr_model_ready": uvr_ready,
        "uvr_model_sha256": settings.uvr_model_sha256,
        "separation_provider": settings.separation_provider,
        "separation_primary": settings.separation_primary,
        "separation_quality_threshold": settings.separation_quality_threshold,
        "separation_ready": separation_ready,
        "lalal_ready": lalal_ready,
        "lalal_splitter": settings.lalal_splitter,
        "ai_required": settings.runtime_mode == "gcp",
        "ai_ready": bool(os.getenv("OPENAI_API_KEY")),
        "hosted_plans": settings.runtime_mode == "gcp",
        "free_ready": uvr_ready,
        "enhanced_ready": (
            settings.runtime_mode == "gcp"
            and lalal_ready
            and bool(os.getenv("OPENAI_API_KEY"))
            and billing.hosted_enhanced_ready()
        ),
        "billing_ready": billing.hosted_enhanced_ready(),
        "reasoning_model": settings.openai_model,
        "audio_model": settings.openai_audio_model,
    }


@app.get("/api/billing/config")
def billing_config() -> dict:
    return billing.public_billing_config()


@app.post("/api/billing/checkout")
def create_checkout() -> dict:
    if settings.runtime_mode != "gcp":
        raise HTTPException(404, "Paid checkout is available only on the hosted website.")
    if not settings.lalal_api_key or not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(503, "Enhanced processing is not fully configured.")
    try:
        return billing.create_enhanced_checkout()
    except billing.BillingError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/billing/session/{session_id}")
def get_checkout_session(session_id: str) -> dict:
    if settings.runtime_mode != "gcp":
        raise HTTPException(404, "Paid checkout is available only on the hosted website.")
    try:
        paid = billing.verify_paid_enhanced_checkout(session_id)
    except billing.BillingError as exc:
        raise HTTPException(402, str(exc)) from exc
    from .saxscribe.gcp_runtime import get_checkout_claimed_job

    if get_checkout_claimed_job(session_id):
        raise HTTPException(409, "This Enhanced payment has already been used.")
    return {
        "paid": True,
        "plan": "enhanced",
        "amount_total": paid.amount_total,
        "currency": paid.currency,
    }


@app.post("/api/billing/webhook")
async def stripe_webhook(request: Request) -> dict:
    if settings.runtime_mode != "gcp":
        raise HTTPException(404, "Stripe webhooks are available only in hosted mode.")
    signature = request.headers.get("stripe-signature", "")
    if not signature:
        raise HTTPException(400, "Missing Stripe-Signature header.")
    try:
        event = billing.construct_webhook_event(await request.body(), signature)
    except billing.BillingError as exc:
        raise HTTPException(400, str(exc)) from exc
    if event.get("type") == "checkout.session.completed":
        from .saxscribe.gcp_runtime import record_checkout_event

        record_checkout_event(event["data"]["object"])
    return {"received": True}


@app.post("/api/jobs")
async def create_job(
    original: UploadFile = File(...),
    isolated: UploadFile | None = File(None),
    title: str = Form(""),
    artist: str = Form(""),
    instrument: str = Form("tenor"),
    plan: str = Form("free"),
    checkout_session_id: str = Form(""),
    highlight_uncertain: bool = Form(True),
) -> dict:
    if instrument not in {"concert", "soprano", "tenor", "alto", "baritone"}:
        raise HTTPException(400, "Unsupported saxophone selection.")
    if plan not in {"free", "enhanced"}:
        raise HTTPException(400, "Unsupported transcription plan.")

    paid_checkout = None
    if settings.runtime_mode == "local":
        if plan != "free":
            raise HTTPException(400, "Local SaxScribe supports only the free UVR workflow.")
        use_ai = False
        separation_provider = "uvr"
    elif settings.runtime_mode == "gcp":
        if plan == "free":
            use_ai = False
            separation_provider = "uvr"
        else:
            if isolated and isolated.filename:
                raise HTTPException(400, "Enhanced uses LALAL.AI separation; remove the pre-isolated stem.")
            if not settings.lalal_api_key:
                raise HTTPException(503, "LALAL_API_KEY is required for Enhanced processing.")
            if not os.getenv("OPENAI_API_KEY"):
                raise HTTPException(503, "OPENAI_API_KEY is required for Enhanced AI review.")
            try:
                paid_checkout = billing.verify_paid_enhanced_checkout(checkout_session_id.strip())
            except billing.BillingError as exc:
                raise HTTPException(402, str(exc)) from exc
            use_ai = True
            separation_provider = "lalal"
    else:
        raise HTTPException(500, f"Unsupported RUNTIME_MODE: {settings.runtime_mode}")
    job_id = uuid.uuid4().hex[:12]
    job_dir = settings.work_dir / job_id
    inputs = job_dir / "inputs"
    inputs.mkdir(parents=True)

    original_name = Path(original.filename or "original.wav").name
    isolated_name = Path(isolated.filename).name if isolated and isolated.filename else None
    original_path = inputs / _safe_name(original_name, "original.wav")
    max_bytes = settings.max_upload_mb * 1024 * 1024
    isolated_path = None
    try:
        await _save_upload(original, original_path, max_bytes)
        if isolated and isolated.filename:
            isolated_path = inputs / _safe_name(isolated.filename, "isolated.wav")
            await _save_upload(isolated, isolated_path, max_bytes)
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise

    if settings.runtime_mode == "gcp":
        from .saxscribe import gcp_runtime

        metadata = {
            "title": title.strip(),
            "artist": artist.strip(),
            "instrument": instrument,
            "plan": plan,
            "separation_provider": separation_provider,
            "use_ai": use_ai,
            "payment_session_id": paid_checkout.session_id if paid_checkout else None,
            "highlight_uncertain": highlight_uncertain,
            "source_name": original_name,
            "isolated_source_name": isolated_name,
        }
        payment_claimed = False
        try:
            if paid_checkout:
                gcp_runtime.claim_checkout_session(paid_checkout, job_id)
                payment_claimed = True
            gcp_runtime.create_job(job_id, metadata, original_path, isolated_path)
            gcp_runtime.dispatch_job(job_id)
        except Exception as exc:
            try:
                gcp_runtime.delete_job(job_id)
            except Exception:
                pass
            if payment_claimed:
                try:
                    gcp_runtime.release_checkout_session(paid_checkout.session_id, job_id)
                except Exception:
                    pass
            shutil.rmtree(job_dir, ignore_errors=True)
            if isinstance(exc, gcp_runtime.CheckoutAlreadyUsed):
                raise HTTPException(409, str(exc)) from exc
            raise
        shutil.rmtree(job_dir, ignore_errors=True)
        return {
            "id": job_id,
            "instrument": instrument,
            "plan": plan,
            "use_ai": use_ai,
            "highlight_uncertain": highlight_uncertain,
            "source_name": original_name,
            "isolated_source_name": isolated_name,
        }

    with jobs_lock:
        jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "stage": "queued",
            "percent": 0,
            "message": "Waiting for the local processing worker",
            "source_name": original_name,
            "isolated_source_name": isolated_name,
            "instrument": instrument,
            "plan": plan,
            "separation_provider": separation_provider,
            "use_ai": use_ai,
            "highlight_uncertain": highlight_uncertain,
            "created_at": _now(),
            "updated_at": _now(),
        }
        cancel_event = threading.Event()
        cancel_events[job_id] = cancel_event
        _persist_job_locked(job_id)
    executor.submit(
        _run,
        job_id,
        job_dir,
        original_path,
        isolated_path,
        title.strip(),
        artist.strip(),
        instrument,
        use_ai,
        separation_provider,
        highlight_uncertain,
        original_name,
        isolated_name,
        cancel_event,
    )
    return {
        "id": job_id,
        "instrument": instrument,
        "plan": plan,
        "use_ai": use_ai,
        "highlight_uncertain": highlight_uncertain,
        "source_name": original_name,
        "isolated_source_name": isolated_name,
    }


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    if settings.runtime_mode != "local":
        raise HTTPException(409, "Cancellation is currently available only for local jobs.")
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found.")
        if job.get("status") in {"complete", "error", "cancelled"}:
            return dict(job)
        event = cancel_events.setdefault(job_id, threading.Event())
        event.set()
        job.update(status="cancelling", message="Stopping the active process", updated_at=_now())
        _persist_job_locked(job_id)
        return dict(job)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    if settings.runtime_mode == "gcp":
        from .saxscribe.gcp_runtime import get_job as get_gcp_job

        job = get_gcp_job(job_id)
        if not job:
            raise HTTPException(404, "Job not found. It may have expired.")
        result = dict(job)
        result.pop("payment_session_id", None)
        if result.get("result"):
            for item in result["result"]["files"]:
                item["url"] = f"/api/jobs/{job_id}/files/{quote(item['name'], safe='')}"
        return result
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found. It may have expired after a server restart.")
        result = dict(job)
    if result.get("result"):
        for item in result["result"]["files"]:
            item["url"] = f"/api/jobs/{job_id}/files/{quote(item['name'], safe='')}"
    return result


@app.get("/api/jobs/{job_id}/files/{filename}")
def download(job_id: str, filename: str):
    if filename != Path(filename).name or filename in {".", ".."}:
        raise HTTPException(404, "File not found.")
    if settings.runtime_mode == "gcp":
        from .saxscribe.gcp_runtime import open_output

        try:
            handle, size, content_type = open_output(job_id, filename)
        except FileNotFoundError:
            raise HTTPException(404, "File not found.")
        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(size),
        }
        return StreamingResponse(handle, media_type=content_type, headers=headers)

    path = settings.work_dir / job_id / "outputs" / filename
    if not path.exists() or path.parent.resolve() != (settings.work_dir / job_id / "outputs").resolve():
        raise HTTPException(404, "File not found.")
    download_name = filename
    with jobs_lock:
        job = jobs.get(job_id, {})
        for item in job.get("result", {}).get("files", []):
            if item.get("name") == filename:
                download_name = item.get("download_name") or filename
                break
    return FileResponse(path, filename=download_name)


FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
