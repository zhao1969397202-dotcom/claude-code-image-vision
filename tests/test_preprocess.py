"""大图片自动处理测试（preprocess.py）。

全部为本地测试：临时图片、mock 缩放器，不发起任何网络请求，
不读取真实 vision_config.env，不涉及真实 API Key。
"""

import contextlib
import hashlib
import io
import random
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parent.parent / "skill" / "src"
sys.path.insert(0, str(SRC))

import preprocess as pp  # noqa: E402
import vision  # noqa: E402
import testdata  # noqa: E402
from api_client import detect_image_format  # noqa: E402


# ---------------------------------------------------------------- 辅助：构造测试图片

def _png_chunk(typ, data):
    return (struct.pack(">I", len(data)) + typ + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))


def make_png(w, h, rgb=(200, 60, 60)):
    """纯标准库构造 (w x h) 纯色 PNG。"""
    row = bytes(rgb) * w
    raw = b"".join(b"\x00" + row for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + _png_chunk(b"IDAT", zlib.compress(raw))
            + _png_chunk(b"IEND", b""))


def make_noise_png(w, h):
    """构造真随机噪声 PNG（zlib 无法压缩，文件大小接近原始像素量）。"""
    rng = random.Random(42)
    raw = bytearray()
    for _ in range(h):
        raw.append(0)  # 每行 PNG 过滤器字节
        raw.extend(rng.randbytes(w * 3))
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + _png_chunk(b"IDAT", zlib.compress(bytes(raw)))
            + _png_chunk(b"IEND", b""))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class PreprocessFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.orig_max_bytes = pp.MAX_IMAGE_BYTES
        self.orig_budget = pp.BODY_BUDGET_BYTES
        self.orig_temp_root = pp.TEMP_ROOT
        pp.TEMP_ROOT = self.tmp / "temp-images"

    def tearDown(self):
        pp.MAX_IMAGE_BYTES = self.orig_max_bytes
        pp.BODY_BUDGET_BYTES = self.orig_budget
        pp.TEMP_ROOT = self.orig_temp_root

    def write_png(self, name, w, h, **kw):
        p = self.tmp / name
        p.write_bytes(make_png(w, h, **kw))
        return p

    def write_noise_png(self, name, w, h):
        p = self.tmp / name
        p.write_bytes(make_noise_png(w, h))
        return p


# ---------------------------------------------------------------- 像素尺寸解析

class TestGetImageDimensions(unittest.TestCase):
    def test_png_dimensions(self):
        w, h = pp.get_image_dimensions(make_png(320, 200), "image/png")
        self.assertEqual((w, h), (320, 200))

    def test_gif_dimensions(self):
        data = b"GIF89a" + struct.pack("<HH", 320, 200) + b"\x00" * 20
        self.assertEqual(pp.get_image_dimensions(data, "image/gif"), (320, 200))

    def test_jpeg_dimensions(self):
        data = (b"\xff\xd8" + b"\xff\xc0" + struct.pack(">H", 17)
                + bytes([8]) + struct.pack(">HH", 100, 200) + b"\x00" * 8 + b"\xff\xd9")
        self.assertEqual(pp.get_image_dimensions(data, "image/jpeg"), (200, 100))

    def test_webp_vp8x_dimensions(self):
        # 标准 chunk 结构：fourcc(4) + size(4) + payload
        # VP8X payload：flags(1) + reserved(3) + 宽-1(3) + 高-1(3)
        payload = b"\x00" * 4 + bytes([0xFF, 0x03, 0x00]) + bytes([0x1F, 0x03, 0x00])
        body = b"VP8X" + struct.pack("<I", len(payload)) + payload
        data = b"RIFF" + struct.pack("<I", len(body)) + b"WEBP" + body
        self.assertEqual(pp.get_image_dimensions(data, "image/webp"), (1024, 800))

    def test_webp_vp8_dimensions(self):
        # VP8 payload：帧标记(3) + 起始码(3) + 分区大小(3) + 关键帧头(10)，
        # 宽高在关键帧头第 6-9 字节（14 位 LE）
        hdr = b"\x00" * 6 + struct.pack("<H", 640) + struct.pack("<H", 480)
        payload = b"\x00" * 9 + hdr
        body = b"VP8 " + struct.pack("<I", len(payload)) + payload
        data = b"RIFF" + struct.pack("<I", len(body)) + b"WEBP" + body
        self.assertEqual(pp.get_image_dimensions(data, "image/webp"), (640, 480))

    def test_webp_vp8l_dimensions(self):
        # VP8L payload：0x2F 签名 + 4 字节位域；w=200 (w-1=199), h=100 (h-1=99)
        payload = b"\x2F" + bytes([199, 0xC0, 24, 0])
        body = b"VP8L" + struct.pack("<I", len(payload)) + payload
        data = b"RIFF" + struct.pack("<I", len(body)) + b"WEBP" + body
        self.assertEqual(pp.get_image_dimensions(data, "image/webp"), (200, 100))

    def test_invalid_png_raises(self):
        with self.assertRaises(pp.PreprocessError):
            pp.get_image_dimensions(b"\x89PNG\r\n\x1a\n" + b"short", "image/png")

    def test_invalid_jpeg_raises(self):
        with self.assertRaises(pp.PreprocessError):
            pp.get_image_dimensions(b"\xff\xd8" + b"no-sof-marker-here", "image/jpeg")


