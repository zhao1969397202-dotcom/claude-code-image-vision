"""大图片自动处理（发送 DeepSeek Vision API 之前的本地预处理）。

原则：
- 图片符合 DeepSeek 官方限制时不做任何处理（绝不无意义地降低质量）
- 超过限制时自动生成缩放/压缩的临时副本，绝不修改用户原始图片
- 临时文件只写入系统 TEMP 专属子目录（%TEMP%\\claude-image-vision\\preprocessed）
- 优先使用 Pillow（LANCZOS 高质量缩放）；未安装 Pillow 时，Windows 用系统自带 GDI+
- 不使用 DeepSeek Files API

官方限制（DeepSeek Vision 文档）：
- 单张图片 ≤ 32MiB（base64 / URL 方式）
- 单边 ≤ 8192px（同请求 ≥ 15 张时 4096px）
- 请求体 ≤ 48MiB（base64 计入）→ 按 ~44MiB 总量预算预留余量

清晰度策略：输出优先 PNG（无损，保护截图/OCR/代码截图/表格/图表的文字清晰度），
PNG 仍超限时才改用 JPEG（质量 85）；仍超限则逐步缩小尺寸，最多尝试 4 轮。
"""

import struct
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from api_client import MAX_IMAGE_BYTES, VisionImageError, detect_image_format

# 像素上限（官方：单边 8192；同请求 ≥15 张时 4096）
MAX_SIDE = 8192
MAX_SIDE_MANY = 4096
MANY_COUNT = 15

# 请求体总量预算（官方 48MiB，base64 后计入，预留余量）
BODY_BUDGET_BYTES = 44 * 1024 * 1024

# 单张图片最多尝试几轮缩放/压缩
MAX_ATTEMPTS = 4

# 临时处理文件专属目录（系统 TEMP 内，绝不写入项目/Skill 目录）
TEMP_ROOT = Path(tempfile.gettempdir()) / "claude-image-vision" / "preprocessed"


class PreprocessError(VisionImageError):
    """图片预处理失败（无法缩放/压缩到限制以内）。"""


# ---------------------------------------------------------------- 像素尺寸解析
# 只用标准库解析头部，不依赖第三方库（Pillow 仅在需要实际缩放时使用）

def get_image_dimensions(data: bytes, mime: str) -> tuple:
    """按格式解析图片像素尺寸（宽, 高）。"""
    if mime == "image/png":
        if len(data) < 24:
            raise PreprocessError("PNG 数据不完整，无法读取尺寸")
        w, h = struct.unpack(">II", data[16:24])
        return w, h
    if mime == "image/gif":
        if len(data) < 10:
            raise PreprocessError("GIF 数据不完整，无法读取尺寸")
        w, h = struct.unpack("<HH", data[6:10])
        return w, h
    if mime == "image/jpeg":
        return _jpeg_dimensions(data)
    if mime == "image/webp":
        return _webp_dimensions(data)
    raise PreprocessError(f"不支持的图片格式：{mime}")


def _jpeg_dimensions(data: bytes) -> tuple:
    """扫描 JPEG 段，读取 SOF 标记中的宽高。"""
    if not data.startswith(b"\xff\xd8"):
        raise PreprocessError("JPEG 数据无效，无法读取尺寸")
    i = 2
    n = len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        # 跳过填充与无长度段
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg_len = struct.unpack(">H", data[i + 2 : i + 4])[0]
        # SOF0..SOF15（除 C4/C8/CC）
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h = struct.unpack(">H", data[i + 5 : i + 7])[0]
            w = struct.unpack(">H", data[i + 7 : i + 9])[0]
            return w, h
        i += 2 + seg_len
    raise PreprocessError("JPEG 中未找到尺寸信息")


