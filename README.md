# Claude Code Image Vision Skill

一个供 Claude Code 调用的 Image Vision Skill：当 Claude Code 当前使用的主模型本身不具备图像理解能力时，
由本 Skill 把图片交给 **DeepSeek V4.1-Flash**（API 模型名 `deepseek-flash`）完成视觉分析，
再把结果交还给主模型继续推理。

**Windows 专用 · 无需安装 Python · 支持 VS Code "+" 图片附件 · 支持大图片自动处理**

## 这个项目解决什么问题

我用 Claude Code 时，主模型是纯文本模型：它能规划任务、写代码、做推理，但**看不了图**。
发一张报错截图过去，它只能看到一个占位符，没法告诉我截图里写了什么。

所以我做了这个 Skill：把"看图"这件事交给一个专门的多模态模型，主模型继续做它擅长的事。
**主模型配置不需要任何改动**，视觉模型只是主模型的"眼睛"，不替代主模型。

## 项目定位：谁负责什么

| 角色 | 负责的事情 |
|---|---|
| Claude Code 主模型 | 理解用户意图、任务规划、判断何时需要图像理解、调用 Skill、结合视觉结果继续推理、给出最终回答 |
| DeepSeek V4.1-Flash（`deepseek-flash`） | 图像理解、OCR、截图分析、图表/表格/流程图读取 |
| Image Vision Skill（本项目） | 两者之间的连接与执行层：组织图片与问题、发起视觉请求、把结果交回主模型 |

需要强调的是：**这不是一个可以脱离 Claude Code 独立运行的图片识别工具**，
而是 Claude Code 使用的一个 Skill。它也不是"让 Claude Code 获得原生视觉能力"——
主模型本身依旧没有视觉能力，只是通过调用外部视觉模型补上了看图这一环。

## 工作原理

```text
用户提供图片 + 问题
        ↓
Claude Code
        ↓
Claude Code 当前使用的主模型
（可能本身没有视觉能力）
        ↓
判断需要图像理解
        ↓
调用 Image Vision Skill
        ↓
vision.py            ← 流程控制：参数、配置、校验、调用、清理
        ↓
preprocess.py        ← 图片尺寸/大小/编码预处理（只处理超限图片）
        ↓
api_client.py        ← 组装并发起 DeepSeek API 请求
        ↓
DeepSeek V4.1-Flash
(deepseek-flash)
        ↓
图像理解 / OCR / 截图分析
        ↓
视觉结果返回
        ↓
Claude Code 主模型
        ↓
结合视觉结果继续推理
        ↓
最终回答用户
```

如果图片是 VS Code 侧边栏 "+" 上传的附件（没有文件路径），
主模型会在调用 `vision.py` 之前先经过 `attachment.py` 提取图片，见下文"VS Code '+' 上传的图片附件"。

## 能力清单

- 普通图片理解与描述（照片、插图等）
- OCR：识别图片中的文字
- 代码截图分析（报错信息、代码逻辑等）
- 网页截图 / UI 截图分析
- 数学题截图分析
- 图表、表格、流程图分析
- 根据图片回答问题（图片 + 用户问题一起发送给视觉模型，针对问题分析）
- 大图片自动缩放/压缩（超限才处理，原图绝不改动）

## Windows 定位

- 当前版本**仅支持 Windows**；
- `skill/runtime/` 内置便携 Python 3.13 + Pillow（Windows 版本），
  使用者**无需额外安装 Python**；
- `bin/vision` 和 `bin/attachment` 会**优先使用 Skill 自带的 runtime**，
  不依赖使用者电脑上的系统 Python，也不依赖项目虚拟环境；
- Pillow 已随 runtime 一起提供，大图片自动处理功能开箱即用；
- 使用者只需要配置自己的 DeepSeek API Key。

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
│   │   └── vision_config.env         ← 真实配置（本地文件，自己填写，不上传）
│   └── src/
│       ├── vision.py                ← 命令行入口：读配置、校验与预处理图片、打印结果
│       ├── api_client.py            ← DeepSeek API 客户端（纯标准库）
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
| `VISION_MODEL` | 可选 | 视觉模型名称，默认 `deepseek-flash`（DeepSeek V4.1-Flash） |
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
3. 交给 `vision.py` 与 DeepSeek V4.1-Flash 分析（图片与用户问题一起发送）；
4. 分析完成后**临时文件自动清理**；
5. 若附件提取失败，自动回退：请用户提供图片文件路径。

> 说明：该功能依赖 Claude Code 本地保存当前会话的附件数据，
> 是本 Skill 针对当前 Claude Code / VS Code 附件机制实现的**适配方案**，
> 并非 Claude Code 官方提供的附件 API。