# ---------------------------------------------------------------- prepare_images 核心逻辑

class TestPrepareNormalImage(PreprocessFixture):
    def test_normal_image_not_processed(self):
        p = self.write_png("ok.png", 100, 100)
        final, temps = pp.prepare_images([str(p)])
        self.assertEqual(final, [str(p)])   # 原图路径原样返回
        self.assertEqual(temps, [])         # 不产生任何临时文件


class TestPrepareOversized(PreprocessFixture):
    def test_pixel_exceeded_auto_processed(self):
        p = self.write_png("wide.png", 8200, 200)  # 单边 8200 > 8192
        orig_hash = sha256(p)
        final, temps = pp.prepare_images([str(p)])
        self.assertNotEqual(final[0], str(p))
        self.assertEqual(len(temps), 1)
        # 1) 处理后仍是有效图片，2) 像素已到限制内，3) 临时文件位置正确
        out = Path(final[0])
        self.assertEqual(out.parent, pp.TEMP_ROOT)
        data = out.read_bytes()
        mime = detect_image_format(data)
        w, h = pp.get_image_dimensions(data, mime)
        self.assertLessEqual(max(w, h), pp.MAX_SIDE)
        # 原始图片未被修改
        self.assertEqual(sha256(p), orig_hash)

    def test_size_exceeded_auto_processed(self):
        pp.MAX_IMAGE_BYTES = 500_000  # 模拟更低的大小限制
        p = self.write_noise_png("noise.png", 600, 600)  # 噪声 PNG 约 1MB
        self.assertGreater(p.stat().st_size, pp.MAX_IMAGE_BYTES)
        orig_hash = sha256(p)
        final, temps = pp.prepare_images([str(p)])
        self.assertNotEqual(final[0], str(p))
        out = Path(final[0])
        self.assertTrue(out.exists())
        self.assertLess(out.stat().st_size, pp.MAX_IMAGE_BYTES)
        self.assertEqual(detect_image_format(out.read_bytes()), "image/jpeg")
        self.assertEqual(sha256(p), orig_hash)  # 原图未动

    def test_multiple_images_processed_separately(self):
        big = self.write_png("big.png", 8200, 200)
        small = self.write_png("small.png", 100, 100)
        orig_big = sha256(big)
        final, temps = pp.prepare_images([str(big), str(small)])
        self.assertNotEqual(final[0], str(big))  # 大图被处理
        self.assertEqual(final[1], str(small))   # 小图原样保留
        self.assertEqual(len(temps), 1)          # 只产生一个临时副本
        self.assertEqual(sha256(big), orig_big)

    def test_body_budget_compresses_largest(self):
        pp.BODY_BUDGET_BYTES = 100_000
        p1 = self.write_noise_png("n1.png", 300, 300)
        p2 = self.write_noise_png("n2.png", 300, 300)
        h1, h2 = sha256(p1), sha256(p2)
        with mock.patch.object(pp, "_run_resizer",
                               side_effect=lambda path, out, w, h, fmt: Path(out).write_bytes(testdata.PNG_BYTES)):
            final, temps = pp.prepare_images([str(p1), str(p2)])
        self.assertEqual(len(temps), 2)          # 两张都转为临时副本
        self.assertNotIn(str(p1), final)
        self.assertNotIn(str(p2), final)
        self.assertLessEqual(pp._estimate_base64(final), pp.BODY_BUDGET_BYTES)
        self.assertEqual(sha256(p1), h1)         # 原图都未改动
        self.assertEqual(sha256(p2), h2)


