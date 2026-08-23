"""命令行入口测试（vision.py）。

全部为本地测试：mock API 调用，不发起任何真实网络请求，
不读取、不修改真实的 vision_config.env。
"""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parent.parent / "skill" / "src"
sys.path.insert(0, str(SRC))

from api_client import VisionAPIError, VisionImageError  # noqa: E402
import vision  # noqa: E402
import testdata  # noqa: E402


class TestSplitQuestion(unittest.TestCase):
    """图片 + 用户问题 参数处理。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.png = self.tmp / "t.png"
        self.png.write_bytes(testdata.PNG_BYTES)

    def test_explicit_question(self):
        ns = vision.parse_args(["a.png", "-q", "为什么报错？"])
        imgs, q = vision.split_question(ns)
        self.assertEqual(imgs, ["a.png"])
        self.assertEqual(q, "为什么报错？")

    def test_trailing_question_with_existing_image(self):
        ns = vision.parse_args([str(self.png), "这张图讲了什么"])
        imgs, q = vision.split_question(ns)
        self.assertEqual(imgs, [str(self.png)])
        self.assertEqual(q, "这张图讲了什么")

    def test_nonexistent_path_not_treated_as_question(self):
        # 回归：不存在的图片路径必须报"图片不存在"，不能被误当问题
        ns = vision.parse_args(["no-such.png"])
        imgs, _ = vision.split_question(ns)
        self.assertEqual(imgs, ["no-such.png"])

    def test_ocr_question(self):
        ns = vision.parse_args([str(self.png), "识别这张图片里的所有文字"])
        imgs, q = vision.split_question(ns)
        self.assertEqual(imgs, [str(self.png)])
        self.assertEqual(q, "识别这张图片里的所有文字")

    def test_code_screenshot_question(self):
        ns = vision.parse_args([str(self.png), "这张代码截图为什么报错"])
        imgs, q = vision.split_question(ns)
        self.assertEqual(q, "这张代码截图为什么报错")

    def test_default_question(self):
        ns = vision.parse_args([str(self.png)])
        _, q = vision.split_question(ns)
        self.assertEqual(q, vision.DEFAULT_QUESTION)

    def test_no_arguments_rejected_by_argparse(self):
        with self.assertRaises(SystemExit):
            vision.parse_args([])


class TestValidateImagePaths(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.png = self.tmp / "t.png"
        self.png.write_bytes(testdata.PNG_BYTES)

    def test_missing_path_raises(self):
        with self.assertRaises(VisionImageError):
            vision.validate_image_paths([str(self.tmp / "nope.png")])

    def test_over_32mib_raises(self):
        big = self.tmp / "big.png"
        with open(big, "wb") as f:
            f.truncate(vision.MAX_IMAGE_BYTES + 1)
        with self.assertRaises(VisionImageError):
            vision.validate_image_paths([str(big)])

    def test_valid_path_passes(self):
        vision.validate_image_paths([str(self.png)])


class TestMainExitCodes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = self.tmp / "vision_config.env"
        self.cfg.write_text("VISION_API_KEY=sk-local-test\n", encoding="utf-8")
        vision.CONFIG_PATH = self.cfg
        self.png = self.tmp / "t.png"
        self.png.write_bytes(testdata.PNG_BYTES)

    def tearDown(self):
        # 恢复真实配置路径，避免影响其他测试
        vision.CONFIG_PATH = vision.SKILL_DIR / "config" / "vision_config.env"

    def test_missing_api_key_returns_1(self):
        self.cfg.write_text("VISION_API_KEY=\n", encoding="utf-8")
        self.assertEqual(vision.main(["a.png"]), 1)

    def test_nonexistent_image_returns_2(self):
        self.assertEqual(vision.main(["no-such.png"]), 2)

    def test_api_error_returns_3(self):
        with mock.patch.object(
            vision, "call_vision_api", side_effect=VisionAPIError("模拟 API 失败")
        ):
            self.assertEqual(vision.main([str(self.png), "-q", "问题"]), 3)

    def test_success_returns_0_and_prints_result(self):
        buf = io.StringIO()
        with mock.patch.object(vision, "call_vision_api", return_value="视觉分析结果"), \
                contextlib.redirect_stdout(buf):
            code = vision.main([str(self.png), "-q", "这是什么？"])
        self.assertEqual(code, 0)
        self.assertIn("视觉分析结果", buf.getvalue())

    def test_success_stdout_contains_only_result(self):
        # stdout 只应包含视觉模型回答，不能混入其他内容（主模型直接读 stdout）
        buf = io.StringIO()
        with mock.patch.object(vision, "call_vision_api", return_value="纯净结果"), \
                contextlib.redirect_stdout(buf):
            vision.main([str(self.png), "-q", "问题"])
        self.assertEqual(buf.getvalue().strip(), "纯净结果")


if __name__ == "__main__":
    unittest.main()
