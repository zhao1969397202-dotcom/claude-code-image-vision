"""测试用图片数据（内存常量，仓库不提交二进制图片文件）。

这些字节只用于本地客户端逻辑测试（魔数识别、base64 编码、请求体结构），
不会发送给真实 API。
"""

import base64

# 1x1 有效 PNG
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

# 以下三个只需满足魔数前缀（本地测试不真正解码图片）
JPEG_BYTES = b"\xff\xd8\xff" + b"fake-jpeg-body-for-local-tests"
GIF_BYTES = b"GIF89a" + b"fake-gif-body-for-local-tests"
WEBP_BYTES = b"RIFF\x00\x00\x00\x00WEBP" + b"fake-webp-body-for-local-tests"

# 非图片内容（用于验证拒绝逻辑）
TEXT_BYTES = "这是一段普通文本，不是图片。".encode("utf-8")
