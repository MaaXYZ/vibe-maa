# 文档导航

vibe-maa 的主要调用者是编程 AI。README 介绍项目；这里按使用与开发目的组织详细文档。

## 让 AI 开始开发

- [Agent Skill](../skills/vibe-maa/SKILL.md)：AI 的入口，包含任务建立、探索、编写、验证和恢复工作流。
- [使用指南](getting-started.md)：安装、接入目标软件、操作命令、结果解释、报告与交付。
- AI 可调用 `vibe-maa capabilities` 获取机器可读能力信息，再按需读取离线协议参考。

## 理解验证结果

- [验收记录](benchmarks.md)：真实模型使用、原始交付物、兼容性发现和当前验证范围。
- [真实软件测试目标](test-targets.md)：固定版本 draw.io 的准备、运行和独立检查。
- [Agent CI](agent-ci.md)：使用模型 API 执行真实编程 Agent 验收。
- [变化后自主重新开发的原始项目](../examples/agent-generated/drawio-change-low/README.md)：失败现场、重新探索、修改、独立回归与安装后接续。
- [隔离环境下的低思考模型交付物](../examples/agent-generated/drawio-low-isolated/README.md)、[参考示例辅助的交付物](../examples/agent-generated/drawio-low-assisted/README.md)和[首次模型交付物](../examples/agent-generated/drawio/README.md)。
- [跨平台参考流程](../examples/drawio-portable/README.md)：单独标注的开发者实现。

## 参与项目开发

- [架构说明](architecture.md)：设备会话、观察、执行、证据与持久任务。
- [接续开发上下文](continuation.md)：当前产物、真实验证、免费复验步骤及剩余边界。
- [贡献指南](../CONTRIBUTING.md)：开发环境、检查和贡献方式。
- [路线图](../ROADMAP.md)与[变更记录](../CHANGELOG.md)。
