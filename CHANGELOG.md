# Changelog

本项目使用 SemVer。公共核心契约的语义变化需要用户确认，详见 `docs/governance.md`。

## [0.3.0] - 2026-11-24

### Added

- 同一仓库支持 Claude Code Plugin、Pi package 与 Codex Plugin 三种分发载体，公共 Skill、脚本和项目状态格式共用一份正本。
- Pi `package.json` manifest，显式导出 `project-collaboration` Skill。
- Codex `.codex-plugin/plugin.json` 与 `.agents/plugins/marketplace.json`，通过同一 Skill 目录提供插件能力。
- Skill 目录内的任务卡与评估脚本包装器，统一 Claude Code、Pi、Codex 和普通终端的路径解析。
- 三端安装、自安装、项目接入、升级和恢复文档，以及 manifest、frontmatter、包装器和隔离实装回归测试。
- Agent 自安装提示必须先向用户确认安装范围与是否固定版本，不得预设 `user` 或目标版本。

### Compatibility

- Claude Code 入口保持 `/agent-collab:project-collaboration`；Pi 入口为 `/skill:project-collaboration`；Codex 入口为 `$agent-collab:project-collaboration`。
- 任务卡、评估记录、ledger 和项目 `.herdr/` 状态不变，无需迁移。
- 核心目标、权限、验收和评估契约不变；三种工具都不获得自动提交、推送、部署或业务验收权限。

## [0.2.0] - 2026-11-24

### Added

- Agent 评估记录模板，以任务、Attempt 和 Agent 为最小归因单元。
- `agent_eval.py check` 质量门禁与 `rollup` 同类任务能力档案。
- 独立复审、业务验收、硬失败、证据不足和责任归因规则。
- 项目历史与同版本 benchmark 分池聚合，小样本和未知指标显式标记。
- 追加式 correction 链：新记录通过 `correction_of` 更正旧判断，汇总只统计链最新记录。
- 评估流程、派单选型、防刷分原则、测试 fixtures 与自动化验证。

### Compatibility

- 任务卡 schema v1 不变，既有项目无需迁移任务卡。
- 评估历史保存在项目本地 `.herdr/agent-evaluations.jsonl`，Plugin 升级不覆盖项目状态。
- 不自动上传评估数据、淘汰 Agent、部署或发布；人的审批和业务验收边界不变。

## [0.1.0] - 2026-11-24

### Added

- 团队共享的 Claude Code Plugin 与内部 marketplace 清单。
- `project-collaboration` Skill，覆盖目标对齐、过程沟通、派单、验证、交付和恢复。
- 生命周期、决策边界、过程沟通、证据验收和状态交接 references。
- 薄 AGENTS、CLAUDE、当前任务卡和 PROGRESS 模板。
- 无第三方依赖的 `validate_task.py`，支持 draft 结构检查与 dispatch 开工就绪检查。
- 单元测试、结构测试、有效任务卡样例和一键 `verify.sh`。
- 本地安装、项目接入、架构和团队版本治理文档。

### Boundaries

- 不修改或覆盖接入项目的运行状态和证据。
- 不自动提交、推送、发布或启用组织强制安装。
- 技术验证不替代独立复审和人的业务验收。
