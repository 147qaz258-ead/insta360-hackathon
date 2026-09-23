from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from touchsight.panorama.views import PanoramaViewGenerator, ViewSpec
from touchsight.web import server


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="upload_test_", dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup)
        self.input_dir = Path(self.tmp.name) / "input"
        self.input_dir.mkdir()
        for replacement in (
            patch.object(server.settings, "input_dir", self.input_dir),
            patch.object(server, "ALLOWED_ROOTS", [self.input_dir.resolve()]),
            patch.dict(server.app.config, TESTING=True),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)
        self.client = server.app.test_client()

    def photo(self, fmt="JPEG"):
        data = io.BytesIO()
        Image.new("RGB", (128, 64), (90, 120, 160)).save(data, format=fmt)
        return data.getvalue()

    def upload(self, content, name):
        return self.client.post("/api/upload", data={"file": (io.BytesIO(content), name)})

    def test_missing_file(self):
        response = self.client.post("/api/upload")
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json)

    def test_unsupported_formats(self):
        for suffix in ("dng", "insv", "mp4", "svg", "exe"):
            with self.subTest(suffix=suffix):
                response = self.upload(b"invalid", f"photo.{suffix}")
                self.assertEqual(response.status_code, 415)
                self.assertIn("INSP", response.json["error"])
        self.assertEqual(list(self.input_dir.iterdir()), [])

    def test_empty_and_corrupt_photos_leave_no_files(self):
        for data, expected in ((b"", 400), (b"not a photo", 422), (self.photo()[:80], 422)):
            with self.subTest(expected=expected, length=len(data)):
                response = self.upload(data, "broken.jpg")
                self.assertEqual(response.status_code, expected)
                self.assertIn("error", response.json)
                self.assertEqual(list(self.input_dir.iterdir()), [])

    def test_renamed_non_photo_format_is_rejected(self):
        response = self.upload(self.photo("GIF"), "renamed.jpg")
        self.assertEqual(response.status_code, 415)
        self.assertEqual(list(self.input_dir.iterdir()), [])

    def test_jpeg_png_upload_and_render(self):
        for name, fmt in (("全景合照.JPG", "JPEG"), ("pano.png", "PNG"), ("pano.jpeg", "JPEG")):
            with self.subTest(name=name):
                response = self.upload(self.photo(fmt), name)
                self.assertEqual(response.status_code, 200, response.json)
                result = response.json
                self.assertEqual(result["source_name"], name)
                self.assertFalse(result["stitched"])
                self.assertEqual(Path(result["name"]).suffix, Path(name).suffix.lower())
                image_response = self.client.get(result["url"])
                self.assertEqual(image_response.status_code, 200)
                image_response.close()
                generator = PanoramaViewGenerator(self.input_dir / result["name"])
                crop = generator.render_to_file(
                    ViewSpec(view_id="test", yaw=20, pitch=0, fov=70, width=120, height=80),
                    Path(self.tmp.name) / "candidates",
                )
                self.assertTrue(crop.is_file())
        self.assertEqual(len(self.client.get("/api/panos").json), 3)
        self.assertFalse(any(p.name.startswith(".upload_") for p in self.input_dir.iterdir()))

    def test_same_name_preserves_existing_photos(self):
        existing = self.input_dir / "pano.jpg"
        original = self.photo()
        existing.write_bytes(original)
        first = self.upload(original, "pano.jpg").json["name"]
        second = self.upload(original, "pano.jpg").json["name"]
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, existing.name)
        self.assertEqual(existing.read_bytes(), original)

    def test_path_and_html_names_are_safe(self):
        for name in ("../../outside.jpg", r"C:\fakepath\outside.jpg", "<img onerror='alert(1)'>.jpg"):
            with self.subTest(name=name):
                response = self.upload(self.photo(), name)
                self.assertEqual(response.status_code, 200, response.json)
                stored = response.json["name"]
                self.assertEqual(Path(stored).name, stored)
                self.assertFalse(any(c in stored for c in "<>:'\"/\\"))
                self.assertTrue((self.input_dir / stored).is_file())
        self.assertFalse((Path(self.tmp.name) / "outside.jpg").exists())

    def test_insp_uses_preparation_and_retains_raw(self):
        raw = b"unit-test INSP payload"

        def stitch_stub(src, dst):
            self.assertEqual(src.suffix, ".insp")
            self.assertEqual(src.read_bytes(), raw)
            dst.write_bytes(self.photo())
            return True

        with patch("touchsight.capture.acquisition.stitch_insp", side_effect=stitch_stub) as stitch:
            response = self.upload(raw, "全景.INSP")
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(stitch.call_count, 1)
        result = response.json
        self.assertTrue(result["stitched"])
        self.assertTrue(result["name"].endswith(".jpg"))
        self.assertEqual(result["source_name"], "全景.INSP")
        raw_path = self.input_dir / "_raw" / f"{Path(result['name']).stem}.insp"
        self.assertEqual(raw_path.read_bytes(), raw)
        self.assertTrue((self.input_dir / result["name"]).is_file())
        self.assertEqual(len(self.client.get("/api/panos").json), 1)

    def test_stitch_failure_cleans_staging(self):
        with patch("touchsight.capture.acquisition.stitch_insp", return_value=False):
            response = self.upload(b"unit-test invalid INSP", "photo.insp")
        self.assertEqual(response.status_code, 422)
        self.assertIn("拼接失败", response.json["error"])
        self.assertEqual(list(self.input_dir.iterdir()), [])

    def test_file_limit_excludes_multipart_overhead(self):
        photo = self.photo()
        with patch.object(server, "MAX_UPLOAD_BYTES", len(photo)):
            accepted = self.upload(photo, "exact.jpg")
            rejected = self.upload(photo + b"extra", "oversized.jpg")
        self.assertEqual(accepted.status_code, 200, accepted.json)
        self.assertEqual(rejected.status_code, 413)
        self.assertEqual(len(self.client.get("/api/panos").json), 1)

    def test_oversized_upload_returns_json(self):
        response = self.client.post(
            "/api/upload", data={"file": (io.BytesIO(b"test"), "photo.jpg")},
            environ_overrides={"CONTENT_LENGTH": str(257 * 1024 * 1024)},
        )
        self.assertEqual(response.status_code, 413)
        self.assertIn("256 MB", response.json["error"])
        self.assertEqual(list(self.input_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
