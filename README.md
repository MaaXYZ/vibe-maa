# vibe-maa

给 Codex、Claude Code 等编程 AI 使用的 MaaFramework 开发工具。AI 可以观察设备、操作界面、在保存的截图上试识别节点、运行 Pipeline，再根据完整现场继续修改项目。

产物是普通 MaaFW Pipeline、图片素材和验证材料；日常执行不需要加载本工具或调用模型。第一版提供 Python SDK、JSON CLI、项目级常驻服务，以及无需真实设备的邮箱示例。

## 安装与检查

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)。当前原生集成以 MaaFW 5.16 为最低版本，仓库锁定完整依赖。

```bash
git clone https://github.com/MaaXYZ/vibe-maa.git
cd vibe-maa
uv sync --locked
uv run vibe-maa --help
```

开发检查：

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

## 先运行可复位示例

邮箱示例具有主页、邮箱、公告弹窗三种状态。界面和观察时的文字结果是确定性测试数据，Pipeline 执行和节点识别使用真实 MaaFramework 原生运行时。无需 ADB 设备或下载 OCR 模型。

```bash
uv run vibe-maa --project examples/mailbox init
uv run vibe-maa --project examples/mailbox session open --backend demo
```

正常命令在 stdout 输出一个 JSON 对象。保存返回的 `result.session_id`，将下面的 `SESSION_ID` 替换成实际值：

```bash
uv run vibe-maa --project examples/mailbox observe --session SESSION_ID
```

返回 `observation_id`、原图路径、图片 SHA256、尺寸，以及 `elements` 中的文字和目标引用。打开返回的 `image_path` 查看原图。保存 `observation_id`，将下面的 `OBS_ID` 替换成实际值：

```bash
uv run vibe-maa --project examples/mailbox probe --session SESSION_ID --observation OBS_ID --node HomeVisible
uv run vibe-maa --project examples/mailbox act --session SESSION_ID --observation OBS_ID --target e2
```

此时 `e2` 对应主页的 Mailbox 按钮。`probe` 只识别保存的图片，绝不执行节点动作；`act` 则点击目标并自动保存操作后的新观测。新动作应使用返回的新 `observation_id`，不能重复使用旧引用。按键和文本输入也必须指定 `--target` 或 `--box`，作为输入前重新检查的画面区域。

现在运行示例流程并独立检查是否回到主页：

```bash
uv run vibe-maa --project examples/mailbox run --session SESSION_ID --entry OpenMailbox --goal HomeVisible --timeout-seconds 10
```

检查返回的 `execution_status` 和 `goal_status`。正常结果分别为 `succeeded` 和 `passed`。更多场景见 [邮箱示例说明](examples/mailbox/README.md)。

## 复现失败并修复

创建一个从公告弹窗开始的新会话：

```bash
uv run vibe-maa --project examples/mailbox session open --backend demo --initial-state popup
```

使用新返回的会话 ID，临时去掉入口对公告的处理分支：

```bash
uv run vibe-maa --project examples/mailbox run --session SESSION_ID --entry OpenMailbox --goal HomeVisible --timeout-seconds 3 --override '{"OpenMailbox":{"next":["ClickMailbox","MailboxVisible"]}}'
```

流程将失败。读取返回的 `report_path`，查看 `events` 中未命中的识别记录及关联原图。现场应显示公告，说明现有后继节点没有覆盖这个界面。报告还保留 `last_recognition` 原图；即使常规图片预算已用完，最后一次有原图的识别仍会单独保存。

移除本次覆盖即可使用示例中完整的公告处理分支：

```bash
uv run vibe-maa --project examples/mailbox run --session SESSION_ID --entry OpenMailbox --goal HomeVisible --timeout-seconds 10
```

实际开发中，AI 根据这类记录修改 `resource/pipeline/` 中的节点，再重跑验证。每次实验重新加载资源文件，设备连接持续保留。

## 连接真实 Android 设备

先准备一个普通 MaaFW 资源目录，并确保 ADB 能访问目标设备。`init` 会补齐目录和最小 `interface.json`，不覆盖已有文件。

```bash
uv run vibe-maa --project ./my-project init
uv run vibe-maa --project ./my-project session open --backend adb --device 127.0.0.1:16384 --adb-path adb
uv run vibe-maa --project ./my-project observe --session SESSION_ID
```

默认资源路径为 `resource`，可以在打开会话时通过 `--resource-path` 指定项目内的其他目录。真实设备 OCR 需要资源包的 `model/ocr/` 中已有匹配的 MaaFW OCR 模型，本工具不会自动下载。缺少模型时，观察仍返回截图，并在 `warnings` 中标明 `OCR_UNAVAILABLE`；可用 `--no-ocr` 显式跳过 OCR。

