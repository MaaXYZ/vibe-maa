# 使用指南

## 安装与升级

用户安装推荐使用独立工具环境：

```bash
uv tool install 'vibe-maa[browser] @ git+https://github.com/MaaXYZ/vibe-maa'
vibe-maa browser install
```

只用 ADB 或邮箱示例时去掉 `[browser]`。升级前关闭项目服务，再重新安装工具；常驻服务会核验版本，不会静默继续使用旧实现：

```bash
vibe-maa --project PATH daemon stop
uv tool install --force 'vibe-maa[browser] @ git+https://github.com/MaaXYZ/vibe-maa'
```

开发源码：

```bash
git clone https://github.com/MaaXYZ/vibe-maa.git
cd vibe-maa
uv sync --locked --extra browser
uv run playwright install chromium
uv run vibe-maa --help
```

Linux 若缺少浏览器系统库，可在支持的系统上按 Playwright 的提示安装，或使用 `uv run playwright install --with-deps chromium`。发布包与开发检验都使用相同的 CLI 和服务实现。

## 创建项目并接入编程 AI

普通资源项目使用 `vibe-maa --project PATH init`。需要可复位学习场景时，在新目录运行 `demo init --starter`；不带 `--starter` 会安装已经覆盖三种起始状态的完整邮箱示例。

```bash
vibe-maa --project ./example demo init --starter
vibe-maa --project ./example agent install --target codex
vibe-maa --project ./example agent show
```

Codex 的 Skill 放在项目 `.agents/skills/vibe-maa/`；Claude Code 放在 `.claude/skills/vibe-maa/`。安装已有目录会明确失败，便于保留用户修改。可以用 `agent export --output skills-copy` 先导出并比较内容。安装后刷新编程 AI 会话。

AI 应先读取项目说明和 `task context`，再操作软件。任务定义保存在 `dev/tasks/`，可以进入版本控制：

```bash
vibe-maa --project ./example task create mailbox --objective "打开邮箱并返回主页" --entry OpenMailbox --goal HomeVisible --cases dev/cases/mailbox.json --max-attempts 10
vibe-maa --project ./example task list
vibe-maa --project ./example task context mailbox
```

可重复使用 `--constraint` 记录资源使用等要求。这些文字要求由编程 AI 遵守，工具不会声称它们已被自动验证。入口与完成条件要由可观察事实支撑，不能把 `DirectHit` 当作完成证明。

## 设备、浏览器与坐标

先运行 `doctor` 检查项目资源、运行时、可选浏览器与 ADB；`devices` 列出 ADB 目标。后端必须显式指定：

```bash
vibe-maa --project ./example session open --backend demo --initial-state popup
vibe-maa --project ./android-project session open --backend adb --device 127.0.0.1:16384 --adb-path adb
vibe-maa --project ./web-project session open --backend browser --url http://127.0.0.1:8080 --width 1280 --height 720
```

浏览器默认无头运行；`--headed` 用于需要观看窗口的本机调试。`--browser-executable PATH` 可指定已有 Chromium。连接浏览器不会给 AI 页面脚本或 DOM 操作能力；输入依赖观察和 MaaFW 图像识别。

使用返回的完整控制器截图坐标。ADB 默认短边 720 像素；浏览器使用所选视口。不要把裁剪图坐标或设备物理坐标直接当成控制器坐标。

OCR 模型应放入会话资源包的 `model/ocr/`。没有模型时，`observe` 的 `warnings` 会解释原因，原图仍可读取。`--no-ocr` 可显式只观察图片。

## 探索、试识别与修复

将实际返回的 ID 替换进命令：

```bash
vibe-maa --project ./example observe --session SESSION_ID
vibe-maa --project ./example probe --session SESSION_ID --observation OBS_ID --node HomeVisible
vibe-maa --project ./example act --session SESSION_ID --observation OBS_ID --target e2
```

`probe` 在保存的图片上仅执行识别，节点的动作和后继均不会执行。动作使用当前观察的目标引用或 `--box X,Y,W,H`，并返回新的观察。输入前会重新核验目标局部图像；旧观察、跨会话观察或已变化的目标会被拒绝。按键和文本输入也需要目标或 guard box。

模板素材从保存的原图生成：

```bash
vibe-maa --project ./example asset extract --observation OBS_ID --box 220,140,200,60 --name MailboxButton
```

先编辑普通 `resource/pipeline/` 文件，再运行保存的任务：

```bash
vibe-maa --project ./example task run mailbox --session SESSION_ID --timeout-seconds 20
vibe-maa --project ./example task context mailbox
```

`run --entry NODE --goal GOAL --override JSON` 适合临时调参；正式任务验收使用当前文件，不接受临时覆盖来替代应保存的修改。

失败时读取 `report show RUN_ID`，重点查看最后节点、等待条件、原始识别图片和缺失证据。根据现场改动节点，再重跑。不要通过放宽完成条件或改样本标签消除失败。

## 恢复任务与解释结果

服务重启会使连接和会话 ID 失效；任务定义、尝试记录和历史图片保留。重新打开正确后端，读取 `task context NAME`，从最新证据继续。历史图片可继续用于 `probe`，但不能再次作为设备点击依据。

任务的 `accepted` 只表示当前源文件、任务定义及所选用例满足已配置检查，且证据标记了 `fixture/device` 范围。修改其中任何一项后需重新验证。异常或中断的尝试会留痕并消耗预算；响应丢失时先检查记录，不能假定输入未发生。

离线样本清单使用完整截图，路径相对于清单文件：

```json
[
  {"node": "HomeVisible", "image": "home.png", "expected_hit": true},
  {"node": "HomeVisible", "image": "popup.png", "expected_hit": false}
]
```

```bash
vibe-maa --project ./example case check --session SESSION_ID --cases dev/cases/mailbox.json
```

离线结果只验证识别，不能证明软件接受输入或真实业务已完成。后者需设备上的运行检查及适当的独立结果依据。

## 分享报告和交付项目

```bash
vibe-maa --project ./example report list
vibe-maa --project ./example report show RUN_ID
vibe-maa --project ./example report export RUN_ID --output evidence.zip
vibe-maa --project ./example export --output maa-project.zip
vibe-maa --project ./example daemon stop
```

导出不会覆盖已有文件。证据 ZIP 含相对路径、来源图片与 SHA256 清单；项目 ZIP 包含 `interface.json` 指定的普通资源及整理后的开发材料，排除服务令牌和原生日志。打包本身不构成验收。

## 当前支持范围

ADB、截图/输入浏览器传输及邮箱测试场景已接入。Win32 控制器、AgentServer 自定义进程生命周期仍未接入；离线 `probe` 暂不支持 Custom、And/Or 或依赖实时上下文的节点。

原生任务超时使用协作停止。设备连接或原生线程完全卡死时，需要额外进程隔离才能提供硬终止保证。工具不回滚游戏服务器或账号状态。

真实软件验收与可选外部模型实验分别见 [Draw.io 目标](test-targets.md)和 [Agent CI](agent-ci.md)。