class TestPreprocessErrors(PreprocessFixture):
    def test_processing_failure_clear_error(self):
        p = self.write_png("wide.png", 8200, 200)
        orig = p.read_bytes()
        # 模拟两个后端都失败：验证错误被包装为明确的 PreprocessError
        with mock.patch.object(pp, "_resize_pillow", side_effect=RuntimeError("模拟 Pillow 失败")), \
                mock.patch.object(pp, "_resize_gdi", side_effect=pp.PreprocessError("模拟 GDI+ 失败")):
            with self.assertRaises(pp.PreprocessError) as ctx:
                pp.process_image(str(p), pp.MAX_SIDE, pp.MAX_IMAGE_BYTES)
        self.assertIn("缩放", str(ctx.exception))
        self.assertEqual(p.read_bytes(), orig)  # 原图未改动
        # 失败后临时目录里不应残留候选文件
        leftovers = list(pp.TEMP_ROOT.glob("*")) if pp.TEMP_ROOT.is_dir() else []
        self.assertEqual(leftovers, [])

    def test_cleanup_only_removes_temp_files(self):
        pp.TEMP_ROOT.mkdir(parents=True)
        inside = pp.TEMP_ROOT / "t.png"
        inside.write_bytes(testdata.PNG_BYTES)
        outside = self.tmp / "outside.png"
        outside.write_bytes(testdata.PNG_BYTES)
        removed = pp.cleanup_temp_files([str(inside), str(outside)])
        self.assertEqual(removed, 1)
        self.assertFalse(inside.exists())
        self.assertTrue(outside.exists())  # 目录外文件绝不删除


# ---------------------------------------------------------------- vision.main 集成

class TestVisionMainIntegration(PreprocessFixture):
    def _temp_config(self, key="sk-LOCAL-TEST-KEY-0123456789abcdef"):
        cfg = self.tmp / "vision_config.env"
        cfg.write_text(f"VISION_API_KEY={key}\n", encoding="utf-8")
        vision.CONFIG_PATH = cfg
        return cfg, key

    def tearDown(self):
        vision.CONFIG_PATH = vision.SKILL_DIR / "config" / "vision_config.env"
        super().tearDown()

    def test_api_uses_processed_image_and_cleans_up(self):
        self._temp_config()
        p = self.write_png("wide.png", 8200, 200)
        captured = {}
        def fake_call(config, image_paths, question):
            captured["paths"] = list(image_paths)
            captured["exists_during_call"] = [Path(x).exists() for x in image_paths]
            return "分析结果"
        with mock.patch.object(vision, "call_vision_api", fake_call), \
                contextlib.redirect_stdout(io.StringIO()):
            code = vision.main([str(p), "-q", "这是什么？"])
        self.assertEqual(code, 0)
        self.assertEqual(len(captured["paths"]), 1)
        self.assertNotEqual(captured["paths"][0], str(p))          # API 拿到的是处理后的副本
        self.assertTrue(all(captured["exists_during_call"]))       # 调用时副本存在
        self.assertFalse(Path(captured["paths"][0]).exists())      # 调用后被清理
        self.assertEqual(len(list(pp.TEMP_ROOT.glob("pre_*"))), 0)  # 无残留

    def test_no_api_key_leak_in_output(self):
        _, key = self._temp_config(key="sk-LEAKTEST-0123456789abcdef")
        p = self.write_png("wide.png", 8200, 200)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(vision, "call_vision_api", return_value="结果"), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = vision.main([str(p), "-q", "问题"])
        self.assertEqual(code, 0)
        self.assertNotIn(key, out.getvalue())
        self.assertNotIn(key, err.getvalue())


# ---------------------------------------------------------------- GDI+ 后备（真实执行）

@unittest.skipUnless(sys.platform == "win32", "GDI+ 仅 Windows")
class TestGdiFallback(PreprocessFixture):
    def test_gdi_real_resize_produces_valid_image(self):
        p = self.write_png("wide.png", 8200, 200)
        with mock.patch.object(pp, "_resize_pillow", side_effect=ImportError("no PIL")):
            out = pp.process_image(str(p), pp.MAX_SIDE, pp.MAX_IMAGE_BYTES)
        self.assertTrue(out.exists())
        data = out.read_bytes()
        self.assertEqual(detect_image_format(data), "image/png")
        w, h = pp.get_image_dimensions(data, "image/png")
        self.assertLessEqual(max(w, h), pp.MAX_SIDE)


if __name__ == "__main__":
    unittest.main()
