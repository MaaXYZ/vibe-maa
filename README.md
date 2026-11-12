# vibe-maa

真实模型运行与跨平台结果见[验收记录](docs/benchmarks.md)；原始生成脚本和派生参考流程分别保留。

让编程 AI 边观察软件、边开发 MaaFramework 自动化，最终交付普通 MaaFW 项目。

你描述目标，Codex 或 Claude Code 负责探索界面、编辑 Pipeline；vibe-maa 提供持久设备会话、同帧观察、仅识别试验、真实运行、任务上下文和验证证据。AI 可以根据失败现场继续修复，也可以在新会话中接着上一次任务工作。

## 安装

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)。工具使用 MaaFramework 5.16+；浏览器支持按需安装。

```bash
uv tool install 'vibe-maa[browser] @ git+https://github.com/MaaXYZ/vibe-maa'
vibe-maa --version
vibe-maa browser install
```

仅操作 Android 或运行邮箱示例时，可以省略 `[browser]` 和 Chromium 安装。也可以从 [GitHub Releases](https://github.com/MaaXYZ/vibe-maa/releases) 获取 wheel，在本机执行 `uv tool install ./vibe_maa-VERSION-py3-none-any.whl`。源码安装、升级与诊断见[使用指南](docs/getting-started.md)。

## 先让 AI 完成一个可复现任务

以下命令在新目录创建邮箱起步项目。它确实缺少公告处理分支，需要 AI 查看界面并修改源文件。

```bash
vibe-maa --project ./mailbox-project demo init --starter
vibe-maa --project ./mailbox-project agent install --target codex
vibe-maa --project ./mailbox-project task create mailbox --objective "打开邮箱，再返回主页；处理启动时的公告" --entry OpenMailbox --goal HomeVisible --cases dev/cases/mailbox.json
```

在该项目目录启动 Codex，然后给出任务：

> 使用 vibe-maa 完成 mailbox 任务。先读取任务上下文，再探索界面、修改 Pipeline 并验证；保留 HomeVisible 完成条件和已有样本标签，最后交付项目与验证报告。

Claude Code 使用 `agent install --target claude`。Skill 安装在项目内，不修改用户目录，也不覆盖已有 Skill。Skill 与示例数据都随安装包提供，无需额外克隆本仓库。

邮箱是可复位测试场景，执行与识别使用真实 MaaFW 原生运行时。其通过结果只证明这套开发流程在该场景有效。真实软件验收采用单独的 [Draw.io 目标](docs/test-targets.md)，与 Android 游戏覆盖范围分开记录。

## 手动检查开发链路

```bash
vibe-maa --project ./mailbox-project session open --backend demo --initial-state popup
```

保存 JSON 中的 `result.session_id`，替换下面的 `SESSION_ID`：

```bash
vibe-maa --project ./mailbox-project observe --session SESSION_ID
vibe-maa --project ./mailbox-project task run mailbox --session SESSION_ID --timeout-seconds 20
vibe-maa --project ./mailbox-project task context mailbox
vibe-maa --project ./mailbox-project report list
```

起步项目在公告界面会失败。AI 应查看返回的原图、识别记录和等待节点，修改 `resource/pipeline/mailbox.json`，再运行同一任务。每次试验都会重新加载资源；已通过的旧代码不能代表新代码通过。

`task context` 保存目标、约束、预算和最新证据，服务重启后仍然可读。重新打开设备会话即可继续。完整的观察、试识别、素材提取和离线用例操作见[使用指南](docs/getting-started.md)。

## 接入实际软件

浏览器只提供截图和输入传输；识别与 Pipeline 执行仍由 MaaFW 完成。浏览器后端不向 AI 暴露 DOM 查询、页面脚本执行或应用内部模型。

```bash
vibe-maa --project ./my-project init
vibe-maa --project ./my-project doctor
vibe-maa --project ./my-project session open --backend browser --url http://127.0.0.1:8080
```

Draw.io 的固定版本、离线部署及独立结果检查见[真实软件测试目标](docs/test-targets.md)。Android 通过 ADB 连接：

```bash
vibe-maa --project ./my-project devices
vibe-maa --project ./my-project session open --backend adb --device 127.0.0.1:16384
```

OCR 需要资源包内提供匹配模型；缺失时观察仍保存截图，并返回明确警告。`doctor` 会列出设备、模型或浏览器准备情况，不会自动连接未知设备。

## 验收与交付

一次请求处理完成、Pipeline 执行成功、任务目标满足分别记录：

| 字段 | 含义 |
| --- | --- |
| `ok` | CLI 请求是否处理完成 |
| `execution_status` | 原生任务成功、失败或超时 |
| `goal_status` | 独立完成条件通过、失败或无法确认 |
| 任务 `state` | 当前源文件与所选用例是否已有有效验收记录 |
| `scope` | 证据来自测试场景还是实际设备/应用 |

未检查目标时保持 `unknown`。修改任务、资源或样本会使已有验收失效。自然语言约束会持久保存，供 AI 遵守，但不会自动转换成完备的业务限制。

```bash
vibe-maa --project ./mailbox-project report show RUN_ID
vibe-maa --project ./mailbox-project report export RUN_ID --output evidence.zip
vibe-maa --project ./mailbox-project export --output maa-project.zip
vibe-maa --project ./mailbox-project daemon stop
```

导出的项目使用普通 MaaFW 资源格式；证据包重写关联路径并携带校验清单。运行记录、原图与本机服务信息保存在 `.maa-dev/`，不会混入项目导出。

## 项目开发与维护

- [使用指南](docs/getting-started.md)：安装、恢复任务、真实设备、报告与导出。
- [架构说明](docs/architecture.md)：连接所有权、证据来源、识别与执行边界。
- [真实软件目标](docs/test-targets.md)：Draw.io 固定版本与验收边界。
- [Agent 验收](docs/agent-ci.md)：外部编程 AI 的可重复开发实验及所需配置。
- [贡献指南](CONTRIBUTING.md)与[变更记录](CHANGELOG.md)。

普通 CI 不调用模型：检查 Ubuntu/Windows、Python 3.11/3.12、原生运行时、安装包和可选浏览器后端。Agent 验收独立运行，不会把预先写好的流程当成 AI 开发成绩。发布流程构建并检查安装包，创建 GitHub 草稿发布；不会自动向 PyPI 发布。

License: [AGPL-3.0-only](LICENSE)。
