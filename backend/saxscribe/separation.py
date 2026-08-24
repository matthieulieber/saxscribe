from __future__ import annotations

import json
import mimetypes
import shutil
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import quote

import requests


@dataclass
class StemCandidate:
    provider: str
    path: Path
    metadata: dict

    def to_dict(self) -> dict:
        value = asdict(self)
        value["path"] = self.path.name
        return value


class SeparatorProvider(Protocol):
    name: str

    def available(self) -> bool: ...

    def separate(
        self,
        source: Path,
        output_dir: Path,
        source_display_name: str,
        cancel_check: Callable[[], bool] | None = None,
    ) -> StemCandidate: ...


class UVRSeparatorProvider:
    name = "uvr"

    def __init__(self, separate_fn: Callable[..., Path], model_path: Path):
        self._separate_fn = separate_fn
        self._model_path = model_path

    def available(self) -> bool:
        return self._model_path.is_file()

    def separate(
        self,
        source: Path,
        output_dir: Path,
        source_display_name: str,
        cancel_check: Callable[[], bool] | None = None,
    ) -> StemCandidate:
        path = self._separate_fn(
            source,
            output_dir,
            source_display_name=source_display_name,
            cancel_check=cancel_check,
        )
        manifest_path = output_dir / "separation-manifest.json"
        runtime_manifest = {}
        if manifest_path.exists():
            try:
                runtime_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                runtime_manifest = {}
        return StemCandidate(
            provider=self.name,
            path=path,
            metadata={
                "engine": "audio-separator",
                "model_path": str(self._model_path),
                "runtime_manifest": runtime_manifest,
            },
        )


