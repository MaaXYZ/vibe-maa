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
- `automation.zip`：普通 MaaFW 项目及来源清单，可继续开发或独立执行。
- `evidence-RUN_ID.zip`：独立验收各次运行的可移植记录、原图和配置来源，不包含服务认证元数据。

只有 `published/` 上传。认证目录、daemon token、原始 Agent 事件流和私有日志不上传。
运行失败或超时会使 job 失败；没有真实调用模型的运行不能声明通过 Agent 验收。

## 本地复现

```bash
uv sync --locked --extra browser
npm ci --prefix integration/agent
uv run playwright install chromium
uv run python integration/agent/run.py --target drawio --output work/agent-run
```

本地同样使用以上环境变量。开发者已有 Codex 登录时，可以显式传入 `--local-auth`；
该模式沿用本机已有配置，不会读取 CI API 密钥，也不会假装使用锁定的 CLI 版本。
程序会记录实际调用的 harness 版本。

实现入口为 [run.py](../integration/agent/run.py)，独立检查位于
[grader.py](../integration/agent/grader.py)。
