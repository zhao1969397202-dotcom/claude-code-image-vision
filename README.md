# Image Vision Skill

给 Claude Code 装上"眼睛"：基于 DeepSeek 官方视觉模型 **`deepseek-v4-flash-vision-exp`** 的全局图片理解 Skill。

**Windows 专用 · 无需安装 Python · 支持 VS Code "+" 图片附件 · 支持大图片自动处理**

主模型（如 DeepSeek-V4-Pro）负责理解任务、规划与推理，但主模型本身不具备视觉能力。
本 Skill 在需要"看图"时，把**图片 + 用户问题原文**一起交给 DeepSeek 视觉模型分析，
再将文字分析结果交回主模型继续推理，给出最终回答。

**主模型配置完全不需要改动**，视觉模型只充当主模型的"眼睛"。

## Windows 定位

- 当前版本**仅支持 Windows**；
- `skill/runtime/` 内置便携 Python 3.13 + Pillow（Windows 版本），
  用户**无需额外安装 Python**；
- `bin/vision` 和 `bin/attachment` 会**优先使用 Skill 自带的 runtime**，
  不依赖用户电脑上的系统 Python，也不依赖项目虚拟环境；
- Pillow 已随 runtime 一起提供，大图片自动处理功能开箱即用；
- 用户只需要配置自己的 DeepSeek Vision API Key。

## 工作原理

```text
用户提问 + 本地图片（本地文件 / 项目内文件 / 截图等 Claude Code 能访问到的本地图片文件）
        ↓
主模型（DeepSeek-V4-Pro）判断需要视觉能力
        ↓
自动调用本 Skill（或用户手动输入 /image-vision）
        ↓
bin/vision 启动器（优先使用 Skill 自带的便携 Python runtime）运行 vision.py
        ↓
读取 config/vision_config.env → 自动检查并预处理超限图片
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
- 大图片自动缩放/压缩（超限才处理，原图绝不改动）

## 目录结构

```text
vision/
├── README.md                        ← 本文件
├── LICENSE                          ← MIT 协议
├── .gitignore                       ← 忽略真实配置与本地文件
├── skill/                           ← ★ 真正安装到 Claude Code 的内容
│   ├── SKILL.md                     ← Skill 入口（触发条件 + 使用说明）
│   ├── bin/
│   │   ├── vision                   ← 启动器：优先使用内置 runtime
│   │   └── attachment               ← 附件提取启动器（同上）
│   ├── runtime/                     ← ★ 内置便携 Python 3.13 + Pillow（Windows）
│   ├── config/
│   │   ├── vision_config.env.example ← 配置模板（GitHub 保留，无真实 Key）
│   │   └── vision_config.env         ← 真实配置（本地文件，你自己填写，不上传）
│   └── src/
│       ├── vision.py                ← 命令行入口：读配置、校验与预处理图片、打印结果
│       ├── api_client.py            ← DeepSeek Vision API 客户端（纯标准库）
│       ├── attachment.py            ← 附件适配层：从会话存档提取 VS Code 上传的图片
│       └── preprocess.py            ← 大图片自动缩放/压缩（超限才处理，原图不改动）
└── tests/                           ← 单元测试（无需真实 API Key）
```

## 安装

1. **下载 / Clone 本仓库**（GitHub）。

2. **只需要复制 `skill/` 目录**到 Claude Code 全局 Skill 目录
   （README.md、LICENSE、tests 等不需要复制）：

   ```text
   C:\Users\<你的用户名>\.claude\skills\image-vision\
   ```

   Windows PowerShell：

   ```powershell
   Copy-Item -Recurse -Force <仓库路径>\skill "$env:USERPROFILE\.claude\skills\image-vision"
   ```

3. **配置 API Key**（见下一节，必须填写）。

4. **重启 Claude Code**（或新开一个会话）。

5. 直接对 Claude Code 发图提问，或手动输入 `/image-vision`。

安装后的目录结构：

```text
C:\Users\<你的用户名>\.claude\
└── skills\
    └── image-vision\
        ├── SKILL.md
        ├── bin\
        │   ├── vision
        │   └── attachment
        ├── runtime\                ← 内置 Python 3.13 + Pillow（Windows）
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

不需要去源代码（vision.py / api_client.py）中寻找配置。
用 VS Code 等任意编辑器直接打开修改，保存即生效。

| 配置项 | 必填 | 说明 |
|---|---|---|
| `VISION_API_KEY` | ✅ 必填 | 你的 DeepSeek API Key，获取地址：https://platform.deepseek.com/api_keys |
| `VISION_API_BASE_URL` | 可选 | API 地址，默认 `https://api.deepseek.com`，一般不用改 |
| `VISION_MODEL` | 可选 | 视觉模型名称，默认 `deepseek-v4-flash-vision-exp` |
| `VISION_IMAGE_DETAIL` | 可选 | 图片精度（官方 detail 参数）：`auto`（默认，推荐）/ `low`（最快最省 token）/ `high` / `original`。代码**原样传递给官方 API**，不做本地校验 |
| `VISION_TIMEOUT_SECONDS` | 可选 | 单次请求超时秒数，默认 `120` |

GitHub 仓库中只有模板文件 `vision_config.env.example`，不含真实 Key。
真实文件 `vision_config.env` 已被 `.gitignore` 忽略，**永远不会上传 GitHub**。

## 支持的图片来源

本 Skill 处理的是**本地图片文件**，包括：

- 本地磁盘上的图片；
- 项目中的图片；
- 截图（包括 VS Code 侧边栏 "+" 上传的图片附件）；
- Claude Code 能够访问到的其他本地图片文件。

