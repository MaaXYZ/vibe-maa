# 使用真实编程 Agent 的 CI 验收

普通 CI 不调用模型。另有手动触发的 **Real agent acceptance** 工作流：用开源
[Codex CLI](https://github.com/openai/codex) 观察应用、编辑普通 MaaFW Pipeline，再由独立程序
从干净状态重跑交付物。模型说“完成”不会使验收通过。

## 配置 GitHub

在仓库 **Settings → Secrets and variables → Actions** 配置：

| 类型 | 名称 | 值 |
| --- | --- | --- |
| Repository secret | `VIBE_MAA_LLM_API_KEY` | 模型服务的 API 密钥 |
| Repository secret 或 variable | `VIBE_MAA_LLM_MODEL` | 支持图像、工具调用和 Responses API 的模型 ID |
| Repository secret 或 variable，可选 | `VIBE_MAA_LLM_BASE_URL` | API 基址，默认 `https://api.openai.com/v1` |
| Repository secret 或 variable，可选 | `VIBE_MAA_LLM_REASONING_EFFORT` | 明确传递的思考强度，例如服务支持的 `low`；留空不覆盖提供方默认值 |

基址不包含 `/responses` 或 `/chat/completions`。Codex 的此接入使用 **Responses API**；
只提供 Chat Completions 的兼容服务不能直接使用这条工作流。配置支持该协议的服务和模型，
不要把密钥写进变量、提示词或仓库文件。

Secret 优先于同名 Variable。密钥始终只使用 Secret。

随后打开 **Actions → Real agent acceptance → Run workflow**。默认目标是 `drawio`；
`mailbox` 是更小的工具链自检场景。缺少 API 密钥或模型 ID 会明确失败，不会跳过后显示成功。

每次运行还可以选择 `reasoning_effort`：

| 运行选项 | 行为 |
| --- | --- |
| `configured` | 沿用仓库 Secret/Variable；未设置时不传思考参数 |
| `low` | 仅本次明确请求低思考强度，不修改仓库配置或模型名称 |
| `high` | 仅本次明确请求高思考强度，不修改仓库配置或模型名称 |

命令行也能明确选择：

```bash
gh workflow run agent-acceptance.yml --repo MaaXYZ/vibe-maa \
  -f target=drawio -f timeout_seconds=600 -f reasoning_effort=low
```

`acceptance.json.configuration` 同时保留请求值和 `configuration_source`，区分单次覆盖、
仓库环境配置及未覆盖的提供方默认值。提供方可能把不同请求档位映射成同一实际档位；
未请求某档位不能当作已经测过该档位。

## 验收内容

- `drawio`：运行固定版本的真实 diagrams.net/draw.io，上游发布包经 SHA256 校验后在本地提供。
  Agent 只通过 vibe-maa 的截图与输入探索，交付 `AgentTask` 入口，创建标有 `Vibe Maa` 的形状。
  独立检查程序在两次全新的浏览器上下文中执行交付物，并检查真实画布中的标签。
- `mailbox`：从故意缺少公告分支的起步项目开发。独立检查程序从主页、邮箱、公告三种状态
  启动真实 MaaFW 执行，检查实际访问邮箱和返回主页。它属于 fixture 验证，不冒充真实应用结果。

独立检查程序放在 Agent 工作目录之外。提交只允许内置界面识别和输入动作，不运行
`Command`、`Shell`、`Custom` 等可直接执行其他代码的动作。Agent 无法靠修改自己的目标节点
或生成一份“通过”报告替代独立检查。

## Harness 与预算

`integration/agent/package-lock.json` 锁定 `@openai/codex@0.167.2` 及依赖完整性。
工作流使用 `npm ci` 安装，不追随未锁定的最新版。

整个 GitHub job 最多 15 分钟，Agent 默认预算 600 秒，可手动选择 30–600 秒。
这是墙钟时间预算，不能当作 token 或费用上限。服务方的请求和用量限制仍由模型账号配置。

Agent 使用独立项目和 workspace-write sandbox；只有该项目可写，应用交互所需的本机网络被允许。
API 密钥只在运行 Agent 的步骤提供，并从 shell 工具环境排除。该工作流仅手动触发，
或由维护者推送带 `[agent-ci]` 标记的提交触发，
不在外部 PR 上运行，也没有仓库写入权限。

## 结果

Artifact `agent-acceptance-TARGET` 包含：

- `acceptance.json`：实际 harness 版本、时间预算、模型配置、上游版本、独立验收结果。
- `agent-activity.json`：最多 1 MiB 的安全工具活动摘要，仅保留工具命令、状态、截断输出及用量；不包含模型思考、加密内容或认证字段。
- `automation.zip`：普通 MaaFW 项目及来源清单，可继续开发或独立执行。
- `evidence-RUN_ID.zip`：独立验收各次运行的可移植记录、原图和配置来源，不包含服务认证元数据。

只有 `published/` 上传。认证目录、daemon token、原始 Agent 事件流和私有日志不上传。
运行失败或超时会使 job 失败；没有真实调用模型的运行不能声明通过 Agent 验收。

运行期间每 30 秒打印经过时间、已解析事件数和工具调用数，不打印模型思考或原始输出。
`usage` 为各次 `turn.completed` 所报告 token 数的和，并另保留最后一次用量；缓存和推理 token
仅在提供方返回时记录，不估算缺失值。暂时错误保留在历史摘要中；后续成功完成的 turn 会清除
终态错误，不会因为已恢复的重试记录而错误拒绝成功交付。

报告另保留 Agent 的实际经过时间、唯一工具项数量、被保留的已完成命令中非零退出码的数量，
以及工具活动是否截断。失败命令计数不等同于业务失败：负例检查可以故意产生非零退出码，
而处理成功的 CLI 请求也可能报告任务未完成。指标用于定位交互负担，最终通过仍由独立检查决定。

对比不同思考设置时，应保持模型、目标、时间预算和工具源码提交一致；报告中的
`source_revision` 记录 CI 提交。历史默认档位与升级工具后的 `low` 运行可以作为观察记录，
但不能把结果差异只归因于思考强度。

## 本地复现

```bash
uv sync --locked --extra browser
npm ci --prefix integration/agent
uv run playwright install chromium
uv run python integration/agent/run.py --target drawio --output work/agent-run
```

在保留本机环境中的模型设置时，仅覆盖本次思考参数：

```bash
uv run python integration/agent/run.py --target drawio --output work/agent-low --reasoning-effort low
```

本地同样使用以上环境变量。开发者已有 Codex 登录时，可以显式传入 `--local-auth`；
该模式沿用本机已有配置，不会读取 CI API 密钥，也不会假装使用锁定的 CLI 版本。
程序会记录实际调用的 harness 版本。

实现入口为 [run.py](../integration/agent/run.py)，独立检查位于
[grader.py](../integration/agent/grader.py)。

## 不再次调用模型的复验

`examples/agent-generated/drawio/` 保存首次真实模型运行的原始交付物及校验清单。
`integration/agent/regrade.py` 可以在新环境独立重放，验证前后源码不变：

```bash
uv run python integration/agent/regrade.py --project examples/agent-generated/drawio --output work/replay
```

初次验收错误地把 draw.io 的默认 `label` 矩形渲染器视为非矩形，原 CI 红色记录保留。
修正后的检查验证实际矩形渲染器，并用真实椭圆做反例。重放与重新调用模型分别记录；
不同系统字体或渲染环境仍可能使原来的图像模板失配，不能通过手动改交付物来冒充原样通过。

## 免费重放已有交付物

```bash
uv run python integration/agent/regrade.py \
  --project examples/agent-generated/drawio \
  --cache work/targets --output work/drawio-replay
```

这条命令不加载 harness，也不调用模型 API。存在原始 `manifest.json` 时先核验全部来源文件，
然后在新应用会话中运行原脚本，记录执行前后源码哈希、原生证据包和最后截图。
`--source-archive PATH` 可额外记录原始 ZIP 的 SHA256。它不会修改脚本或放宽模板阈值；
字体、操作系统或浏览器导致的识别差异会作为真实重放失败保留。
