"""API 客户端测试（api_client）。

全部为本地测试：使用 mock 拦截 urllib，不发起任何真实网络请求。
"""

import base64
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parent.parent / "skill" / "src"
sys.path.insert(0, str(SRC))

import api_client as ac  # noqa: E402
import testdata  # noqa: E402

VALID_CONFIG = {
    "api_key": "sk-local-test",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-v4-flash-vision-exp",
    "detail": "auto",
    "timeout_seconds": 120,
}


class TestDetectFormat(unittest.TestCase):
    """普通图片格式识别：按文件内容魔数判断。"""

    def test_supported_formats(self):
        cases = {
            "PNG": (testdata.PNG_BYTES, "image/png"),
            "JPEG": (testdata.JPEG_BYTES, "image/jpeg"),
            "GIF": (testdata.GIF_BYTES, "image/gif"),
            "WEBP": (testdata.WEBP_BYTES, "image/webp"),
        }
        for name, (data, mime) in cases.items():
            with self.subTest(fmt=name):
                self.assertEqual(ac.detect_image_format(data), mime)

    def test_rejects_non_image_content(self):
        with self.assertRaises(ValueError):
            ac.detect_image_format(testdata.TEXT_BYTES)


class TestEncodeImage(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_roundtrip_base64(self):
        p = self.tmp / "t.png"
        p.write_bytes(testdata.PNG_BYTES)
        mime, b64 = ac.encode_image(str(p))
        self.assertEqual(mime, "image/png")
        self.assertEqual(base64.b64decode(b64), testdata.PNG_BYTES)

    def test_missing_file_raises_image_error(self):
        with self.assertRaises(ac.VisionImageError):
            ac.encode_image(str(self.tmp / "nope.png"))

    def test_over_32mib_raises(self):
        p = self.tmp / "big.png"
        with open(p, "wb") as f:
            f.truncate(ac.MAX_IMAGE_BYTES + 1)
        with self.assertRaises(ac.VisionImageError):
            ac.encode_image(str(p))

    def test_unsupported_content_raises(self):
        # 扩展名是 .png 但内容是文本：应按内容拒绝（与官方"按内容判断"一致）
        p = self.tmp / "fake.png"
        p.write_bytes(testdata.TEXT_BYTES)
        with self.assertRaises(ac.VisionImageError):
            ac.encode_image(str(p))


class TestBuildRequestBody(unittest.TestCase):
    """请求体结构：对照官方 text + image_url 格式。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.png = self.tmp / "t.png"
        self.png.write_bytes(testdata.PNG_BYTES)

    def test_official_structure_with_question(self):
        body = ac.build_request_body(
            "deepseek-v4-flash-vision-exp", [str(self.png)], "这是什么？", "auto"
        )
        self.assertEqual(body["model"], "deepseek-v4-flash-vision-exp")
        msg = body["messages"][0]
        self.assertEqual(msg["role"], "user")
        self.assertEqual(len(body["messages"]), 1)
        self.assertEqual(msg["content"][0], {"type": "text", "text": "这是什么？"})
        img = msg["content"][1]
        self.assertEqual(img["type"], "image_url")
        self.assertTrue(img["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(img["image_url"]["detail"], "auto")

    def test_no_question_omits_text_block(self):
        body = ac.build_request_body("m", [str(self.png)], "")
        content = body["messages"][0]["content"]
        self.assertEqual([b["type"] for b in content], ["image_url"])

    def test_multiple_images_in_one_request(self):
        p2 = self.tmp / "t.jpg"
        p2.write_bytes(testdata.JPEG_BYTES)
        body = ac.build_request_body("m", [str(self.png), str(p2)], "")
        content = body["messages"][0]["content"]
        self.assertEqual([b["type"] for b in content], ["image_url", "image_url"])
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_ocr_question_preserved_verbatim(self):
        q = "识别这张代码截图里的所有报错文字"
        body = ac.build_request_body("m", [str(self.png)], q)
        self.assertEqual(body["messages"][0]["content"][0]["text"], q)

    def test_code_screenshot_question_preserved_verbatim(self):
        q = "这张代码截图为什么报错？请指出错误行"
        body = ac.build_request_body("m", [str(self.png)], q)
        self.assertEqual(body["messages"][0]["content"][0]["text"], q)


class TestCallVisionAPI(unittest.TestCase):
    """API 调用与错误处理：mock urllib，不发起真实请求。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.png = self.tmp / "t.png"
        self.png.write_bytes(testdata.PNG_BYTES)

    def _fake_response(self, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        resp = mock.Mock()
        resp.read.return_value = data
        resp.__enter__ = mock.Mock(return_value=resp)
        resp.__exit__ = mock.Mock(return_value=False)
        return resp

    def _http_error(self, code, error_message):
        body = json.dumps({"error": {"message": error_message}}).encode("utf-8")
        return urllib.error.HTTPError(
            "https://api.deepseek.com/chat/completions", code, "HTTP Error", {}, io.BytesIO(body)
        )

    def test_success_returns_content_and_request_is_correct(self):
        resp = self._fake_response({"choices": [{"message": {"content": "图中是一只猫"}}]})
        with mock.patch("urllib.request.urlopen", return_value=resp) as m:
            out = ac.call_vision_api(VALID_CONFIG, [str(self.png)], "这是什么？")
        self.assertEqual(out, "图中是一只猫")
        # 校验实际发出的请求
        req = m.call_args[0][0]
        self.assertTrue(req.full_url.endswith("/chat/completions"))
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.headers["Authorization"], "Bearer sk-local-test")
        self.assertEqual(req.headers["Content-type"], "application/json")
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["model"], "deepseek-v4-flash-vision-exp")
        self.assertEqual(body["messages"][0]["role"], "user")

    def test_http_401_parses_official_error_and_hints_key(self):
        err = self._http_error(401, "Authentication Fails, Your api key: **** is invalid")
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(ac.VisionAPIError) as ctx:
                ac.call_vision_api(VALID_CONFIG, [str(self.png)], "q")
        msg = str(ctx.exception)
        self.assertIn("401", msg)
        self.assertIn("Authentication Fails", msg)
        self.assertIn("VISION_API_KEY", msg)

    def test_http_400_hints_image_format_or_size(self):
        err = self._http_error(400, "Invalid image")
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(ac.VisionAPIError) as ctx:
                ac.call_vision_api(VALID_CONFIG, [str(self.png)], "q")
        msg = str(ctx.exception)
        self.assertIn("400", msg)
        self.assertIn("请求参数错误", msg)

    def test_network_error_raises_api_error(self):
        err = urllib.error.URLError("connection refused")
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(ac.VisionAPIError) as ctx:
                ac.call_vision_api(VALID_CONFIG, [str(self.png)], "q")
        self.assertIn("网络请求失败", str(ctx.exception))

    def test_timeout_raises_api_error(self):
        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            with self.assertRaises(ac.VisionAPIError):
                ac.call_vision_api(VALID_CONFIG, [str(self.png)], "q")

    def test_malformed_response_raises_api_error(self):
        resp = self._fake_response({"unexpected": True})
        with mock.patch("urllib.request.urlopen", return_value=resp):
            with self.assertRaises(ac.VisionAPIError):
                ac.call_vision_api(VALID_CONFIG, [str(self.png)], "q")

    def test_empty_content_raises_api_error(self):
        resp = self._fake_response({"choices": [{"message": {"content": ""}}]})
        with mock.patch("urllib.request.urlopen", return_value=resp):
            with self.assertRaises(ac.VisionAPIError):
                ac.call_vision_api(VALID_CONFIG, [str(self.png)], "q")


if __name__ == "__main__":
    unittest.main()