坐标全部使用返回的完整控制器截图坐标，不能直接假设为设备物理分辨率。真实 ADB 会话默认将截图短边设为 720 像素。当前版本尚未用具体游戏验证自动日常任务。

## 素材与离线检查

从保存的观测裁剪模板；名称只允许字母、数字、下划线和连字符，已有素材不会被覆盖：

```bash
uv run vibe-maa --project examples/mailbox asset extract --observation OBS_ID --box 220,140,200,60 --name MailboxButton
```

生成的图片写入 `resource/image/`，来源记录写入 `dev/assets/`，包含原始观测 ID、原图 SHA256 和裁剪区域。

离线检查使用 JSON 或 JSONC 数组，例如 `dev/cases/mailbox.json`：

```json
[
  {"node":"HomeVisible","image":"home.png","expected_hit":true},
  {"node":"HomeVisible","image":"mailbox.png","expected_hit":false}
]
```

图片路径相对于清单文件，预期结果需要有独立依据。将对应完整截图放在清单旁，再运行：

```bash
uv run vibe-maa --project examples/mailbox case check --session SESSION_ID --cases dev/cases/mailbox.json
```

结果的 `scope` 为 `offline_recognition_only`。图片检查通过只证明节点能识别这些样本；设备上的真实转移和任务完成情况仍需 `run` 验证。

## 命令与结果约定

| 命令 | 用途 |
| --- | --- |
| `session open / list / close` | 设备会话管理 |
| `observe` | 同一帧的图片、OCR 和坐标 |
| `act` | 带观测引用的点击、滑动、按键、输入文本 |
| `probe` | 在保存图片上试识别一个节点 |
| `run` | 有时间预算的 Pipeline 实验及可选目标检查 |
| `asset extract` | 裁剪图片并记录来源 |
| `case check` | 正例、反例的离线识别检查 |
| `artifact --kind … --id …` | 读取保存的观察、动作或实验记录 |
| `daemon status / stop` | 查看或关闭项目常驻服务 |

除 `--help` 和 `--version` 外，CLI 在 stdout 输出单个版本化 JSON 对象：

```json
{"schema_version":1,"ok":true,"result":{}}
```

`ok:false` 表示请求出错，含稳定的 `error.code`，CLI 退出码为 1。`ok:true` 表示请求处理完成，退出码为 0；它不保证动作、Pipeline 或业务目标成功。调用方还应检查动作的 `status`、实验的 `execution_status`（`succeeded`、`failed`、`timeout`）、`goal_status`（`passed`、`failed`、`unknown`），以及样本检查的 `passed`。

未指定 `--goal`，或目标无法核验时，`goal_status` 为 `unknown`。目标节点执行识别，不执行动作；目标应能通过画面证明完成，不能使用无条件命中的 `DirectHit`。与任务返回状态独立的检查，也仍然依赖目标定义本身是否正确。

## 服务与记录

第一次设备命令自动启动项目级常驻服务；后续独立 CLI 调用继续使用同一连接。服务只监听本机回环地址，认证信息保存在项目的 `.maa-dev/`。同一服务串行执行设备操作；同一系统用户下，针对相同 ADB 地址的跨项目会话也受系统文件锁保护。服务重启后需要新开会话，历史记录保留，旧截图仍可用于 `probe`。

```bash
uv run vibe-maa --project examples/mailbox session list
uv run vibe-maa --project examples/mailbox daemon status
uv run vibe-maa --project examples/mailbox daemon stop
```

运行时记录、原图、原生日志和服务元数据都位于 `.maa-dev/`，默认忽略，不提交到 Git。需要交付的 Pipeline、资源图片、样本和来源记录由项目维护。网络中断或响应丢失时，客户端不会自动重试输入；先检查保存的动作和实验记录，再决定是否继续。

## 当前范围

- 已实现 ADB 和可复位示例后端；Win32 等其他控制器尚未接入。
- `probe` 支持数值 ROI 的内置识别；Custom、And/Or 及依赖运行上下文的节点暂不支持离线试识别。标准任务由 MaaFW 原生执行。
- 第一版尚未管理 AgentServer 或注册项目自定义模块；依赖这些能力的任务需要后续接入。
- 点击前会检查会话、操作代次、观测年龄和目标局部图像。动态目标、外部同时操作以及截图到输入之间的变化，仍需任务自身设计应对。
- 本项目不包含模型服务、聊天界面、自动理解所有游戏规则的调度器或账号状态回滚。任务目标和验证依据需要明确给定。

设计说明见 [架构文档](docs/architecture.md)。编程 AI 的工作流指引见 [Agent Skill](skills/vibe-maa/SKILL.md)。