def _webp_dimensions(data: bytes) -> tuple:
    """解析 WebP 三种编码（VP8X / VP8 / VP8L）的宽高。"""
    if not (data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
        raise PreprocessError("WebP 数据无效，无法读取尺寸")
    chunk = data[12:16]
    if chunk == b"VP8X":
        w = 1 + int.from_bytes(data[24:27], "little")
        h = 1 + int.from_bytes(data[27:30], "little")
        return w, h
    if chunk == b"VP8 ":
        if len(data) < 30:
            raise PreprocessError("WebP(VP8) 数据不完整，无法读取尺寸")
        hdr = data[29:39]  # 关键帧头（帧标记3+起始码3+大小3 之后）
        w = int.from_bytes(hdr[6:8], "little") & 0x3FFF
        h = int.from_bytes(hdr[8:10], "little") & 0x3FFF
        return w, h
    if chunk == b"VP8L":
        if len(data) < 25 or data[20] != 0x2F:
            raise PreprocessError("WebP(VP8L) 数据无效，无法读取尺寸")
        b0, b1, b2, b3 = data[21], data[22], data[23], data[24]
        w = 1 + (b0 | ((b1 & 0x3F) << 8))
        h = 1 + (((b1 & 0xC0) >> 6) | (b2 << 2) | ((b3 & 0x3F) << 10))
        return w, h
    raise PreprocessError("WebP 编码类型未知，无法读取尺寸")


# ---------------------------------------------------------------- 缩放/压缩后端

def _resize_pillow(path: str, out: Path, w: int, h: int, fmt: str) -> None:
    """Pillow：LANCZOS 高质量缩放。"""
    from PIL import Image  # 延迟导入：未安装时抛 ImportError，走 GDI+ 后备

    with Image.open(path) as im:
        if fmt == "jpeg":
            if im.mode == "RGBA":
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.getchannel("A"))
                im2 = bg
            else:
                im2 = im.convert("RGB")
        elif im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
            im2 = im.convert("RGBA")
        else:
            im2 = im.convert("RGB")
        resized = im2.resize((w, h), Image.LANCZOS)
        if fmt == "jpeg":
            resized.save(out, "JPEG", quality=85)
        else:
            resized.save(out, "PNG")


# Windows 系统自带 GDI+ 的 PowerShell 脚本（无 Pillow 时的后备，输出质量高）
_GDI_PS = r'''
param([string]$In, [string]$Out, [int]$W, [int]$H, [string]$Format = "Png")
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing
$src = [System.Drawing.Image]::FromFile($In)
$bmp = New-Object System.Drawing.Bitmap $W, $H
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
$g.DrawImage($src, 0, 0, $W, $H)
if ($Format -eq "Jpeg") {
  $codec = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq 'image/jpeg' }
  $ep = New-Object System.Drawing.Imaging.EncoderParameters 1
  $ep.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter ([System.Drawing.Imaging.Encoder]::Quality, [long]85)
  $bmp.Save($Out, $codec, $ep)
} else {
  $bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
}
$g.Dispose(); $bmp.Dispose(); $src.Dispose()
'''


def _resize_gdi(path: str, out: Path, w: int, h: int, fmt: str) -> None:
    """Windows GDI+（PowerShell System.Drawing）：高质量双三次缩放。"""
    if sys.platform != "win32":
        raise PreprocessError(
            "未安装 Pillow，且当前系统不支持 GDI+ 缩放；请安装 Pillow 或手动压缩图片"
        )
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    ps_file = TEMP_ROOT / "_resize.ps1"
    ps_file.write_text(_GDI_PS, encoding="utf-8")
    fmt_arg = "Jpeg" if fmt == "jpeg" else "Png"
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(ps_file),
             "-In", str(path), "-Out", str(out),
             "-W", str(w), "-H", str(h), "-Format", fmt_arg],
            capture_output=True, timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise PreprocessError(f"GDI+ 缩放失败：{e}") from e
    if proc.returncode != 0 or not Path(out).is_file():
        detail = (proc.stderr or proc.stdout).decode("utf-8", errors="replace").strip()[:200]
        raise PreprocessError(f"GDI+ 缩放失败：{detail or '未知错误'}")


def _run_resizer(path: str, out: Path, w: int, h: int, fmt: str) -> None:
    """缩放调度：优先 Pillow，未安装时 Windows 用 GDI+。"""
    try:
        _resize_pillow(path, out, w, h, fmt)
    except ImportError:
        _resize_gdi(path, out, w, h, fmt)
    except PreprocessError:
        raise
    except Exception as e:
        raise PreprocessError(f"图片缩放/压缩失败（Pillow）：{e}") from e


