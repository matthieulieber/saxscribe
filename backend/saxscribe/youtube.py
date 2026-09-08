from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse


YOUTUBE_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}


class YouTubeSourceError(RuntimeError):
    pass


class YouTubeDownloadCancelled(YouTubeSourceError):
    pass


@dataclass(frozen=True)
class YouTubeSource:
    path: Path
    video_id: str
    title: str
    artist: str
    duration_seconds: float
    canonical_url: str


def normalize_youtube_url(value: str) -> str:
    """Accept one YouTube video and strip playlist/tracking parameters."""
    raw = (value or "").strip()
    if not raw:
        raise YouTubeSourceError("Paste a YouTube video link or upload an audio file.")
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        parsed = urlparse(raw)
    except ValueError as exc:
        raise YouTubeSourceError("That is not a valid YouTube video link.") from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise YouTubeSourceError("Only a normal YouTube web link is supported.")
    if host not in YOUTUBE_HOSTS:
        raise YouTubeSourceError("Only youtube.com and youtu.be video links are supported.")

    path_parts = [part for part in parsed.path.split("/") if part]
    query = parse_qs(parsed.query)
    video_id = ""
    if host == "youtu.be":
        video_id = path_parts[0] if path_parts else ""
    elif parsed.path.rstrip("/") == "/watch":
        video_id = (query.get("v") or [""])[0]
    elif path_parts and path_parts[0] in {"shorts", "embed", "live"}:
        video_id = path_parts[1] if len(path_parts) > 1 else ""
    elif path_parts and path_parts[0] == "playlist":
        raise YouTubeSourceError("Playlists are not supported. Paste one video link.")

    if not YOUTUBE_VIDEO_ID.fullmatch(video_id):
        raise YouTubeSourceError("Paste a link to one YouTube video, not a channel, search, or playlist.")
    return f"https://www.youtube.com/watch?v={video_id}"


def _load_youtube_dl():
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise YouTubeSourceError(
            "YouTube input is not installed. Run scripts/setup-mac.sh again."
        ) from exc
    return YoutubeDL


class _QuietLogger:
    def debug(self, _message: str) -> None:
        return

    def info(self, _message: str) -> None:
        return

    def warning(self, _message: str) -> None:
        return

    def error(self, _message: str) -> None:
        return


def download_youtube_audio(
    url: str,
    target_dir: Path,
    *,
    max_bytes: int,
    max_seconds: int,
    cancel_check: Callable[[], bool] | None = None,
    progress: Callable[[str, int, str], None] | None = None,
) -> YouTubeSource:
    """Download one authorized YouTube video's best audio-only stream."""
    canonical_url = normalize_youtube_url(url)
    video_id = canonical_url.rsplit("=", 1)[-1]
    target_dir.mkdir(parents=True, exist_ok=True)
    cancel_check = cancel_check or (lambda: False)

    def ensure_not_cancelled() -> None:
        if cancel_check():
            raise YouTubeDownloadCancelled("YouTube download cancelled.")

    def match_filter(info: dict, *, incomplete: bool = False) -> str | None:
        ensure_not_cancelled()
        if incomplete:
            return None
        live_status = str(info.get("live_status") or "")
        if info.get("is_live") or live_status in {"is_live", "is_upcoming", "post_live"}:
            return "Live and upcoming YouTube streams are not supported."
        duration = info.get("duration")
        if duration is None:
            return "The video's duration could not be verified."
        if float(duration) > max_seconds:
            return f"The YouTube video exceeds the {max_seconds // 60}-minute limit."
        return None

    last_reported = -1

    def progress_hook(status: dict) -> None:
        nonlocal last_reported
        ensure_not_cancelled()
        if not progress or status.get("status") != "downloading":
            return
        total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
        downloaded = status.get("downloaded_bytes") or 0
        percent = min(99, int(downloaded * 100 / total)) if total else 0
        bucket = percent // 10
        if bucket != last_reported:
            last_reported = bucket
            progress("fetching", min(7, 3 + percent // 25), f"Downloading YouTube audio · {percent}%")

    if progress:
        progress("fetching", 2, "Checking the YouTube video")
    ensure_not_cancelled()
    options = {
        "format": "m4a/bestaudio/best",
        "outtmpl": str(target_dir / "youtube-%(id)s.%(ext)s"),
        "noplaylist": True,
        "playlist_items": "1",
        "max_filesize": max_bytes,
        "socket_timeout": 20,
        "retries": 2,
        "fragment_retries": 2,
        "continuedl": False,
        "overwrites": True,
        "quiet": True,
        "no_warnings": True,
        "logger": _QuietLogger(),
        "match_filter": match_filter,
        "progress_hooks": [progress_hook],
        # Current YouTube extraction requires a JS challenge runtime. Node 22+
        # is already required by SaxScribe's frontend toolchain.
        "js_runtimes": {"node": {}},
    }
    try:
        downloader_class = _load_youtube_dl()
        with downloader_class(options) as downloader:
            info = downloader.extract_info(canonical_url, download=True)
    except YouTubeDownloadCancelled:
        raise
    except Exception as exc:
        ensure_not_cancelled()
        for partial in target_dir.glob("youtube-*.part"):
            partial.unlink(missing_ok=True)
        message = str(exc).strip()
        if "minute limit" in message or "duration could not" in message or "Live and upcoming" in message:
            raise YouTubeSourceError(message) from exc
        raise YouTubeSourceError(
            "YouTube could not provide this audio. The video may be private, restricted, "
            "blocked for automated access, or temporarily unavailable. Upload an audio file instead."
        ) from exc

    ensure_not_cancelled()
    candidates = [
        path
        for path in target_dir.glob(f"youtube-{video_id}.*")
        if path.is_file() and path.suffix not in {".part", ".ytdl", ".json"}
    ]
    if len(candidates) != 1:
        raise YouTubeSourceError("YouTube finished without producing one usable audio file.")
    source_path = candidates[0]
    if source_path.stat().st_size > max_bytes:
        source_path.unlink(missing_ok=True)
        raise YouTubeSourceError(f"Downloaded audio exceeds the {max_bytes // 1024 // 1024} MB limit.")

    duration = float(info.get("duration") or 0)
    if not 0 < duration <= max_seconds:
        source_path.unlink(missing_ok=True)
        raise YouTubeSourceError("The YouTube video's duration is missing or exceeds the configured limit.")
    title = str(info.get("track") or info.get("title") or f"YouTube {video_id}").strip()
    artist = str(info.get("artist") or info.get("uploader") or "").strip()
    if progress:
        progress("fetching", 7, "YouTube audio downloaded")
    return YouTubeSource(
        path=source_path,
        video_id=video_id,
        title=title[:200],
        artist=artist[:200],
        duration_seconds=duration,
        canonical_url=canonical_url,
    )
