from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from .billing import PaidCheckout
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


def _payment_document(session_id: str):
    firestore_client, _ = _clients()
    return firestore_client.collection(settings.firestore_payments_collection).document(session_id)


class CheckoutAlreadyUsed(RuntimeError):
    pass


def get_checkout_claimed_job(session_id: str) -> str | None:
    snapshot = _payment_document(session_id).get()
    if not snapshot.exists:
        return None
    return (snapshot.to_dict() or {}).get("claimed_job_id")


def record_checkout_event(session) -> None:
    metadata = session.get("metadata") or {}
    if metadata.get("saxscribe_plan") != "enhanced":
        return
    session_id = str(session.get("id") or "")
    if not session_id:
        return
    _payment_document(session_id).set(
        {
            "session_id": session_id,
            "payment_status": session.get("payment_status"),
            "status": session.get("status"),
            "amount_total": session.get("amount_total"),
            "currency": session.get("currency"),
            "payment_intent_id": session.get("payment_intent"),
            "plan": "enhanced",
            "checkout_completed_at": datetime.now(timezone.utc).isoformat(),
        },
        merge=True,
    )


def claim_checkout_session(payment: PaidCheckout, job_id: str) -> None:
    try:
        from google.cloud import firestore
    except ImportError as exc:
        raise RuntimeError("google-cloud-firestore is required for hosted billing.") from exc
    firestore_client, _ = _clients()
    reference = firestore_client.collection(settings.firestore_payments_collection).document(
        payment.session_id
    )
    transaction = firestore_client.transaction()

    @firestore.transactional
    def claim(current_transaction) -> None:
        snapshot = reference.get(transaction=current_transaction)
        existing = snapshot.to_dict() if snapshot.exists else {}
        claimed_job_id = existing.get("claimed_job_id")
        if claimed_job_id and claimed_job_id != job_id:
            raise CheckoutAlreadyUsed(
                "This Enhanced payment has already been used for another transcription."
            )
        current_transaction.set(
            reference,
            {
                **payment.to_record(),
                "plan": "enhanced",
                "payment_status": "paid",
                "claimed_job_id": job_id,
                "claimed_at": datetime.now(timezone.utc).isoformat(),
            },
            merge=True,
        )

    claim(transaction)


def release_checkout_session(session_id: str, job_id: str) -> None:
    try:
        from google.cloud import firestore
    except ImportError as exc:
        raise RuntimeError("google-cloud-firestore is required for hosted billing.") from exc
    firestore_client, _ = _clients()
    reference = firestore_client.collection(settings.firestore_payments_collection).document(
        session_id
    )
    transaction = firestore_client.transaction()

    @firestore.transactional
    def release(current_transaction) -> None:
        snapshot = reference.get(transaction=current_transaction)
        existing = snapshot.to_dict() if snapshot.exists else {}
        if existing.get("claimed_job_id") != job_id:
            return
        current_transaction.set(
            reference,
            {
                "claimed_job_id": firestore.DELETE_FIELD,
                "claimed_at": firestore.DELETE_FIELD,
                "released_at": datetime.now(timezone.utc).isoformat(),
            },
            merge=True,
        )

    release(transaction)


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


def delete_job(job_id: str) -> None:
    firestore_client, storage_client = _clients()
    bucket = storage_client.bucket(settings.gcp_bucket)
    for blob in bucket.list_blobs(prefix=f"jobs/{job_id}/"):
        blob.delete()
    firestore_client.collection(settings.firestore_collection).document(job_id).delete()


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
