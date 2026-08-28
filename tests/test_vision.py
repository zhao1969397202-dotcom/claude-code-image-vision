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

from api_client import MAX_IMAGE_BYTES, VisionAPIError, VisionImageError  # noqa: E402
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

    def test_over_32mib_file_passes_validate(self):
        # 大小超限不再由 validate 拦截，交给 prepare_images 自动处理
        big = self.tmp / "big.png"
        big.write_bytes(testdata.PNG_BYTES)
        with open(big, "ab") as f:
            f.truncate(MAX_IMAGE_BYTES + 1)
        self.assertGreater(big.stat().st_size, MAX_IMAGE_BYTES)
        vision.validate_image_paths([str(big)])  # 不应抛异常

    def test_unsupported_format_raises(self):
        bad = self.tmp / "fake.png"  # 扩展名是 .png，内容是文本
        bad.write_bytes(testdata.TEXT_BYTES)
        with self.assertRaises(VisionImageError):
            vision.validate_image_paths([str(bad)])

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


class TestIsolatedInterpreter(unittest.TestCase):
    """回归测试：便携运行时（embeddable）以隔离模式运行，不把脚本目录加入 sys.path。
    用系统 python 的 -I 参数模拟隔离模式，验证入口脚本自带路径引导、模块互引正常。"""

    def test_entry_scripts_work_under_isolated_mode(self):
        import subprocess
        src = vision.SKILL_DIR / "src"
        # vision.py：应走到配置检查（项目 env Key 为空 → 配置错误），而不是模块导入崩溃
        proc1 = subprocess.run(
            [sys.executable, "-I", str(src / "vision.py"), "missing.png"],
            capture_output=True, timeout=60,
        )
        err1 = proc1.stderr.decode("utf-8", errors="replace")
        self.assertNotIn("ModuleNotFoundError", err1)
        self.assertNotIn("Traceback", err1)
        self.assertIn("配置错误", err1)
        # attachment.py：清理模式应正常执行
        proc2 = subprocess.run(
            [sys.executable, "-I", str(src / "attachment.py"), "--cleanup"],
            capture_output=True, timeout=60,
        )
        self.assertEqual(proc2.returncode, 0)


if __name__ == "__main__":
    unittest.main()
