---
name: image-vision
description: Analyze images using the DeepSeek vision model (deepseek-v4-flash-vision-exp).
  Triggers when the user asks what a picture/screenshot/photo shows（图片/截图/照片里是什么）,
  requests OCR or text extraction from images（识别图片中的文字）, analyzes code screenshots
  or error messages shown in images（代码截图/报错截图）, reads charts, diagrams, tables,
  flowcharts, math problems or UI/webpage screenshots（图表/流程图/表格/数学题/UI 截图）,
  or asks any question about image files (.png/.jpg/.jpeg/.webp/.gif). The skill runs a
  local Python script that sends the image together with the user's question to the
  DeepSeek vision API and returns a text analysis for the main model to continue reasoning.
allowed-tools: Bash, Glob
user-invocable: true
---

# Image Vision Skill

## 这个 Skill 只做一件事：图片理解

- 主模型负责理解任务、推理、最终回答；本 Skill 只是主模型的"眼睛"，**只负责图片理解 / 视觉分析**。
- 不是通用 DeepSeek Skill：不处理纯文本任务，不回答与图片无关的问题。
- 视觉模型一律使用 `config/vision_config.env` 中的 `VISION_MODEL`（默认 `deepseek-v4-flash-vision-exp`）。
  **不要调用其他模型来代替视觉模型，不要修改主模型配置。**

## 什么时候调用（description 已覆盖，此处重申）

用户任务涉及以下任一情况时调用本 Skill：

图片、截图、照片、OCR、识别图片中的文字、代码截图、报错截图、图表、
表格、流程图、数学题截图、网页/UI 截图、根据图片回答问题。

用户也可以手动输入 `/image-vision` 调用本 Skill。

## 核心规则：图片 + 用户问题必须一起发送

**永远不要只发送图片。** 每次调用都把「图片 + 用户的问题原文」一起交给视觉模型，
让视觉模型针对用户的问题进行分析。只有用户没有明确问题时，才省略 `-q`
（脚本会使用默认问题描述图片）。

## 调用方式

1. 确定图片路径：用户提供的路径、项目中的文件，或先用 Glob 查找。
   若用户只贴了图片附件而没有文件路径，先向用户确认路径。
2. 运行脚本（在任意目录运行均可，脚本会自动定位自己的配置文件）：

```bash
python "${CLAUDE_SKILL_DIR}/src/vision.py" "<图片1路径>" ["<图片2路径>" ...] -q "<用户问题原文>"
```

- Windows 上使用 `python`（不要用 `python3`）。
- 路径含空格或中文时保留引号；问题文本保持用户原文，不要改写。
- 多张图片一次传入（官方单请求最多 600 张）。
- 视觉模型的回答打印在 stdout；stderr 只用于进度和错误信息。

3. 读取 stdout 作为视觉分析结果，**基于它继续推理并给出最终回答**。
   可以简要说明哪些结论来自视觉模型。
4. 若视觉结果不足以回答问题，带着**同一张图片 + 更精确的问题**重新运行脚本
   （图片和问题始终一起发送）。

## VS Code "+" 上传图片：附件分支

通过 "+" 上传的图片**不会产生文件路径**（对话里只有一个占位符，如"不支持的图片"）。
检测到以下情况时走附件分支：
- 用户消息里出现图片附件占位符（如"不支持的图片"）；
- 用户说"上传了图片 / 贴了图 / 看下这张图"，但**没有任何文件路径**。

1. 提取附件为临时图片（只读取当前会话存档，只提取图片字节，不读取聊天内容）：

```bash
python "${CLAUDE_SKILL_DIR}/src/attachment.py" "${CLAUDE_SESSION_ID}"
```

- 成功：stdout 输出临时图片路径（多张则每行一个），退出码 0。
- 失败（退出码 1/2/3）：读取 stderr 原因，**回退到路径方案**——
  请用户提供图片文件路径，或把图片保存到项目目录后告知文件名。

2. 把临时路径当作普通图片路径，与用户问题原文一起调用视觉模型（规则与上面完全一致）：

```bash
python "${CLAUDE_SKILL_DIR}/src/vision.py" "<临时路径1>" ["<临时路径2>" ...] -q "<用户问题原文>"
```

3. 视觉分析完成后清理临时图片：

```bash
python "${CLAUDE_SKILL_DIR}/src/attachment.py" --cleanup "<临时路径1>" ["<临时路径2>" ...]
```

注意：
- 用户直接给了文件路径时，继续用上面的常规路径方案，**不要经过 attachment.py**。
- attachment.py 不会把聊天记录发送给视觉模型；交给 vision.py 的只有图片文件本身。

## 退出码与错误处理

vision.py：

| 退出码 | 含义 | 处理方式 |
|---|---|---|
| 0 | 成功 | stdout 即视觉分析结果 |
| 1 | 配置错误（Key 未填 / 配置文件缺失） | 提示用户用 VS Code 打开 `config/vision_config.env` 填写 `VISION_API_KEY` |
| 2 | 图片错误（不存在 / 格式不支持 / 超过 32MiB） | 把 stderr 的具体原因转达用户；支持 PNG / JPEG / GIF / WEBP |
| 3 | API 错误（网络 / 超时 / 官方错误码） | 把 stderr 中的官方错误信息转达用户，必要时提示检查 `VISION_API_BASE_URL`、余额或稍后重试 |

attachment.py（附件提取）：

| 退出码 | 含义 | 处理方式 |
|---|---|---|
| 0 | 成功 | stdout 即临时图片路径 |
| 1 | 无法定位当前会话存档 | 回退到路径方案：请用户提供图片路径 |
| 2 | 当前会话中没有图片附件 | 回退到路径方案：请用户提供图片路径 |
| 3 | 提取失败（base64 解码 / 格式无法识别 / 写入失败） | 回退到路径方案：请用户提供图片路径 |

## 配置：全部集中在一个文件

- 唯一配置位置：`${CLAUDE_SKILL_DIR}/config/vision_config.env`
  （KEY=VALUE 纯文本文件，用 VS Code 直接编辑，保存即生效）。
- 更换视觉模型、API 地址、API Key：**只改这个文件**，
  不要修改 `vision.py` / `api_client.py` / 本文件。
- 真实 API Key 只存在于 `vision_config.env`：
  **永远不要**把 Key 写进本文件或代码，也不要写进 Claude Code 的 settings.json / 环境变量。
