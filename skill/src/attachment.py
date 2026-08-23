"""从当前 Claude Code 会话的 JSONL 存档中提取图片附件。

背景：
VS Code 侧边栏通过 "+" 上传的图片会成为对话中的 base64 图片内容块，
不会产生文件路径（主模型无视觉时上下文里只有占位符文本）。
图片的原始字节仍保存在本机会话存档中：

    ~/.claude/projects/<项目哈希>/<会话ID>.jsonl

本脚本从【当前会话】的存档里提取【最近一条含图片的用户消息】中的图片，
解码后保存为系统临时目录下的文件，输出路径供 vision.py 使用。

安全约定：
- 只读取当前会话的 JSONL（优先使用 Claude Code 官方变量 ${CLAUDE_SESSION_ID} 定位）
- 只提取图片 base64 数据，绝不读取、打印或转发其他聊天内容
- 临时文件只写入系统临时目录下的专属子目录，绝不写入项目目录或 Skill 目录
- 提供 --cleanup 子命令，供视觉分析完成后清理临时图片

用法：
    python attachment.py <session_id>          # 提取附件图片，stdout 每行输出一个临时路径
    python attachment.py --cleanup <path ...>  # 清理临时图片
    （<session_id> 为空/未展开时自动回退：查找 60 分钟内最新修改的会话存档）

退出码：
    0 成功
    1 无法定位会话（session 未找到且无可用兜底）
    2 当前会话中没有找到图片附件
    3 提取失败（base64 解码失败 / 图片格式无法识别 / 写入失败）
"""

import argparse
import base64
import binascii
import json
import re
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Optional

# api_client.py 与本文件同目录，直接运行本脚本时同目录会自动加入 sys.path
from api_client import detect_image_format

HOME_CLAUDE = Path.home() / ".claude"
PROJECTS_DIR = HOME_CLAUDE / "projects"

# 临时图片专属子目录（位于系统临时目录内）
TEMP_ROOT = Path(tempfile.gettempdir()) / "claude-image-vision"

# 兜底定位：只接受最近多少秒内修改过的会话存档
FALLBACK_RECENT_SECONDS = 60 * 60

# 图片 MIME -> 临时文件扩展名（官方支持格式）
MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


class AttachmentError(Exception):
    """附件提取错误，携带面向用户的说明与退出码。"""

    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


def _list_session_files(projects_dir: Path) -> list:
    """列出所有候选会话存档（自然排除子代理存档等深层文件）。"""
    if not projects_dir.is_dir():
        return []
    files = []
    for f in projects_dir.glob("*/*.jsonl"):
        if "subagents" in f.parts:
            continue
        files.append(f)
    return files


def find_session_file(session_id: str, projects_dir: Optional[Path] = None) -> Path:
    """定位当前会话的 JSONL 存档。

    优先按 ${CLAUDE_SESSION_ID} 精确匹配；为空、未展开或找不到时，
    回退为"60 分钟内最新修改的会话存档"。
    """
    if projects_dir is None:
        projects_dir = PROJECTS_DIR
    sid = (session_id or "").strip()
    if sid:
        target = f"{sid}.jsonl"
        for f in _list_session_files(projects_dir):
            if f.name == target:
                return f
        # 精确匹配失败，继续走兜底
    candidates = []
    now = time.time()
    for f in _list_session_files(projects_dir):
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        if now - mtime <= FALLBACK_RECENT_SECONDS:
            candidates.append((mtime, f))
    if not candidates:
        raise AttachmentError(
            "无法定位当前会话存档（session id 未匹配，且 60 分钟内没有新修改的会话记录）",
            exit_code=1,
        )
    return max(candidates)[1]


def extract_latest_user_images(jsonl_path: Path) -> list:
    """从存档中提取【最近一条含图片的用户消息】的全部图片块。

    只解析结构，绝不读取或输出文字内容；损坏/非法的行直接跳过。
    """
    try:
        lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise AttachmentError(f"无法读取会话存档：{jsonl_path}（{e}）", exit_code=3) from e
    latest_blocks = []
    for line in lines:
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue  # 损坏/非法行：跳过
        if obj.get("type") != "user":
            continue
        message = obj.get("message") or {}
        content = message.get("content")
        if not isinstance(content, list):
            continue
        blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "image"]
        if blocks:
            latest_blocks = blocks  # 始终保留最后一条含图片的消息
    return latest_blocks


def block_to_bytes(block: dict) -> tuple:
    """把图片块解码为 (mime, bytes)。media_type 缺失或未知时按文件魔数识别。"""
    source = block.get("source") or {}
    if source.get("type") != "base64":
        raise AttachmentError("图片块不是 base64 形式，无法提取", exit_code=3)
    data_b64 = source.get("data") or ""
    try:
        raw = base64.b64decode(data_b64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise AttachmentError(f"图片 base64 解码失败：{e}", exit_code=3) from e
    mime = source.get("media_type") or ""
    if mime not in MIME_EXT:
        try:
            mime = detect_image_format(raw)
        except ValueError as e:
            raise AttachmentError(f"图片格式无法识别：{e}", exit_code=3) from e
    return mime, raw


def _safe_session_token(session_id: str) -> str:
    """把会话 ID 清洗为可用于文件名的片段。"""
    token = re.sub(r"[^A-Za-z0-9_-]", "", session_id)[:16]
    return token or "session"


def save_images(blocks: list, session_id: str, temp_root: Optional[Path] = None) -> list:
    """把图片块保存为临时文件，返回路径列表。"""
    if temp_root is None:
        temp_root = TEMP_ROOT
    temp_root.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time())
    saved = []
    for index, block in enumerate(blocks, 1):
        mime, raw = block_to_bytes(block)
        name = f"ccv_{_safe_session_token(session_id)}_{stamp}_{index}{MIME_EXT[mime]}"
        out = temp_root / name
        try:
            out.write_bytes(raw)
        except OSError as e:
            raise AttachmentError(f"写入临时文件失败：{out}（{e}）", exit_code=3) from e
        saved.append(out)
    return saved


def cleanup(paths: list, temp_root: Optional[Path] = None) -> int:
    """删除临时图片。只允许删除 temp_root 目录内的文件，返回删除数量。"""
    if temp_root is None:
        temp_root = TEMP_ROOT
    root = temp_root.resolve()
    removed = 0
    for p in paths:
        target = Path(p).resolve()
        if root not in target.parents:
            print(f"[跳过] 不在临时目录内，拒绝删除：{p}", file=sys.stderr)
            continue
        try:
            target.unlink()
            removed += 1
        except OSError as e:
            print(f"[跳过] 删除失败：{p}（{e}）", file=sys.stderr)
    return removed


def main(argv=None) -> int:
    # Windows 下强制 UTF-8 输出，避免中文乱码
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--cleanup":
        removed = cleanup(args[1:])
        print(f"[attachment] 已清理 {removed} 个临时图片文件", file=sys.stderr)
        return 0

    session_id = args[0] if args else ""
    try:
        jsonl = find_session_file(session_id)
        blocks = extract_latest_user_images(jsonl)
        if not blocks:
            raise AttachmentError("当前会话中没有找到图片附件", exit_code=2)
        saved = save_images(blocks, session_id or jsonl.stem)
    except AttachmentError as e:
        print(f"[附件错误] {e}", file=sys.stderr)
        return e.exit_code
    except Exception:
        print("[意外错误] 发生未预期的异常：", file=sys.stderr)
        traceback.print_exc()
        return 3
    print(f"[attachment] 已提取 {len(saved)} 张图片（来源：当前会话存档）", file=sys.stderr)
    for p in saved:
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
