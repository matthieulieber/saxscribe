from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from .settings import settings


def _validate() -> None:
    missing = [
        name
        for name, value in {
            "GOOGLE_CLOUD_PROJECT": settings.gcp_project,
            "GCP_BUCKET": settings.gcp_bucket,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing hosted-runtime settings: {', '.join(missing)}")


def _clients():
    try:
        from google.cloud import firestore, storage
    except ImportError as exc:
        raise RuntimeError("Google Cloud dependencies are missing. Install backend/requirements-cloud.txt.") from exc
    _validate()
    return firestore.Client(project=settings.gcp_project), storage.Client(project=settings.gcp_project)


def _document(job_id: str):
    firestore_client, _ = _clients()
    return firestore_client.collection(settings.firestore_collection).document(job_id)


def create_job(job_id: str, metadata: dict, original_path: Path, isolated_path: Path | None) -> None:
    firestore_client, storage_client = _clients()
    bucket = storage_client.bucket(settings.gcp_bucket)
    original_object = f"jobs/{job_id}/inputs/{original_path.name}"
    bucket.blob(original_object).upload_from_filename(str(original_path))
    isolated_object = None
    if isolated_path:
        isolated_object = f"jobs/{job_id}/inputs/{isolated_path.name}"
        bucket.blob(isolated_object).upload_from_filename(str(isolated_path))
    now = datetime.now(timezone.utc).isoformat()
    firestore_client.collection(settings.firestore_collection).document(job_id).set(
        {
            **metadata,
            "id": job_id,
            "status": "queued",
            "stage": "queued",
            "percent": 0,
            "message": "Waiting for a processing worker",
            "original_object": original_object,
            "isolated_object": isolated_object,
            "created_at": now,
            "updated_at": now,
        }
    )


def dispatch_job(job_id: str) -> None:
    try:
        from google.cloud import run_v2
    except ImportError as exc:
        raise RuntimeError("google-cloud-run is required to dispatch Cloud Run Jobs.") from exc
    _validate()
    client = run_v2.JobsClient()
    name = client.job_path(settings.gcp_project, settings.gcp_region, settings.gcp_job_name)
    client.run_job(
        request={
            "name": name,
            "overrides": {
                "container_overrides": [
                    {"env": [{"name": "SAXSCRIBE_JOB_ID", "value": job_id}]}
                ]
            },
        }
    )


def get_job(job_id: str) -> dict | None:
    snapshot = _document(job_id).get()
    return snapshot.to_dict() if snapshot.exists else None


def update_job(job_id: str, **changes) -> None:
    _document(job_id).set(
        {**changes, "updated_at": datetime.now(timezone.utc).isoformat()},
        merge=True,
    )


def download_object(object_name: str, target: Path) -> None:
    _, storage_client = _clients()
    target.parent.mkdir(parents=True, exist_ok=True)
    storage_client.bucket(settings.gcp_bucket).blob(object_name).download_to_filename(str(target))


def upload_outputs(job_id: str, output_dir: Path) -> None:
    _, storage_client = _clients()
    bucket = storage_client.bucket(settings.gcp_bucket)
    for path in output_dir.iterdir():
        if path.is_file():
            bucket.blob(f"jobs/{job_id}/outputs/{path.name}").upload_from_filename(str(path))


def open_output(job_id: str, filename: str) -> tuple[BinaryIO, int, str]:
    _, storage_client = _clients()
    blob = storage_client.bucket(settings.gcp_bucket).blob(f"jobs/{job_id}/outputs/{filename}")
    if not blob.exists():
        raise FileNotFoundError(filename)
    blob.reload()
    return blob.open("rb"), int(blob.size or 0), blob.content_type or "application/octet-stream"
