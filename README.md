# Image Vision Skill

给 Claude Code 装上"眼睛"：基于 DeepSeek 官方视觉模型 **`deepseek-v4-flash-vision-exp`** 的全局图片理解 Skill。

主模型（如 DeepSeek-V4-Pro）负责理解任务、规划、推理，但主模型本身不具备视觉能力。本 Skill 让 Claude Code 在需要"看图"时，把**图片 + 用户问题**一起交给 DeepSeek 视觉模型分析，再根据返回的文字结果继续推理，给出最终回答。

**主模型配置完全不需要改动**，视觉模型只充当主模型的"眼睛"。

## 工作原理

```text
用户提问 + 图片（本地图片 / 项目图片 / 截图 / 任何可访问的图片路径）
        ↓
主模型（DeepSeek-V4-Pro）判断需要视觉能力
        ↓
自动调用本 Skill（或用户手动输入 /image-vision）
        ↓
运行本地脚本 vision.py，读取 config/vision_config.env 配置
        ↓
图片 base64 编码 + 用户问题原文，一起发送给视觉模型
        ↓
deepseek-v4-flash-vision-exp 返回文字分析结果
        ↓
主模型基于视觉结果继续推理
        ↓
最终回答用户
```

## 能力清单

- 普通图片理解与描述（照片、插图等）
- OCR：识别图片中的文字
- 代码截图分析（报错信息、代码逻辑等）
- 网页截图 / UI 截图分析
- 数学题截图分析
- 图表、表格、流程图分析
- 根据图片回答问题（图片 + 问题一起发送给视觉模型，针对问题分析）

## 目录结构

```text
vision/
├── README.md                        ← 本文件
├── LICENSE                          ← MIT 协议
├── .gitignore                       ← 忽略真实配置与本地文件
├── skill/                           ← ★ 真正安装到 Claude Code 的内容
│   ├── SKILL.md                     ← Skill 入口（触发条件 + 使用说明）
│   ├── bin/
│   │   ├── vision                   ← 启动器：自动使用内置运行时或系统 python
│   │   └── attachment               ← 附件提取启动器（同上）
│   ├── runtime/                     ← ★ 内置便携 Python 3.13 + Pillow（Windows），
│   │                                  用户无需安装 Python 即可使用
│   ├── config/
│   │   ├── vision_config.env.example ← 配置模板（GitHub 保留，无真实 Key）
│   │   └── vision_config.env         ← 真实配置（本地文件，你自己填写，不上传）
│   └── src/
│       ├── vision.py                ← 命令行入口：读配置、校验图片、打印结果
│       ├── api_client.py            ← DeepSeek Vision API 客户端（纯标准库）
│       ├── attachment.py            ← 附件适配层：从会话存档提取 VS Code 上传的图片
│       └── preprocess.py            ← 大图片自动缩放/压缩（超限才处理，原图不改动）
└── tests/                           ← 单元测试（无需真实 API Key）
```

## 安装

**只需要安装 `skill/` 目录**（README、LICENSE、tests 不需要复制）。

**无需安装 Python**：`skill/runtime/` 内置便携 Python 3.13 + Pillow（Windows 版），
`bin/` 启动器会自动优先使用它，与系统 Python、项目虚拟环境完全隔离，互不影响。
（macOS / Linux 使用系统 `python3`；未装 Pillow 时仅"大图片自动处理"不可用，其余功能正常。）

1. 把仓库里的 `skill/` 文件夹复制到 Claude Code 全局 Skill 目录：

   ```text
   C:\Users\<你的用户名>\.claude\skills\image-vision\
   ```

   Windows PowerShell：

   ```powershell
   Copy-Item -Recurse -Force <仓库路径>\skill "$env:USERPROFILE\.claude\skills\image-vision"
   ```

   macOS / Linux：

   ```bash
   cp -r <仓库路径>/skill ~/.claude/skills/image-vision
   ```

2. 配置视觉模型（见下一节，**必须填写 API Key**）。

3. 重启 Claude Code（或新开一个会话）。

4. 直接对 Claude Code 发图提问，或手动输入 `/image-vision`。

最终目录结构：

```text
C:\Users\<你的用户名>\.claude\
└── skills\
    └── image-vision\
        ├── SKILL.md
        ├── bin\
        │   ├── vision
        │   └── attachment
        ├── runtime\                ← 内置 Python + Pillow（Windows）
        ├── config\
        │   ├── vision_config.env.example
        │   └── vision_config.env
        └── src\
            ├── vision.py
            ├── api_client.py
            ├── attachment.py
            └── preprocess.py
```

## 配置

**所有 API 配置集中在一个文件，唯一位置：**

```text
skill/config/vision_config.env
```

不需要去源代码（vision.py / api_client.py）或 README 中寻找配置。用 VS Code 等任意编辑器直接打开修改，保存即生效。

