from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .saxscribe.gcp_runtime import (
    download_object,
    get_job,
    release_checkout_session,
    update_job,
    upload_outputs,
)
from .saxscribe.pipeline import run_pipeline
from .saxscribe.settings import settings
from .saxscribe.youtube import download_youtube_audio


def main() -> None:
    job_id = os.getenv("SAXSCRIBE_JOB_ID", "").strip()
    if not job_id:
        raise RuntimeError("SAXSCRIBE_JOB_ID is required.")
    job = get_job(job_id)
    if not job:
        raise RuntimeError(f"Hosted job {job_id} does not exist.")
    plan = job.get("plan", "free")
    if plan not in {"free", "enhanced"}:
        raise RuntimeError(f"Hosted job {job_id} has an invalid plan.")
    if plan == "enhanced" and job.get("isolated_object"):
        raise RuntimeError("Enhanced jobs must use LALAL.AI separation, not an uploaded stem.")
    use_ai = plan == "enhanced"
    separation_provider = "lalal" if plan == "enhanced" else "uvr"

    with tempfile.TemporaryDirectory(prefix=f"saxscribe-{job_id}-") as directory:
        job_dir = Path(directory)
        inputs = job_dir / "inputs"
        inputs.mkdir(parents=True, exist_ok=True)
        original = None

        def progress(stage: str, percent: int, message: str) -> None:
            update_job(job_id, status="running", stage=stage, percent=percent, message=message)

        try:
            if job.get("youtube_url"):
                source = download_youtube_audio(
                    job["youtube_url"],
                    inputs,
                    max_bytes=settings.max_upload_mb * 1024 * 1024,
                    max_seconds=settings.max_youtube_seconds,
                    progress=progress,
                )
                original = source.path
                source_name = f"YouTube · {source.title}"
                title = job.get("title") or source.title
                artist = job.get("artist") or source.artist
                update_job(
                    job_id,
                    source_name=source_name,
                    youtube_video_id=source.video_id,
                    youtube_duration_seconds=source.duration_seconds,
                )
            elif job.get("original_object"):
                original = inputs / Path(job["original_object"]).name
                download_object(job["original_object"], original)
                source_name = job.get("source_name") or original.name
                title = job.get("title", "")
                artist = job.get("artist", "")
            else:
                raise RuntimeError("Hosted job has neither an uploaded recording nor a YouTube link.")
            isolated = None
            if job.get("isolated_object"):
                isolated = inputs / Path(job["isolated_object"]).name
                download_object(job["isolated_object"], isolated)

            result = run_pipeline(
                job_dir,
                original,
                isolated,
                title,
                artist,
                job.get("instrument", "tenor"),
                use_ai,
                bool(job.get("highlight_uncertain", True)),
                progress,
                original_display_name=source_name,
                isolated_display_name=job.get("isolated_source_name"),
                separation_provider=separation_provider,
            )
            upload_outputs(job_id, job_dir / "outputs")
            update_job(
                job_id,
                status="complete",
                stage="complete",
                percent=100,
                message="Your score is ready",
                result=result,
            )
        except Exception as exc:
            if plan == "enhanced" and job.get("payment_session_id"):
                try:
                    release_checkout_session(job["payment_session_id"], job_id)
                except Exception:
                    pass
            update_job(job_id, status="error", stage="error", message=str(exc), error=str(exc))
            raise


if __name__ == "__main__":
    main()