class LalalSeparatorProvider:
    """LALAL.AI API v1 wind-stem provider.

    The API contract is intentionally isolated here so the rest of SaxScribe is
    independent from a vendor-specific upload/poll/download workflow.
    """

    name = "lalal"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://www.lalal.ai/api/v1",
        splitter: str = "phoenix",
        extraction_level: str = "deep_extraction",
        poll_seconds: float = 4.0,
        timeout_seconds: int = 1800,
        session: requests.Session | None = None,
    ):
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.splitter = splitter
        self.extraction_level = extraction_level
        self.poll_seconds = max(1.0, poll_seconds)
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    def available(self) -> bool:
        return bool(self.api_key)

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-License-Key": self.api_key}

    @staticmethod
    def _safe_output_name(source_display_name: str) -> str:
        source_stem = Path(source_display_name or "recording").stem
        safe = "".join(character if character.isalnum() or character in " ._-'" else "-" for character in source_stem)
        safe = safe.strip(" .-") or "recording"
        return f"1_{safe}_(Wind Inst - LALAL).wav"

    def _raise_for_api_error(self, response: requests.Response, stage: str) -> dict:
        try:
            payload = response.json()
        except ValueError:
            payload = {"detail": response.text[-1000:]}
        if not response.ok:
            detail = payload.get("detail") or payload.get("error") or response.reason
            raise RuntimeError(f"LALAL.AI {stage} failed: {detail}")
        return payload

    def _upload(self, source: Path) -> str:
        content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        headers = {
            **self._headers,
            "Content-Type": content_type,
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(source.name)}",
        }
        with source.open("rb") as handle:
            response = self.session.post(
                f"{self.base_url}/upload/",
                headers=headers,
                data=handle,
                timeout=300,
            )
        payload = self._raise_for_api_error(response, "upload")
        source_id = payload.get("id")
        if not source_id:
            raise RuntimeError("LALAL.AI upload response did not include a source id.")
        return str(source_id)

    def _start(self, source_id: str) -> str:
        response = self.session.post(
            f"{self.base_url}/split/stem_separator/",
            headers=self._headers,
            json={
                "source_id": source_id,
                "idempotency_key": str(uuid.uuid4()),
                "presets": {
                    "splitter": self.splitter,
                    "stem": "wind",
                    "encoder_format": "wav",
                    "extraction_level": self.extraction_level,
                },
            },
            timeout=60,
        )
        payload = self._raise_for_api_error(response, "split request")
        task_id = payload.get("task_id")
        if not task_id:
            raise RuntimeError("LALAL.AI split response did not include a task id.")
        return str(task_id)

    def _wait(self, task_id: str, cancel_check: Callable[[], bool] | None = None) -> dict:
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            if cancel_check and cancel_check():
                raise RuntimeError("LALAL.AI separation cancelled locally.")
            response = self.session.post(
                f"{self.base_url}/check/",
                headers=self._headers,
                json={"task_ids": [task_id]},
                timeout=60,
            )
            payload = self._raise_for_api_error(response, "status check")
            item = payload.get("result", {}).get(task_id)
            if not item:
                raise RuntimeError("LALAL.AI status response omitted the requested task.")
            status = item.get("status")
            if status == "success":
                return item
            if status in {"error", "server_error", "cancelled"}:
                detail = item.get("error") or status
                if isinstance(detail, dict):
                    detail = detail.get("detail") or json.dumps(detail)
                raise RuntimeError(f"LALAL.AI separation failed: {detail}")
            time.sleep(self.poll_seconds)
        raise RuntimeError(f"LALAL.AI separation timed out after {self.timeout_seconds} seconds.")

    def _delete_source(self, source_id: str) -> None:
        try:
            self.session.post(
                f"{self.base_url}/delete/",
                headers=self._headers,
                json={"source_id": source_id},
                timeout=30,
            )
        except requests.RequestException:
            # Remote expiry still deletes the source. Cleanup failure must not
            # invalidate a successfully downloaded stem.
            return

    def separate(
        self,
        source: Path,
        output_dir: Path,
        source_display_name: str,
        cancel_check: Callable[[], bool] | None = None,
    ) -> StemCandidate:
        if not self.available():
            raise RuntimeError("LALAL_API_KEY is required for LALAL.AI separation.")
        source_id = self._upload(source)
        task_id = None
        try:
            task_id = self._start(source_id)
            completed = self._wait(task_id, cancel_check)
            tracks = completed.get("result", {}).get("tracks", [])
            matches = [item for item in tracks if item.get("type") == "stem" and item.get("label") == "wind"]
            if len(matches) != 1 or not matches[0].get("url"):
                labels = [f"{item.get('type')}:{item.get('label')}" for item in tracks]
                raise RuntimeError(f"LALAL.AI returned no unambiguous wind stem. Tracks: {labels}")
            output_dir.mkdir(parents=True, exist_ok=True)
            target = output_dir / self._safe_output_name(source_display_name)
            with self.session.get(matches[0]["url"], stream=True, timeout=300) as response:
                response.raise_for_status()
                with target.open("wb") as handle:
                    shutil.copyfileobj(response.raw, handle)
            return StemCandidate(
                provider=self.name,
                path=target,
                metadata={
                    "engine": "lalal-api-v1",
                    "splitter": self.splitter,
                    "stem": "wind",
                    "extraction_level": self.extraction_level,
                    "source_id": source_id,
                    "task_id": task_id,
                },
            )
        finally:
            self._delete_source(source_id)


def candidate_quality(evidence: dict) -> float:
    """Conservative heuristic, not a claimed transcription-accuracy score."""
    candidate_count = max(1, int(evidence.get("candidate_note_count") or 0))
    voiced_coverage = min(1.0, float(evidence.get("high_confidence_note_count") or 0) / candidate_count)
    pitch_agreement = max(0.0, min(1.0, float(evidence.get("high_confidence_pitch_agreement") or 0.0)))
    return round(0.55 * pitch_agreement + 0.45 * voiced_coverage, 4)


def compare_event_sets(left: list[dict], right: list[dict]) -> dict:
    if not left or not right:
        return {"agreement": 0.0, "matched_notes": 0, "reference_notes": max(len(left), len(right))}
    smaller, larger = (left, right) if len(left) <= len(right) else (right, left)
    matched = 0
    used: set[int] = set()
    for item in smaller:
        choices = [
            (index, candidate)
            for index, candidate in enumerate(larger)
            if index not in used
            and abs(float(candidate["start_beat"]) - float(item["start_beat"])) <= 0.25
            and abs(int(candidate["pitch"]) - int(item["pitch"])) <= 1
        ]
        if choices:
            index, _ = min(choices, key=lambda pair: abs(float(pair[1]["start_beat"]) - float(item["start_beat"])))
            used.add(index)
            matched += 1
    return {
        "agreement": round(matched / max(len(smaller), 1), 4),
        "matched_notes": matched,
        "reference_notes": len(smaller),
    }