### 大图片自动处理

Skill 在把图片发送给 DeepSeek API **之前**，会自动检查每张图片的
文件大小与像素尺寸：

- **未超限**：直接使用原图，不做任何处理（不会无意义地降低质量）；
- **超限**（单张 >32MiB、单边 >8192px、≥15 张时单边 >4096px，
  或全部图片合计超出请求体预算）：自动生成**临时副本**进行缩放/压缩后发送，
  **用户原始图片绝不会被修改**；
- 处理优先保证清晰度：优先输出无损 PNG（截图、OCR、代码截图、表格、
  图表的文字清晰度最佳），PNG 仍超限时才改用高质量 JPEG；
- API 请求完成后，临时副本自动清理。

### 更换视觉模型

如果目标模型**兼容当前 API 请求格式**，只需修改
`skill/config/vision_config.env` 中的一行：

```env
VISION_MODEL=deepseek-flash
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

## 官方限制与本地工程策略

这一节区分两类东西：**DeepSeek 官方文档规定的外部限制**，和**我自己在项目里定的工程策略**。

### DeepSeek 官方限制

本 Skill 使用官方支持的 **Base64 内联方式**传图（不使用 Files API）。
当前官方文档给出的限制如下：

| 限制项 | 数值 |
|---|---|
| 单张图片文件大小（base64 / 外部 URL） | ≤ 32 MiB |
| 请求体大小（base64 计入） | ≤ 48 MiB |
| 单边像素尺寸 | ≤ 8192 px |
| 同请求图片数 ≥ 15 张时单边 | ≤ 4096 px |
| 单次请求图片数量 | ≤ 600 张 |
| 单张图片计费 | 最多 384 tokens |
| 支持的图片格式 | PNG / JPEG / GIF / WEBP（按内容判断） |
| 图片位置 | 只能放在 `user` 消息中，放 system / assistant 消息会返回 400 |
| `detail` 参数 | `low` / `high`（等同 `original`）/ `original` / `auto` |

### 我在项目里定的工程策略

下面这些**不是官方规定**，是我为了让请求稳定发出去而自己设定的：

| 策略 | 取值 | 为什么这样定 |
|---|---|---|
| 请求体安全预算 | 44 MiB | 官方上限 48 MiB，留出余量，避免 JSON 外壳和边界误差把请求顶过线 |
| 超限图片优先输出格式 | PNG 无损 | 截图、OCR、代码截图、表格、图表的文字最怕压缩失真 |
| PNG 仍超限时的兜底 | JPEG 质量 85 | 在体积和文字可读性之间取的折中值 |
| 仍超限时的收缩步长 | 尺寸 × 0.85 | 逐步收缩，比一次性大幅缩小更能保住细节 |
| 单张图最大尝试轮数 | 4 轮 | 避免极端图片导致长时间循环 |
| 多图超预算时 | 优先压缩最大的那张 | 用最小的影响换回整体预算 |
| 原图处理方式 | 只读，绝不修改 | 用户文件不能被这个 Skill 动过 |
| 模块划分 | 流程 / 预处理 / API 客户端 / 附件提取分离 | 降低耦合，各部分可以独立测试 |

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

- 真实配置文件 `vision_config.env` 包含 API Key，**必须由使用者自己填写**，
  已被 `.gitignore` 忽略，**严禁提交到 GitHub**；
- GitHub 仓库只保留 `vision_config.env.example` 模板（无真实 Key）；
- 填写 Key 后请勿删除或修改 `.gitignore` 中的忽略规则；
- 每次提交前请检查 `git status`，确认真实配置文件未被 Git 跟踪。

## 模型迁移说明

本 Skill 最初的视觉模型是 `deepseek-v4-flash-vision-exp`。
DeepSeek 发布 V4.1-Flash 后，项目已迁移到当前模型：

```text
DeepSeek V4.1-Flash
API model: deepseek-flash
```

旧名称 `deepseek-v4-flash-vision-exp` 已退役，虽然官方在一段时间内仍会把它
路由到新模型，但项目不再依赖它。如果你是从旧版本升级，请把配置文件里的
`VISION_MODEL` 改为 `deepseek-flash`（不改也能跑，但会走官方的兼容路由）。

由于请求格式（Chat Completions、`image_url` + base64 data URL、`detail` 参数）
和图片限制在迁移前后**保持不变**，代码侧只需要更新默认模型名，
`preprocess.py` 与 `attachment.py` 的设计没有改动。

## 路线图

- [ ] 提供只含 `skill/` 内容的 ZIP 下载包（当前请直接下载仓库并复制 `skill/` 目录）

## License

[MIT](LICENSE) © 2026 jinha
