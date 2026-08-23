"""DeepSeek Vision API 客户端。

仅使用 Python 标准库，零第三方依赖。

严格按 DeepSeek 官方文档实现：
https://api-docs.deepseek.com/guides/vision/

- 接口：POST {VISION_API_BASE_URL}/chat/completions（OpenAI 兼容 Chat Completions）
- 模型：deepseek-v4-flash-vision-exp（由配置文件 VISION_MODEL 指定）
- 图片：base64 编码为 data URL，放入 user 消息的 image_url 块
  （官方规定图片只能出现在 user 消息中，system/assistant 消息放图片会返回 400）
- 支持格式：PNG / JPEG / GIF / WEBP（按文件内容魔数判断，不看文件名）
- 限制：单张图片 ≤ 32MiB（base64 / URL 方式）、请求体 ≤ 48MiB、
  单边 ≤ 8192px、单请求 ≤ 600 张（大小与数量限制在本地校验，
  像素尺寸与请求体限制由 API 返回错误时透传给用户）
"""

import base64
import json
import urllib.error
import urllib.request

# 官方限制：base64 / URL 方式单张图片 ≤ 32MiB
MAX_IMAGE_BYTES = 32 * 1024 * 1024

# 官方支持的图片格式（按文件内容判断）
SUPPORTED_FORMATS = ("PNG", "JPEG", "GIF", "WEBP")


class VisionConfigError(Exception):
    """配置错误：配置文件缺失 / API Key 未填写等。"""


class VisionImageError(Exception):
    """图片错误：文件不存在 / 格式不支持 / 超过大小限制。"""


class VisionAPIError(Exception):
    """视觉 API 调用失败：网络 / 超时 / 官方返回错误。"""


def detect_image_format(data: bytes) -> str:
    """按文件内容魔数判断图片格式，返回 MIME 类型。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError(
        "不支持的图片格式，仅支持："
        + " / ".join(SUPPORTED_FORMATS)
        + "（按文件内容判断，与扩展名无关）"
    )


def encode_image(path: str) -> tuple:
    """读取图片文件，按内容识别格式并 base64 编码。返回 (mime, b64)。"""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise VisionImageError(f"无法读取图片文件：{path}（{e}）") from e
    if len(data) > MAX_IMAGE_BYTES:
        raise VisionImageError(
            f"图片超过 {MAX_IMAGE_BYTES // (1024 * 1024)}MiB 上限：{path}"
            f"（{len(data) / (1024 * 1024):.1f}MiB）"
        )
    try:
        mime = detect_image_format(data)
    except ValueError as e:
        raise VisionImageError(f"图片格式不支持：{path}（{e}）") from e
    return mime, base64.b64encode(data).decode("ascii")


def build_request_body(model: str, images: list, question: str, detail: str = "") -> dict:
    """按官方格式构造请求体：text 块 + image_url 块，全部放在 user 消息中。

    官方格式示例：
        {"role": "user", "content": [
            {"type": "text", "text": "..."},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
        ]}
    """
    content = []
    if question:
        content.append({"type": "text", "text": question})
    for path in images:
        mime, b64 = encode_image(path)
        image_url = {"url": f"data:{mime};base64,{b64}"}
        if detail:
            image_url["detail"] = detail
        content.append({"type": "image_url", "image_url": image_url})
    return {
        "model": model,
        "messages": [{"role": "user", "content": content}],
    }


def _format_http_error(code: int, body: str) -> str:
    """把官方返回的 HTTP 错误转成带提示的可读信息。"""
    msg = body
    try:
        data = json.loads(body)
        err = data.get("error") or {}
        if isinstance(err, dict):
            msg = err.get("message") or body
        elif isinstance(err, str):
            msg = err
    except (ValueError, AttributeError):
        pass
    hints = {
        400: "请求参数错误：请检查图片格式 / 大小（单张 ≤32MiB、单边 ≤8192px）",
        401: "API Key 无效或未填写，请检查 vision_config.env 中的 VISION_API_KEY",
        402: "账户余额不足，请前往 DeepSeek 平台充值",
        404: "模型或接口地址错误，请检查 VISION_MODEL 与 VISION_API_BASE_URL",
        429: "请求过于频繁或额度受限，请稍后重试",
        500: "DeepSeek 服务器错误，请稍后重试",
    }
    text = f"DeepSeek API 返回错误 {code}：{msg}"
    hint = hints.get(code)
    if hint:
        text += f"（{hint}）"
    return text


def call_vision_api(config: dict, image_paths: list, question: str) -> str:
    """调用 DeepSeek Vision API，返回视觉分析文本。

    config 字段：api_key / base_url / model / detail / timeout_seconds
    """
    url = f"{config['base_url'].rstrip('/')}/chat/completions"
    body = build_request_body(
        model=config["model"],
        images=image_paths,
        question=question,
        detail=config.get("detail", ""),
    )
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config['api_key']}",
        },
        method="POST",
    )
    timeout = config.get("timeout_seconds", 120)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise VisionAPIError(_format_http_error(e.code, err_body)) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise VisionAPIError(
            f"网络请求失败（{reason}）。请检查网络连接与 VISION_API_BASE_URL 配置"
        ) from e

    try:
        data = json.loads(raw)
        content = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise VisionAPIError(f"API 返回格式异常，无法解析分析结果：{raw[:200]}") from e
    if not content or not isinstance(content, str):
        raise VisionAPIError("API 返回的分析内容为空")
    return content.strip()