# ---------------------------------------------------------------- 处理流程

def process_image(path: str, max_side: int, max_bytes: int,
                  out_dir: Path = None) -> Path:
    """把一张超限图片处理为限制以内的临时副本，返回副本路径。"""
    if out_dir is None:
        out_dir = TEMP_ROOT
    out_dir.mkdir(parents=True, exist_ok=True)
    data = Path(path).read_bytes()
    mime = detect_image_format(data)
    w, h = get_image_dimensions(data, mime)

    scale = min(1.0, max_side / max(w, h))
    png_out = out_dir / f"pre_{uuid.uuid4().hex[:12]}.png"
    jpg_out = png_out.with_suffix(".jpg")
    result = None
    for _ in range(MAX_ATTEMPTS):
        nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
        # 1) 优先 PNG（无损，保护截图/OCR/代码截图/表格/图表的清晰度）
        _run_resizer(path, png_out, nw, nh, "png")
        if png_out.stat().st_size <= max_bytes:
            result = png_out
            break
        # 2) PNG 仍超限：同尺寸 JPEG（质量 85）
        _run_resizer(path, jpg_out, nw, nh, "jpeg")
        if jpg_out.stat().st_size <= max_bytes:
            result = jpg_out
            break
        scale *= 0.85
    if result is None:
        for leftover in (png_out, jpg_out):
            leftover.unlink(missing_ok=True)
        raise PreprocessError(
            f"图片自动处理后仍超过限制，请手动压缩：{path}（原图未改动）"
        )
    # 清理未采用的那份候选文件
    for candidate in (png_out, jpg_out):
        if candidate != result:
            candidate.unlink(missing_ok=True)
    return result


def _estimate_base64(paths: list) -> int:
    """估算 base64 编码后的总字节数（×4/3 上限估算）。"""
    total = 0
    for p in paths:
        try:
            total += Path(p).stat().st_size
        except OSError:
            pass
    return int(total * 4 / 3)


def prepare_images(paths: list) -> tuple:
    """逐张检查并处理，返回 (最终图片路径列表, 临时副本路径列表)。

    - 符合限制的原图直接使用，不做任何处理；
    - 超限图片各自独立处理，不影响其他正常图片；
    - 多图合计超过请求体预算时，继续压缩最大的图片；
    - 用户原始图片只读，绝不修改。
    """
    many = len(paths) >= MANY_COUNT
    max_side = MAX_SIDE_MANY if many else MAX_SIDE
    final, temps = [], []
    for p in paths:
        data = Path(p).read_bytes()
        mime = detect_image_format(data)
        w, h = get_image_dimensions(data, mime)
        if len(data) <= MAX_IMAGE_BYTES and max(w, h) <= max_side:
            final.append(p)  # 原图符合限制，不做任何处理
            continue
        proc = process_image(p, max_side, MAX_IMAGE_BYTES)
        final.append(str(proc))
        temps.append(str(proc))

    # 请求体总量预算（48MiB 官方上限，base64 计入，预留余量）
    for _ in range(len(final) * MAX_ATTEMPTS + 1):
        if _estimate_base64(final) <= BODY_BUDGET_BYTES:
            break
        largest = max(final, key=lambda x: Path(x).stat().st_size)
        proc = process_image(largest, max_side,
                             BODY_BUDGET_BYTES // max(1, len(final)))
        final[final.index(largest)] = str(proc)
        temps.append(str(proc))
    else:
        raise PreprocessError(
            "多张图片合计仍超过请求体限制，请减少图片数量或手动压缩"
        )
    return final, temps


def cleanup_temp_files(paths: list) -> int:
    """删除预处理临时文件（只允许删除 TEMP_ROOT 内的文件）。"""
    root = TEMP_ROOT.resolve()
    removed = 0
    for p in paths:
        try:
            target = Path(p).resolve()
        except OSError:
            continue
        if root not in target.parents:
            continue
        try:
            target.unlink()
            removed += 1
        except OSError:
            pass
    return removed
