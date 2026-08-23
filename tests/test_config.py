"""配置解析测试（vision.load_config）。

全部使用临时配置文件，不读取、不修改真实的 vision_config.env。
"""

import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "skill" / "src"
sys.path.insert(0, str(SRC))

from api_client import VisionConfigError  # noqa: E402
import vision  # noqa: E402


class TestConfigParsing(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = self.tmp / "vision_config.env"

    def write(self, text):
        self.cfg.write_text(text, encoding="utf-8")
        return self.cfg

    def test_parses_key_value_with_comments_and_blank_lines(self):
        self.write("# 注释行\n\nVISION_API_KEY=sk-test\nVISION_IMAGE_DETAIL=low\n")
        c = vision.load_config(self.cfg)
        self.assertEqual(c["api_key"], "sk-test")
        self.assertEqual(c["detail"], "low")

    def test_strips_quotes_around_value(self):
        self.write('VISION_API_KEY="sk-quoted"\n')
        self.assertEqual(vision.load_config(self.cfg)["api_key"], "sk-quoted")

    def test_ignores_lines_without_equals(self):
        self.write("VISION_API_KEY=sk-test\n这不是配置行\n")
        self.assertEqual(vision.load_config(self.cfg)["api_key"], "sk-test")

    def test_defaults_applied(self):
        self.write("VISION_API_KEY=sk-test\n")
        c = vision.load_config(self.cfg)
        self.assertEqual(c["model"], "deepseek-v4-flash-vision-exp")
        self.assertEqual(c["base_url"], "https://api.deepseek.com")
        self.assertEqual(c["detail"], "auto")
        self.assertEqual(c["timeout_seconds"], 120)

    def test_invalid_timeout_falls_back_to_default(self):
        self.write("VISION_API_KEY=sk-test\nVISION_TIMEOUT_SECONDS=abc\n")
        self.assertEqual(vision.load_config(self.cfg)["timeout_seconds"], 120)

    def test_missing_file_raises_config_error(self):
        with self.assertRaises(VisionConfigError):
            vision.load_config(self.tmp / "not-exist.env")


class TestConfigErrors(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = self.tmp / "vision_config.env"

    def test_empty_key_raises(self):
        self.cfg.write_text("VISION_API_KEY=\n", encoding="utf-8")
        with self.assertRaises(VisionConfigError):
            vision.load_config(self.cfg)

    def test_placeholder_keys_raise(self):
        for bad in ("your_api_key_here", "YOUR-API-KEY-HERE", "your_api_key", "changeme"):
            with self.subTest(bad=bad):
                self.cfg.write_text(f"VISION_API_KEY={bad}\n", encoding="utf-8")
                with self.assertRaises(VisionConfigError):
                    vision.load_config(self.cfg)

    def test_error_message_points_to_config_file(self):
        self.cfg.write_text("VISION_API_KEY=\n", encoding="utf-8")
        with self.assertRaises(VisionConfigError) as ctx:
            vision.load_config(self.cfg)
        self.assertIn("vision_config.env", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
