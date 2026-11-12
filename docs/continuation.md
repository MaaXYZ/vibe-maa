# 接续开发上下文

目标是让编程 AI 在目标界面变化后继续开发：从真实失败和当前观察判断问题，修改普通 MaaFW Pipeline/素材，再检查新场景与旧场景。不是把某个固定脚本重放成功当作整个目标完成。

当前开发分支：`feat/drawio-recovery-acceptance`；从 main `b5a16a7` 接续，没有重建项目。代码版本为 **0.4.0 unreleased**。main 和既有 v0.3.0 草稿 Release 保持原状。

已经保留两轮真实 `deepseek-flash`、Responses API、显式 `low`、每轮最多 600 秒的开发：

- [43202606848](https://github.com/MaaXYZ/vibe-maa/actions/runs/43202606848)：原项目正常起点通过、Device 启动弹窗失败；模型接续修复，用时 446.007 秒，独立六次通过。其原始任务因两个 URL 的归属仍为 `needs_validation`；第二 Linux 主机随后在 SplashStorage 3/3 失败。这些事实未改写。
- [43208826944](https://github.com/MaaXYZ/vibe-maa/actions/runs/43208826944)：以该真实变化失败为输入，在受控变化环境复现后交给模型重新开发，未提供修复答案。模型用时 172.282 秒，自行改为上下文颜色识别，经历一次离线参数失败后修正。独立新旧环境、两个起点共十二次通过，任务整体 `accepted/current`。原先失败的主机原样重放六次也通过。

原始模型交付物在 [drawio-change-low](../examples/agent-generated/drawio-change-low/README.md)，可直接下载 [MaaFW 项目 ZIP](records/archives/drawio-change-43208826944-automation.zip)。所有原始产物、修改前失败、工具活动、native 证据与后续重放分别存放在 [records](records/) 和 [benchmarks](benchmarks.md)，不可替换为事后手修版本。人工图标诊断实验仅在忽略的 `work/manual-diagnostic-adaptation`，不计作模型成功。

工具改动包括显式 `scenario_urls`、精确 URL/运行时/资源/起点证据归属、错目标输入前拒绝，以及失败后的验收证据保留。声明改变需要新的证明；旧记录不会追溯合并。Skill 已补充目标变化后的重新观察、回归与上下文识别限制。

免费复验：

```bash
uv sync --locked --extra browser
uv run vibe-maa browser install
uv run python integration/agent/regrade.py \
  --project examples/agent-generated/drawio-change-low \
  --starting-states blank splash --repetitions 3 --output work/change-replay-new
uv run python integration/agent/validate_recovery.py \
  --project examples/agent-generated/drawio-change-low --output work/change-task-new
```

输出目录必须新建。任务声明端口 38285；如目标地址不同，显式修改定义后重新验证。解压后的源码项目不包含旧 `.maa-dev` ledger，其内历史 `verification` 文本不代表当前设备已经通过。

变更试验可用 `--target drawio-change` 复现；先运行 `integration/agent/preflight.py`。没有新问题需要研究时，优先免费重放，避免重复付费调用。禁止读取密钥或发布原始认证日志。完整 job 留出 25 分钟，模型预算仍是 600 秒，不是费用限额。

独立审查确认两轮开发记录与清单哈希，且未见其他答案、grader 或 DOM 访问。活动记录有输出字段截断，网络不受文件系统隔离策略限制；不作完全信息流证明。新 ColorMatch 依赖 SplashOpen 先进入 Storage，不能称为通用弹窗分类器。其他真实应用、更多业务流程变化及统计成功率尚未建立。

本分支供审查，尚未合并。0.4.0 尚未正式发布，也未部署。保留独立验证和原始产物的审查边界，不把后续维护版本替换成历史模型输出。
