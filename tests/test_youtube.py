import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.saxscribe.youtube import (
    YouTubeSourceError,
    download_youtube_audio,
    normalize_youtube_url,
)


class FakeYoutubeDL:
    last_options = None

    def __init__(self, options):
        self.options = options
        FakeYoutubeDL.last_options = options

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def extract_info(self, url, download=True):
        assert download is True
        assert url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        path = Path(
            self.options["outtmpl"]
            .replace("%(id)s", "dQw4w9WgXcQ")
            .replace("%(ext)s", "m4a")
        )
        path.write_bytes(b"authorized-audio")
        self.options["progress_hooks"][0](
            {"status": "downloading", "downloaded_bytes": 5, "total_bytes": 10}
        )
        return {
            "id": "dQw4w9WgXcQ",
            "duration": 212,
            "title": "Test song",
            "artist": "Test artist",
        }


class YouTubeSourceTests(unittest.TestCase):
    def test_normalizes_supported_single_video_forms(self):
        expected = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        self.assertEqual(normalize_youtube_url("https://youtu.be/dQw4w9WgXcQ?t=12"), expected)
        self.assertEqual(
            normalize_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=abc"),
            expected,
        )
        self.assertEqual(normalize_youtube_url("youtube.com/shorts/dQw4w9WgXcQ"), expected)
        self.assertEqual(normalize_youtube_url("https://music.youtube.com/watch?v=dQw4w9WgXcQ"), expected)

    def test_rejects_non_youtube_and_playlist_urls(self):
        rejected = [
            "https://youtube.com.example.org/watch?v=dQw4w9WgXcQ",
            "https://www.youtube.com/playlist?list=PL123",
            "https://www.youtube.com/@channel",
            "file:///etc/passwd",
        ]
        for value in rejected:
            with self.subTest(value=value), self.assertRaises(YouTubeSourceError):
                normalize_youtube_url(value)

    def test_download_is_single_video_capped_and_returns_metadata(self):
        reports = []
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "backend.saxscribe.youtube._load_youtube_dl", return_value=FakeYoutubeDL
        ):
            result = download_youtube_audio(
                "https://youtu.be/dQw4w9WgXcQ",
                Path(directory),
                max_bytes=1024,
                max_seconds=480,
                progress=lambda *items: reports.append(items),
            )
        self.assertEqual(result.title, "Test song")
        self.assertEqual(result.artist, "Test artist")
        self.assertEqual(result.duration_seconds, 212)
        self.assertEqual(result.canonical_url, "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertTrue(FakeYoutubeDL.last_options["noplaylist"])
        self.assertEqual(FakeYoutubeDL.last_options["max_filesize"], 1024)
        self.assertEqual(FakeYoutubeDL.last_options["js_runtimes"], {"node": {}})
        self.assertTrue(any(items[0] == "fetching" for items in reports))

    def test_download_honors_cancellation_before_network_access(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "backend.saxscribe.youtube._load_youtube_dl", return_value=FakeYoutubeDL
        ) as loader:
            with self.assertRaises(YouTubeSourceError):
                download_youtube_audio(
                    "https://youtu.be/dQw4w9WgXcQ",
                    Path(directory),
                    max_bytes=1024,
                    max_seconds=480,
                    cancel_check=lambda: True,
                )
            loader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
