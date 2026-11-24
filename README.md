# agent-collab

给你和团队统一维护的项目协作 Plugin：把目标确认、过程沟通、人机决策边界、状态恢复和证据验收放进同一版本化正本，项目仓库只保留自己的事实与状态。

## 包含什么

- `skills/project-collaboration/`：按需加载的项目协作主流程及专项参考。
- `templates/`：薄 `AGENTS.md`、当前任务卡、进度与恢复入口模板。
- `scripts/validate_task.py`：无第三方依赖的任务卡结构与开工就绪校验。
- `scripts/verify.sh`：单元测试、任务卡样例校验和 Claude Code Plugin 校验。
- `docs/`：接入、架构和团队治理约定。

## 本地安装

这会把 marketplace 写入你的 Claude Code 用户设置；在团队安装文档中使用实际的绝对路径：

```bash
claude plugin marketplace add /absolute/path/agent-collab
claude plugin install agent-collab@agent-collab
claude plugin list
```

安装后，通过 `/agent-collab:project-collaboration` 显式调用，也可由 Claude 根据目标确认、开工、恢复、进度或验收场景自动加载。

仅在本次开发目录中试用时，可使用：

```bash
claude --plugin-dir /absolute/path/agent-collab
```

## 接入项目

不要复制公共 Skill 到项目 `.claude/skills/`。按 [接入指南](docs/adoption.md) 操作：

1. 安装并启用同一团队版本的 Plugin。
2. 用薄 `templates/AGENTS.md` 保留项目目标来源、写权限和验收命令。
3. 从模板建立 `.herdr/current-task.json` 与 `.herdr/PROGRESS.md`，已有文件必须合并而不是覆盖。
4. 在 Claude Code 会话中加载 `/agent-collab:project-collaboration`，由该 Plugin 流程在插件上下文中执行开工校验：

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/validate_task.py" .herdr/current-task.json --phase dispatch
   ```

`${CLAUDE_PLUGIN_ROOT}` 只在 Plugin 组件上下文中被替换（对 Skill 而言，是在加载 Skill 的 Markdown 时替换），普通终端通常没有导出该变量，直接粘贴会解析到错误路径。因此上述命令必须在已加载的 Plugin 流程内运行；如果确实要脱离 Plugin 在普通 shell 中执行，请把 `${CLAUDE_PLUGIN_ROOT}` 替换为 Plugin 的实际安装绝对路径，不要依赖它已被设置。

## 团队升级

正本仓库发布新版本后，成员主动更新：

```bash
claude plugin marketplace update agent-collab
claude plugin update agent-collab@agent-collab
```

更新后重启 Claude Code。升级只更新公共流程，不应覆盖任何项目的 `.herdr/`、目标卡、证据或项目特例。版本与变更审批见 [治理约定](docs/governance.md)。

## 开发验证

```bash
./scripts/verify.sh
```

需要 Python 3 和 Claude Code CLI。验证只读取本仓库，不安装 Plugin、不修改用户设置。

## 当前边界

- v0.1 提供流程、模板和开工任务卡校验，不声称 prompt 能强制所有行为。
- 校验脚本不能判断目标是否正确、测试是否有业务价值，也不能替代独立复审和人的业务验收。
- 初始方案不启用组织强制安装；如未来要求所有成员不可关闭，再单独设计 managed settings。
- 本仓库暂不包含远程发布地址。提交、推送和团队分发需要明确授权后执行。