Skill 通过文件路径读取本地图片，**不会去下载互联网上的图片**；
网络图片请先保存到本地，再提供文件路径。

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

然后提供图片文件路径和你的问题。

### 多张图片

可以一次提供多张本地图片，视觉模型会一起分析：

```text
"对比 before.png 和 after.png 两张 UI 截图，说明差异"
```

### VS Code "+" 上传的图片附件

在 VS Code 侧边栏点击输入框旁的 **"+"** 上传图片后，可以直接提问
（例如"帮我看看这张图片"），**无需手动提供文件路径**。

处理流程：

1. `attachment.py` 从**当前 Claude Code 会话存档**中提取附件里的图片数据
   （只提取图片字节，不读取聊天内容）；
2. 图片临时保存到**系统临时目录**；
3. 交给 `vision.py` 与 DeepSeek Vision 分析（图片与用户问题一起发送）；
4. 分析完成后**临时文件自动清理**；
5. 若附件提取失败，自动回退：请用户提供图片文件路径。

> 说明：该功能依赖 Claude Code 本地保存当前会话的附件数据，
> 是本 Skill 针对当前 Claude Code / VS Code 附件机制实现的**适配方案**，
> 并非 Claude Code 官方提供的附件 API。

### 大图片自动处理

Skill 在把图片发送给 DeepSeek Vision API **之前**，会自动检查每张图片的
文件大小与像素尺寸：

- **未超限**：直接使用原图，不做任何处理（不会无意义地降低质量）；
- **超限**（单张 >32MiB、单边 >8192px、≥15 张时单边 >4096px，
  或全部图片合计超出请求体预算）：自动生成**临时副本**进行缩放/压缩后发送，
  **用户原始图片绝不会被修改**；
- 处理优先保证清晰度：优先输出无损 PNG（截图、OCR、代码截图、表格、
  图表的文字清晰度最佳），PNG 仍超限时才改用高质量 JPEG；
- API 请求完成后，临时副本自动清理。

### 更换视觉模型

如果目标视觉模型**兼容当前 API 请求格式**，只需修改
`skill/config/vision_config.env` 中的一行：

```env
VISION_MODEL=deepseek-v4-flash-vision-exp
```

通常无需修改代码。换 API 地址改 `VISION_API_BASE_URL`，换 Key 改 `VISION_API_KEY`。

## 支持的图片格式

| 格式 | 支持 |
|---|---|
| PNG | ✅ |
| JPG / JPEG | ✅ |
| WEBP | ✅ |
| GIF | ✅ |

格式按**文件实际内容**判断（不看文件名后缀）。

## DeepSeek Vision 官方限制

本 Skill 使用官方支持的 **Base64 内联方式**传图（不使用 Files API）。
适用限制如下：

| 限制项 | 数值 | 本 Skill 的处理 |
|---|---|---|
| 单张图片文件大小 | ≤ 32 MiB | 发送前自动检查，超限自动压缩 |
| 单边像素尺寸 | ≤ 8192 px | 发送前自动检查，超限自动缩放 |
| 15 张及以上时单边 | ≤ 4096 px | 发送前自动检查，超限自动缩放 |
| 单次请求图片数量 | ≤ 600 张 | 官方限制，由 API 侧校验 |
| 单张图片计费 | 最多 384 tokens | 官方计费规则 |

## 常见问题

**Q：Skill 没有被自动触发？**
A：确认安装位置为 `~/.claude/skills/image-vision/SKILL.md`（不是仓库里的 `skill/`），
然后重启 Claude Code；或手动输入 `/image-vision`。

**Q：提示"请先填写 VISION_API_KEY"？**
A：打开 `skill/config/vision_config.env`，在 `VISION_API_KEY=` 后面填上你的
DeepSeek API Key。

**Q：提示"不支持的图片格式"？**
A：仅支持 PNG / JPEG / GIF / WEBP，请转换格式后重试。

**Q：API 返回 400 错误？**
A：超限图片在发送前会被自动处理。若**自动处理后仍无法满足官方限制**
（例如多张图片合计超出请求体上限），Skill 会明确报错并说明原因；
收到 400 时请同时查看错误信息中的官方提示。

**Q：请求超时？**
A：在配置文件中调大 `VISION_TIMEOUT_SECONDS`。

**Q：需要安装 Python 依赖吗？**
A：**不需要**。Skill 内置便携 Python 3.13 + Pillow（`skill/runtime/`，Windows 版），
clone 仓库即可在 Windows 上运行，与系统 Python / 项目虚拟环境完全隔离。

**Q：配置文件在哪里？**
A：唯一位置 `skill/config/vision_config.env`，不用翻源代码。

**Q：VS Code 里点 "+" 上传的图片能识别吗？**
A：能。Skill 会从当前会话存档中自动提取附件图片（`attachment.py`），无需提供路径；
提取失败时会提示你提供文件路径。

## 安全说明

- 真实配置文件 `vision_config.env` 包含 API Key，**必须由用户自己填写**，
  已被 `.gitignore` 忽略，**严禁提交到 GitHub**；
- GitHub 仓库只保留 `vision_config.env.example` 模板（无真实 Key）；
- 填写 Key 后请勿删除或修改 `.gitignore` 中的忽略规则；
- 每次提交前请检查 `git status`，确认真实配置文件未被 Git 跟踪。

## 路线图

- [ ] 提供只含 `skill/` 内容的 ZIP 下载包（当前请直接下载仓库并复制 `skill/` 目录）

## License

[MIT](LICENSE) © 2026 jinha