| 配置项 | 必填 | 说明 |
|---|---|---|
| `VISION_API_KEY` | ✅ 必填 | 你的 DeepSeek API Key，获取地址：https://platform.deepseek.com/api_keys |
| `VISION_API_BASE_URL` | 可选 | API 地址，默认 `https://api.deepseek.com`，一般不用改 |
| `VISION_MODEL` | 可选 | 视觉模型名称，默认 `deepseek-v4-flash-vision-exp` |
| `VISION_IMAGE_DETAIL` | 可选 | 图片精度：`auto`（推荐）/ `low`（最快最省）/ `high` / `original` |
| `VISION_TIMEOUT_SECONDS` | 可选 | 单次请求超时秒数，默认 `120` |

GitHub 仓库中只有模板文件 `vision_config.env.example`，不含真实 Key。真实文件 `vision_config.env` 已被 `.gitignore` 忽略，**永远不会上传 GitHub**。

## 使用方法

### 自动调用

不需要任何特殊命令，正常提问即可，Claude Code 会根据任务自动调用本 Skill：

```text
"帮我看看 D:\screenshots\error.png 这张报错截图为什么报错"
"识别 ./docs/scan.png 里的文字"
"分析这个图表的趋势：chart.png"
"这张数学题截图怎么做？img/math.jpg"
```

### 手动调用

输入：

```text
/image-vision
```

然后提供图片路径和你的问题。

### 多张图片

可以一次提供多张图片，视觉模型会一起分析：

```text
"对比 before.png 和 after.png 两张 UI 截图，说明差异"
```

### VS Code "+" 上传的图片附件

在 VS Code 侧边栏点击输入框旁的 "+" 上传图片后直接提问（例如"帮我看看这张图片"），
Skill 会自动从**当前会话存档**中提取图片附件进行分析，无需手动提供文件路径。

原理：上传的图片以 base64 形式保存在当前会话记录中，`attachment.py` 只提取图片字节，
解码保存到**系统临时目录**（分析完成后自动清理），再交给 `vision.py` 分析。
提取失败时自动回退：请你提供图片文件路径。

## 支持的图片格式

| 格式 | 支持 |
|---|---|
| PNG | ✅ |
| JPG / JPEG | ✅ |
| WEBP | ✅ |
| GIF | ✅ |

格式按**文件实际内容**判断（不看文件名后缀）。

官方限制（以 DeepSeek 官方文档为准）：

- 单张图片 ≤ 32MiB（base64 / URL 方式）
- 单边最大 8192 像素（15 张及以上时降至 4096）
- 单次请求最多 600 张图片
- 单张图片计费最多 384 tokens

## 更换视觉模型

只需要修改配置文件 `skill/config/vision_config.env` 中的一行：

```env
VISION_MODEL=deepseek-v4-flash-vision-exp
```

换成其他模型名即可。换 API 地址改 `VISION_API_BASE_URL`，换 Key 改 `VISION_API_KEY`。**不需要改动任何代码。**

## 常见问题

**Q：Skill 没有被自动触发？**
A：确认安装位置为 `~/.claude/skills/image-vision/SKILL.md`（不是仓库里的 `skill/`），然后重启 Claude Code；或手动输入 `/image-vision`。

**Q：提示"请先填写 VISION_API_KEY"？**
A：打开 `skill/config/vision_config.env`，在 `VISION_API_KEY=` 后面填上你的 DeepSeek API Key。

**Q：提示"不支持的图片格式"？**
A：仅支持 PNG / JPEG / GIF / WEBP，请转换格式后重试。

**Q：API 返回 400 错误？**
A：多为图片过大（单张 > 32MiB、单边 > 8192px）或格式不符，压缩或转换后重试。

**Q：请求超时？**
A：在配置文件中调大 `VISION_TIMEOUT_SECONDS`。

**Q：需要安装 Python 依赖吗？**
A：**不需要（Windows）**。Skill 内置便携 Python 3.13 + Pillow（`skill/runtime/`），
clone 仓库即可运行，与系统 Python / 项目虚拟环境完全隔离。
macOS / Linux 使用系统 `python3`（脚本本身是标准库零依赖；未装 Pillow 时仅大图自动处理不可用）。

**Q：配置文件在哪里？**
A：唯一位置 `skill/config/vision_config.env`，不用翻源代码。

**Q：VS Code 里点 "+" 上传的图片能识别吗？**
A：能。Skill 会从当前会话存档中自动提取附件图片（`attachment.py`），无需提供路径。
提取失败时会提示你提供文件路径。

## 安全说明

- 真实配置文件 `vision_config.env` 包含 API Key，已被 `.gitignore` 忽略，**严禁提交**。
- GitHub 仓库只保留 `vision_config.env.example` 模板（无真实 Key）。
- 上传 GitHub 前请检查 `git status`，确认真实配置文件未被 Git 跟踪。

## 路线图

- [ ] 提供只含 `skill/` 内容的 ZIP 下载包（当前请直接下载仓库并复制 `skill/` 目录）

## License

[MIT](LICENSE) © 2026 jinha
