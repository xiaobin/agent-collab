# 架构与职责

## 为什么是版本化公共包

个人全局 Skill 能解决“我的所有会话可用”，但不天然提供团队单一正本、版本发布、更新审计和项目薄入口。`agent-collab` 同时作为 Claude Code Plugin、Pi package 和 Codex Plugin 分发：公共流程只维护一份，成员安装明确版本，项目不分叉公共规则。三种工具读取同一个 `project-collaboration` Skill，不维护多份流程正文。

## 载体分工

| 内容 | 载体 | 原因 |
| --- | --- | --- |
| 通用生命周期、沟通和验收流程 | 公共 Skill 与 references | 团队复用，按需进入上下文 |
| 项目目标来源、写权限、验收命令 | 项目薄 `AGENTS.md` | 项目事实不能写死在公共流程里 |
| 当前目标、范围和验收 | `.herdr/current-task.json` | 机器可读，可校验和交接 |
| 进度、下一动作、恢复入口 | `.herdr/PROGRESS.md` | 人类可读的快照 |
| 调度、验证、审查和决策事实 | 项目 ledger 与原始证据 | 可追加追溯，不靠记忆重建 |
| 每个 Attempt 的评估事实 | 项目 `.herdr/agent-evaluations.jsonl` | 项目所有、只追加；最终记录与 correction 也追加，脚本只校验结构与确定性门禁 |
| 派单画像与 benchmark 汇总 | 由评估记录派生的 rollup 输出 | 可重建的派生工件，不作为权威证据 |
| 开工字段与结构检查 | `scripts/validate_task.py` | 确定性、可重复、无模型歧义 |
| 非显性长期偏好 | 用户或项目 memory | 只作背景，不作任务事实源 |

## 目录

```text
agent-collab/
├── .claude-plugin/
│   ├── plugin.json
│   └── marketplace.json
├── .codex-plugin/
│   └── plugin.json
├── .agents/plugins/
│   └── marketplace.json
├── package.json
├── skills/project-collaboration/
│   ├── SKILL.md
│   ├── scripts/
│   └── references/
├── templates/
├── scripts/
├── tests/
├── docs/
├── CHANGELOG.md
└── README.md
```

Claude Code Plugin 与 marketplace 同根，marketplace entry 的 `source` 为 `./`。Pi 通过根目录 `package.json` 的 `pi.skills` 显式加载同一个 Skill；Codex 通过 `.codex-plugin/plugin.json` 导出同一目录，并由 `.agents/plugins/marketplace.json` 分发。三端 manifest 的名称与版本必须一致，结构测试会检查这一约束。Skill 内的 shell 包装器从自身位置定位公共 Python 脚本，避免把 `${CLAUDE_PLUGIN_ROOT}` 当成跨工具环境变量。

## 提示与硬门禁的边界

Skill 和 AGENTS.md 是模型指令，通常会被遵守，但不是权限系统。脚本只能确定性地验证它定义的结构和开工条件。真正的权限控制仍由 Claude Code、Pi 或 Codex 的权限机制、项目脚本、CI 和人的审批承担。

当前版本有意不注册自动 hook：不同项目的命令、状态目录和 agent 调度器仍需先完成接入验证，不能用一刀切的 hook 阻断正常工作。后续新增 hook 必须伴随可重复测试和关闭误拦截的证据。

## 上下文预算

主 Skill 只保留每次任务都需要的主路径；生命周期、决策、沟通、验收和交接细节拆入 references。项目 AGENTS.md 不再复制整套流程。升级时优先改最便宜的载体，避免把公共规则重新堆回常驻文件。

## 状态隔离

公共包安装和更新只处理公共文件。项目的 `.herdr/`、ledger、测试结果和业务验收证据属于项目，公共发布脚本不得清空、重建或覆盖它们。

## 评估与画像

评估把“谁在什么证据下判成什么”落到项目自己的 `.herdr/agent-evaluations.jsonl`，每个 Attempt 一条，最终记录与 correction 也追加，历史不改写。汇总画像由 `scripts/agent_eval.py rollup` 从最终记录派生，可随时重建；画像与原始记录冲突时以原始记录和证据为准。

评估不引入新的自动 hook，也不改变权限。脚本能校验记录结构和门禁取值，不能证明引用文件真实、命令确实执行或业务有价值；这些仍由协调者重跑、独立 reviewer 和用户业务验收承担。`mode` 区分项目评估与 benchmark，两者分开汇总；样本不足只给原始数据，不给排名。
