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
        original = inputs / Path(job["original_object"]).name
        download_object(job["original_object"], original)
        isolated = None
        if job.get("isolated_object"):
            isolated = inputs / Path(job["isolated_object"]).name
            download_object(job["isolated_object"], isolated)

        def progress(stage: str, percent: int, message: str) -> None:
            update_job(job_id, status="running", stage=stage, percent=percent, message=message)

        try:
            result = run_pipeline(
                job_dir,
                original,
                isolated,
                job.get("title", ""),
                job.get("artist", ""),
                job.get("instrument", "tenor"),
                use_ai,
                bool(job.get("highlight_uncertain", True)),
                progress,
                original_display_name=job.get("source_name") or original.name,
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
