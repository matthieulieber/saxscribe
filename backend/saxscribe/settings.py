from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORK_DIR = ROOT / "work"
DEFAULT_UVR_MODEL_DIR = ROOT / ".models" / "audio-separator"
DEFAULT_UVR_MODEL_SHA256 = "acc6d472b4b478da9c9ab5af45b167749e05a7f65b30c7d5988b3700a513aeee"


@dataclass(frozen=True)
class Settings:
    runtime_mode: str = os.getenv("RUNTIME_MODE", "local").lower()
    work_dir: Path = Path(os.getenv("WORK_DIR", str(DEFAULT_WORK_DIR)))
    uvr_model_name: str = os.getenv("UVR_MODEL_NAME", "17_HP-Wind_Inst-UVR.pth")
    uvr_target_stem: str = os.getenv("UVR_TARGET_STEM", "Woodwinds")
    uvr_output_label: str = os.getenv("UVR_OUTPUT_LABEL", "Wind Inst")
    uvr_model_dir: str = os.getenv("UVR_MODEL_DIR") or str(DEFAULT_UVR_MODEL_DIR)
    uvr_model_sha256: str = os.getenv("UVR_MODEL_SHA256") or DEFAULT_UVR_MODEL_SHA256
    uvr_output_format: str = os.getenv("UVR_OUTPUT_FORMAT", "WAV")
    uvr_vr_window_size: int = int(os.getenv("UVR_VR_WINDOW_SIZE", "512"))
    uvr_vr_aggression: int = int(os.getenv("UVR_VR_AGGRESSION", "5"))
    uvr_vr_high_end_process: bool = os.getenv("UVR_VR_HIGH_END_PROCESS", "true").lower() in {"1", "true", "yes", "on"}
    uvr_force_cpu: bool = os.getenv("UVR_FORCE_CPU", "true").lower() in {"1", "true", "yes", "on"}
    uvr_resampler: str = os.getenv("UVR_RESAMPLER", "polyphase")
    separation_provider: str = os.getenv("SEPARATION_PROVIDER", "uvr").lower()
    separation_primary: str = os.getenv("SEPARATION_PRIMARY", "uvr").lower()
    separation_quality_threshold: float = float(os.getenv("SEPARATION_QUALITY_THRESHOLD", "0.68"))
    lalal_api_key: str = os.getenv("LALAL_API_KEY", "")
    lalal_api_base_url: str = os.getenv("LALAL_API_BASE_URL", "https://www.lalal.ai/api/v1")
    lalal_splitter: str = os.getenv("LALAL_SPLITTER", "phoenix")
    lalal_extraction_level: str = os.getenv("LALAL_EXTRACTION_LEVEL", "deep_extraction")
    lalal_poll_seconds: float = float(os.getenv("LALAL_POLL_SECONDS", "4"))
    lalal_timeout_seconds: int = int(os.getenv("LALAL_TIMEOUT_SECONDS", "1800"))
    sax_device: str = os.getenv("SAX_DEVICE", "cpu")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5-mini")
    openai_audio_model: str = os.getenv("OPENAI_AUDIO_MODEL", "gpt-audio-1.5")
    openai_audio_chunk_seconds: int = int(os.getenv("OPENAI_AUDIO_CHUNK_SECONDS", "60"))
    openai_audio_max_chunks: int = int(os.getenv("OPENAI_AUDIO_MAX_CHUNKS", "8"))
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "250"))
    keep_jobs_hours: int = int(os.getenv("KEEP_JOBS_HOURS", "24"))
    gcp_project: str = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    gcp_region: str = os.getenv("GCP_REGION", "us-west1")
    gcp_bucket: str = os.getenv("GCP_BUCKET", "")
    gcp_job_name: str = os.getenv("GCP_JOB_NAME", "saxscribe-worker")
    firestore_collection: str = os.getenv("FIRESTORE_COLLECTION", "saxscribe_jobs")
    firestore_payments_collection: str = os.getenv(
        "FIRESTORE_PAYMENTS_COLLECTION", "saxscribe_payments"
    )
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    stripe_secret_key: str = os.getenv("STRIPE_SECRET_KEY", "")
    stripe_webhook_secret: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    stripe_enhanced_price_id: str = os.getenv("STRIPE_ENHANCED_PRICE_ID", "")


settings = Settings()
