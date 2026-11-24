# Changelog

本项目使用 SemVer。公共核心契约的语义变化需要用户确认，详见 `docs/governance.md`。

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
