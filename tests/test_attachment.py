"""附件适配层测试（attachment.py）。

全部为本地测试：使用临时伪造的会话存档目录，不读取真实会话，
不发起任何网络请求，不触碰真实 vision_config.env。
"""

import base64
import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parent.parent / "skill" / "src"
sys.path.insert(0, str(SRC))

import attachment as att  # noqa: E402
import testdata  # noqa: E402


def user_msg_with_images(pairs):
    """构造一条含图片块的用户消息。pairs = [(media_type, raw_bytes), ...]"""
    content = []
    for mime, raw in pairs:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": mime, "data": base64.b64encode(raw).decode()},
        })
    content.append({"type": "text", "text": "帮我看看这张图片"})
    return {"type": "user", "message": {"role": "user", "content": content}}


def make_transcript(path, entries):
    """把条目列表写入 JSONL（entries 支持 str 作为原始行）。"""
    lines = []
    for e in entries:
        lines.append(e if isinstance(e, str) else json.dumps(e, ensure_ascii=False))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TranscriptFixture(unittest.TestCase):
    """共享的临时会话目录夹具。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.projects = self.tmp / "projects"
        self.session_dir = self.projects / "proj-hash"
        self.session_dir.mkdir(parents=True)
        self.sid = "aaaa1111-bbbb-2222-cccc-3333dddd4444"
        self.jsonl = self.session_dir / f"{self.sid}.jsonl"
        self.temp_root = self.tmp / "temp-images"
        self._patch_projects = mock.patch.object(att, "PROJECTS_DIR", self.projects)
        self._patch_projects.start()

    def tearDown(self):
        self._patch_projects.stop()


class TestFindSessionFile(TranscriptFixture):
    def test_find_by_session_id(self):
        make_transcript(self.jsonl, [user_msg_with_images([("image/png", testdata.PNG_BYTES)])])
        self.assertEqual(att.find_session_file(self.sid), self.jsonl)

    def test_wrong_session_id_falls_back_to_newest_recent(self):
        make_transcript(self.jsonl, ["{}"])
        other = self.session_dir / "other-session.jsonl"
        make_transcript(other, [user_msg_with_images([("image/png", testdata.PNG_BYTES)])])
        self.assertEqual(att.find_session_file("no-such-id"), other)

    def test_empty_session_id_uses_fallback(self):
        make_transcript(self.jsonl, [user_msg_with_images([("image/png", testdata.PNG_BYTES)])])
        self.assertEqual(att.find_session_file(""), self.jsonl)

    def test_stale_files_not_used_when_no_session_match(self):
        make_transcript(self.jsonl, ["{}"])
        old = self.session_dir / "old-session.jsonl"
        make_transcript(old, ["{}"])
        one_hour_ago = time.time() - att.FALLBACK_RECENT_SECONDS - 60
        os.utime(self.jsonl, (one_hour_ago, one_hour_ago))
        os.utime(old, (one_hour_ago, one_hour_ago))
        with self.assertRaises(att.AttachmentError) as ctx:
            att.find_session_file("no-such-id")
        self.assertEqual(ctx.exception.exit_code, 1)

    def test_subagent_transcripts_are_not_candidates(self):
        # 子代理存档更深一层，glob("*/*.jsonl") 天然不会匹配到
        sub = self.session_dir / "subagents" / "agent-1.jsonl"
        sub.parent.mkdir(parents=True)
        make_transcript(sub, [user_msg_with_images([("image/png", testdata.PNG_BYTES)])])
        make_transcript(self.jsonl, [user_msg_with_images([("image/png", testdata.PNG_BYTES)])])
        self.assertEqual(att.find_session_file(self.sid), self.jsonl)


class TestExtractLatestUserImages(TranscriptFixture):
    def test_single_image_extracted(self):
        make_transcript(self.jsonl, [user_msg_with_images([("image/png", testdata.PNG_BYTES)])])
        blocks = att.extract_latest_user_images(self.jsonl)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["source"]["media_type"], "image/png")

    def test_multiple_images_in_one_message(self):
        pairs = [("image/png", testdata.PNG_BYTES), ("image/jpeg", testdata.JPEG_BYTES)]
        make_transcript(self.jsonl, [user_msg_with_images(pairs)])
        blocks = att.extract_latest_user_images(self.jsonl)
        self.assertEqual(len(blocks), 2)

    def test_latest_message_with_images_wins(self):
        first = user_msg_with_images([("image/png", testdata.PNG_BYTES)])
        second = user_msg_with_images([("image/jpeg", testdata.JPEG_BYTES)])
        make_transcript(self.jsonl, [first, second])
        blocks = att.extract_latest_user_images(self.jsonl)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["source"]["media_type"], "image/jpeg")

    def test_tool_result_user_messages_ignored(self):
        tool_msg = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "x", "content": "..."}],
            },
        }
        make_transcript(self.jsonl, [tool_msg, user_msg_with_images([("image/png", testdata.PNG_BYTES)])])
        blocks = att.extract_latest_user_images(self.jsonl)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["source"]["media_type"], "image/png")

    def test_no_image_attachment_returns_empty(self):
        make_transcript(self.jsonl, ['{"type":"user","message":{"role":"user","content":[{"type":"text","text":"你好"}]}}'])
        self.assertEqual(att.extract_latest_user_images(self.jsonl), [])

    def test_corrupt_lines_skipped(self):
        good = user_msg_with_images([("image/png", testdata.PNG_BYTES)])
        make_transcript(self.jsonl, ["这不是合法 JSON", "", good, "{broken"])
        blocks = att.extract_latest_user_images(self.jsonl)
        self.assertEqual(len(blocks), 1)


class TestBlockToBytes(unittest.TestCase):
    def _block(self, mime, raw):
        return {"type": "image", "source": {"type": "base64", "media_type": mime,
                                            "data": base64.b64encode(raw).decode()}}

    def test_png_with_media_type(self):
        mime, raw = att.block_to_bytes(self._block("image/png", testdata.PNG_BYTES))
        self.assertEqual(mime, "image/png")
        self.assertEqual(raw, testdata.PNG_BYTES)

    def test_jpeg_with_media_type(self):
        mime, raw = att.block_to_bytes(self._block("image/jpeg", testdata.JPEG_BYTES))
        self.assertEqual(mime, "image/jpeg")
        self.assertEqual(raw, testdata.JPEG_BYTES)

    def test_missing_media_type_sniffed_by_magic_bytes(self):
        block = {"type": "image", "source": {"type": "base64",
                                             "data": base64.b64encode(testdata.PNG_BYTES).decode()}}
        mime, raw = att.block_to_bytes(block)
        self.assertEqual(mime, "image/png")
        self.assertEqual(raw, testdata.PNG_BYTES)

    def test_unknown_media_type_sniffed_by_magic_bytes(self):
        block = {"type": "image", "source": {"type": "base64", "media_type": "application/octet-stream",
                                             "data": base64.b64encode(testdata.WEBP_BYTES).decode()}}
        self.assertEqual(att.block_to_bytes(block)[0], "image/webp")

    def test_bad_base64_raises_exit_3(self):
        block = {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": "!!!不是base64!!!"}}
        with self.assertRaises(att.AttachmentError) as ctx:
            att.block_to_bytes(block)
        self.assertEqual(ctx.exception.exit_code, 3)

    def test_non_base64_source_raises(self):
        block = {"type": "image", "source": {"type": "url", "url": "https://example.com/x.png"}}
        with self.assertRaises(att.AttachmentError) as ctx:
            att.block_to_bytes(block)
        self.assertEqual(ctx.exception.exit_code, 3)

    def test_unrecognizable_content_raises(self):
        block = {"type": "image", "source": {"type": "base64",
                                             "data": base64.b64encode(testdata.TEXT_BYTES).decode()}}
        with self.assertRaises(att.AttachmentError) as ctx:
            att.block_to_bytes(block)
        self.assertEqual(ctx.exception.exit_code, 3)


class TestSaveAndCleanup(TranscriptFixture):
    def test_save_creates_files_with_correct_content(self):
        blocks = user_msg_with_images([
            ("image/png", testdata.PNG_BYTES),
            ("image/jpeg", testdata.JPEG_BYTES),
        ])["message"]["content"][:2]
        saved = att.save_images(blocks, self.sid, temp_root=self.temp_root)
        self.assertEqual(len(saved), 2)
        for p in saved:
            self.assertTrue(p.exists())
            self.assertEqual(p.parent, self.temp_root)
        self.assertEqual(saved[0].suffix, ".png")
        self.assertEqual(saved[1].suffix, ".jpg")
        self.assertEqual(saved[0].read_bytes(), testdata.PNG_BYTES)

    def test_save_uses_sanitized_token_for_unexpanded_session_var(self):
        blocks = user_msg_with_images([("image/png", testdata.PNG_BYTES)])["message"]["content"][:1]
        saved = att.save_images(blocks, "${CLAUDE_SESSION_ID}", temp_root=self.temp_root)
        self.assertNotIn("${", saved[0].name)          # 不允许出现未展开的变量语法
        self.assertIn("CLAUDE_SESSION", saved[0].name)  # 保留可读的会话标记（清洗后截断到 16 字符）

    def test_cleanup_removes_only_temp_files(self):
        blocks = user_msg_with_images([("image/png", testdata.PNG_BYTES)])["message"]["content"][:1]
        saved = att.save_images(blocks, self.sid, temp_root=self.temp_root)
        outside = self.tmp / "outside.png"
        outside.write_bytes(testdata.PNG_BYTES)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            removed = att.cleanup([str(saved[0]), str(outside)], temp_root=self.temp_root)
        self.assertEqual(removed, 1)
        self.assertFalse(saved[0].exists())      # 临时文件已删除
        self.assertTrue(outside.exists())        # 目录外文件拒绝删除
        self.assertIn("拒绝删除", err.getvalue())


class TestMainCli(TranscriptFixture):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = att.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_extract_success_prints_paths(self):
        make_transcript(self.jsonl, [user_msg_with_images([("image/png", testdata.PNG_BYTES)])])
        code, out, err = self._run([self.sid])
        self.assertEqual(code, 0)
        paths = [p for p in out.splitlines() if p.strip()]
        self.assertEqual(len(paths), 1)
        self.assertTrue(Path(paths[0]).exists())
        # stdout 只有路径，不含聊天内容
        self.assertNotIn("帮我看看", out)

    def test_no_images_returns_2(self):
        make_transcript(self.jsonl, ['{"type":"user","message":{"role":"user","content":[{"type":"text","text":"hi"}]}}'])
        code, _, err = self._run([self.sid])
        self.assertEqual(code, 2)
        self.assertIn("没有找到图片附件", err)

    def test_session_not_found_returns_1(self):
        # 存档过旧 + session 不匹配 -> 无法定位
        make_transcript(self.jsonl, ["{}"])
        one_hour_ago = time.time() - att.FALLBACK_RECENT_SECONDS - 60
        os.utime(self.jsonl, (one_hour_ago, one_hour_ago))
        code, _, err = self._run(["no-such-id"])
        self.assertEqual(code, 1)
        self.assertIn("无法定位", err)

    def test_decode_failure_returns_3(self):
        bad = {
            "type": "user",
            "message": {"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": "!!!bad base64!!!"}},
                {"type": "text", "text": "图"},
            ]},
        }
        make_transcript(self.jsonl, [bad])
        code, _, err = self._run([self.sid])
        self.assertEqual(code, 3)
        self.assertIn("base64 解码失败", err)

    def test_cleanup_mode_removes_files(self):
        make_transcript(self.jsonl, [user_msg_with_images([("image/png", testdata.PNG_BYTES)])])
        code, out, _ = self._run([self.sid])
        self.assertEqual(code, 0)
        path = out.splitlines()[0].strip()
        self.assertTrue(Path(path).exists())
        code2, _, _ = self._run(["--cleanup", path])
        self.assertEqual(code2, 0)
        self.assertFalse(Path(path).exists())


if __name__ == "__main__":
    unittest.main()
