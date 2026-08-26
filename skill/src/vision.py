"""Image Vision Skill 命令行入口。

用法：
    python vision.py <图片1> [图片2 ...] -q "<用户问题>"

流程：
    vision_config.env -> 读取配置 -> 校验图片 -> 调用 api_client
    -> deepseek-v4-flash-vision-exp -> 打印分析结果到 stdout

约定：
    - stdout 只打印视觉模型的回答原文（供主模型读取后继续推理）
    - 错误信息走 stderr，退出码：
        0 成功
        1 配置错误（配置文件缺失 / API Key 未填写）
        2 图片错误（不存在 / 格式不支持 / 超过大小限制）
        3 API 错误（网络失败 / 超时 / 官方返回错误）

零第三方依赖：配置文件解析只用 Python 标准库（不使用 python-dotenv）。
"""

import argparse
import os
import sys
import traceback
from pathlib import Path
from typing import Optional

# 便携运行时（python.org embeddable，隔离模式）不会把脚本所在目录加入 sys.path，
# 这里手动补上，保证 skill/src 内的模块互引在任何运行方式下都可用
_SRC_DIR = str(Path(__file__).resolve().parent)
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from api_client import (
    MAX_IMAGE_BYTES,
    VisionAPIError,
    VisionConfigError,
    VisionImageError,
    call_vision_api,
)
from preprocess import cleanup_temp_files, prepare_images

# Skill 根目录（src/ 的上一级），配置文件固定在其 config/ 子目录下，
# 不依赖 Skill 被安装在哪个位置
SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_DIR / "config" / "vision_config.env"

DEFAULT_QUESTION = "请详细描述这张图片的内容。"

# 与模板一致的占位 Key 值，防止用户忘了替换就调用 API
PLACEHOLDER_KEYS = ("your_api_key_here", "your-api-key-here", "your_api_key", "changeme")


def load_config(config_path: Optional[Path] = None) -> dict:
    """解析 vision_config.env（KEY=VALUE 格式，支持 # 注释与空行，纯标准库实现）。"""
    if config_path is None:
        config_path = CONFIG_PATH  # 运行时解析，便于测试注入
    if not config_path.is_file():
        raise VisionConfigError(
            f"配置文件不存在：{config_path}\n"
            "请复制同目录的 vision_config.env.example 为 vision_config.env 并填写 API Key。"
        )
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as e:
        raise VisionConfigError(f"无法读取配置文件：{config_path}（{e}）") from e

    values = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            values[key] = value
    return _finalize_config(values, config_path)


def _finalize_config(values: dict, config_path: Path) -> dict:
    """补默认值并校验必填项。"""
    api_key = values.get("VISION_API_KEY", "").strip()
    if not api_key or api_key.lower() in PLACEHOLDER_KEYS:
        raise VisionConfigError(
            "VISION_API_KEY 未填写。请用 VS Code 打开配置文件：\n"
            f"  {config_path}\n"
            "在 VISION_API_KEY= 后面填上你的 DeepSeek API Key"
            "（账号级 Key 即可，无需单独申请视觉模型 Key）。"
        )
    try:
        timeout = int(values.get("VISION_TIMEOUT_SECONDS", "120").strip())
    except ValueError:
        timeout = 120
    return {
        "api_key": api_key,
        "base_url": values.get("VISION_API_BASE_URL", "https://api.deepseek.com").strip(),
        "model": values.get("VISION_MODEL", "deepseek-v4-flash-vision-exp").strip(),
        "detail": values.get("VISION_IMAGE_DETAIL", "auto").strip(),
        "timeout_seconds": max(1, timeout),
    }


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="vision.py",
        description="把图片和问题一起发送给 DeepSeek 视觉模型，打印文字分析结果。",
    )
    parser.add_argument("images", nargs="+", help="图片路径，可多个（PNG/JPEG/GIF/WEBP）")
    parser.add_argument("-q", "--question", help="要问视觉模型的问题；不填则只描述图片")
    return parser.parse_args(argv)


def split_question(ns: argparse.Namespace) -> tuple:
    """-q 未提供时：仅当位置参数 ≥2 个、且前面至少有一个真实存在的图片文件时，
    才把最后一个参数当作问题文本；否则全部按图片路径处理。
    （避免"不存在的图片路径"被误当成问题，导致图片校验被绕过）"""
    images = list(ns.images)
    question = ns.question
    if (
        question is None
        and len(images) >= 2
        and any(Path(p).is_file() for p in images[:-1])
    ):
        question = images.pop()
    if not images:
        raise VisionImageError("至少需要一个图片路径")
    return images, (question or DEFAULT_QUESTION).strip()


def validate_image_paths(paths: list) -> None:
    for path in paths:
        if not Path(path).is_file():
            raise VisionImageError(f"图片文件不存在：{path}")
        size = os.path.getsize(path)
        if size > MAX_IMAGE_BYTES:
            raise VisionImageError(
                f"图片超过 {MAX_IMAGE_BYTES // (1024 * 1024)}MiB 上限：{path}"
                f"（{size / (1024 * 1024):.1f}MiB）"
            )


def main(argv=None) -> int:
    # Windows 下强制 UTF-8 输出，避免中文乱码
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        ns = parse_args(argv)
        images, question = split_question(ns)
        config = load_config()
        validate_image_paths(images)
        # 大图片自动处理：超限图片生成临时缩放/压缩副本，原图只读、绝不修改
        prepared, temp_files = prepare_images(images)
        try:
            if temp_files:
                print(
                    f"[vision] 有 {len(temp_files)} 张图片超过限制，"
                    "已自动缩放/压缩生成临时副本（原图未改动）",
                    file=sys.stderr,
                )
            print(
                f"[vision] 正在调用 {config['model']} 分析 {len(prepared)} 张图片...",
                file=sys.stderr,
            )
            result = call_vision_api(config, prepared, question)
        finally:
            # 分析完成后清理临时副本
            cleanup_temp_files(temp_files)
        print(result)
        return 0
    except VisionConfigError as e:
        print(f"[配置错误] {e}", file=sys.stderr)
        return 1
    except VisionImageError as e:
        print(f"[图片错误] {e}", file=sys.stderr)
        return 2
    except VisionAPIError as e:
        print(f"[API 错误] {e}", file=sys.stderr)
        return 3
    except Exception:
        print("[意外错误] 发生未预期的异常：", file=sys.stderr)
        traceback.print_exc()
        return 3


if __name__ == "__main__":
    sys.exit(main())
