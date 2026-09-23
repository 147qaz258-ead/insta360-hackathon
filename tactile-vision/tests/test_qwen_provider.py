import unittest
from unittest.mock import patch

from multimodal.base import MultimodalProviderError
from multimodal.qwen import Qwen3VLApiProvider, _extract_json_object


class QwenProviderTests(unittest.TestCase):
    def test_extracts_plain_json(self):
        self.assertEqual(_extract_json_object('{"version": 1}')["version"], 1)

    def test_extracts_fenced_json(self):
        value = _extract_json_object('```json\n{"version": 1}\n```')
        self.assertEqual(value["version"], 1)

    def test_rejects_non_json(self):
        with self.assertRaises(MultimodalProviderError):
            _extract_json_object("hello")

    def test_extracts_largest_object_when_gateway_appends_json(self):
        value = _extract_json_object('{"note":"x"}\n{"version":2,"height_rows_rle":[[[80,0]]]}')
        self.assertEqual(value["version"], 2)

    def test_status_hides_credential(self):
        provider = Qwen3VLApiProvider(credential="test-credential-value")
        status = provider.status()
        self.assertTrue(status["configured"])
        self.assertFalse(status["thinking"])
        self.assertNotIn("test-credential-value", str(status))

    def test_structured_retry_retries_transient_upload_disconnect(self):
        provider = Qwen3VLApiProvider(credential="test")
        responses = [
            MultimodalProviderError("image upload connection failed: remote end closed connection"),
            {"ok": True},
        ]

        def fake_chat(_messages, max_tokens):
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        provider._chat = fake_chat
        with patch("multimodal.qwen.time.sleep"):
            result = provider._structured_chat_retry([], max_tokens=100, attempts=2)
        self.assertEqual(result, {"ok": True})

    def test_native_transport_falls_back_to_compatible_endpoint(self):
        provider = Qwen3VLApiProvider(credential="test", api_mode="dashscope-native")
        provider._resolve_native_images = lambda _messages: (_ for _ in ()).throw(
            MultimodalProviderError("image upload connection failed: remote end closed")
        )
        provider._compatible_chat_content = lambda _messages, _max_tokens: '{"ok":true}'

        result = provider._chat([], max_tokens=100)

        self.assertTrue(result["ok"])
        self.assertEqual(result["_meta"]["provider"], "openai-compatible-multimodal-fallback")

    def test_converts_openai_image_message_to_dashscope_shape(self):
        converted = Qwen3VLApiProvider._to_dashscope_messages([{
            "role": "user",
            "content": [
                {"type": "text", "text": "describe"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ],
        }])
        self.assertEqual(converted[0]["content"][0], {"image": "data:image/png;base64,abc"})
        self.assertEqual(converted[0]["content"][1], {"text": "describe"})

    def test_manifest_errors_accepts_complete_contract(self):
        manifest = {
            "version": 2,
            "device_id": "rdk_hdmi",
            "cols": 80,
            "rows": 48,
            "height_encoding": "uint8",
            "regions": [{"id": 1, "name": "杯子", "description": "桌面左侧杯子", "speech": "这是杯子"}],
            "scene_summary": "桌面场景",
            "audio_overview": "左侧有杯子",
            "design_spec": {"strategy": "轮廓"},
            "self_review": {"protocol_check": "pending rows"},
        }
        self.assertEqual(Qwen3VLApiProvider._manifest_errors(manifest), [])

    def test_manifest_errors_rejects_truncated_inner_object(self):
        errors = Qwen3VLApiProvider._manifest_errors({"strategy": "轮廓", "regions": []})
        self.assertTrue(any("version" in error for error in errors))
        self.assertTrue(any("regions" in error for error in errors))
        self.assertTrue(any("scene_summary" in error for error in errors))

    def test_row_chunk_errors_accepts_clean_chunk(self):
        manifest = {"regions": [{"id": 1}, {"id": 2}]}
        rows = [
            {"row": 0, "height_rle": [[10, 0], [60, 120], [10, 0]], "region_rle": [[10, 0], [60, 1], [10, 0]]},
            {"row": 1, "height_rle": [[80, 0]], "region_rle": [[80, 0]]},
        ]
        errors = Qwen3VLApiProvider._row_chunk_errors(
            {"row_start": 0, "row_end": 1, "rows": rows}, 0, 1, manifest
        )
        self.assertEqual(errors, [])

    def test_row_chunk_errors_reports_unowned_raised_pins(self):
        rows = [
            {"row": 0, "height_rle": [[40, 90], [40, 0]], "region_rle": [[80, 0]]},
        ]
        errors = Qwen3VLApiProvider._row_chunk_errors(
            {"row_start": 0, "row_end": 0, "rows": rows}, 0, 0, {"regions": [{"id": 1}]}
        )
        self.assertTrue(any("height>0" in message and "region_id 为 0" in message for message in errors))

    def test_row_chunk_errors_reports_unknown_region_ids(self):
        rows = [
            {"row": 0, "height_rle": [[80, 50]], "region_rle": [[30, 1], [50, 9]]},
        ]
        errors = Qwen3VLApiProvider._row_chunk_errors(
            {"row_start": 0, "row_end": 0, "rows": rows}, 0, 0, {"regions": [{"id": 1}]}
        )
        self.assertTrue(any("9" in message and "未定义" in message for message in errors))


if __name__ == "__main__":
    unittest.main()
