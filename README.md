# agent-collab

给你和团队统一维护的项目协作 Plugin：把目标确认、过程沟通、人机决策边界、状态恢复和证据验收放进同一版本化正本，项目仓库只保留自己的事实与状态。

## 包含什么

- `skills/project-collaboration/`：按需加载的项目协作主流程及专项参考。
- `templates/`：薄 `AGENTS.md`、当前任务卡、进度与恢复入口模板。
- `scripts/validate_task.py`：无第三方依赖的任务卡结构与开工就绪校验。
- `scripts/agent_eval.py`：评估记录校验与历史画像汇总；只校验结构、门禁和确定性规则。
- `scripts/verify.sh`：单元测试、任务卡与评估样例校验、rollup 快照和 Claude Code Plugin 校验。
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

## 让 agent 自行安装

把下面整段发给 Claude Code agent。它会先检查现有安装，再添加公开 marketplace、安装或更新 Plugin，并用 `claude plugin list` 核对版本；遇到权限确认或命令失败会停下来报告，不绕过。

```text
请帮我安装团队协作 Plugin agent-collab，只做安装与验证：不要修改项目代码或
CLAUDE.md/AGENTS.md，不要把 Plugin 的 Skill 复制到项目目录，不要 commit 或 push。

来源：https://github.com/xiaobin/agent-collab
期望版本：0.2.0（若仓库已发布更高版本，先报告实际版本，不要自行跨版本升级）
安装范围：user

按顺序执行：
1. 运行 `claude plugin marketplace list` 和 `claude plugin list`，判断是否已添加
   marketplace `agent-collab`、是否已安装 `agent-collab@agent-collab`。
2. marketplace 未添加时运行：
   claude plugin marketplace add xiaobin/agent-collab
   已添加时运行：
   claude plugin marketplace update agent-collab
3. Plugin 未安装时运行：
   claude plugin install agent-collab@agent-collab
   已安装时运行：
   claude plugin update agent-collab@agent-collab
4. 运行 `claude plugin list`，确认 agent-collab@agent-collab 的 Status 为 enabled，
   Version 与期望版本一致。
5. 如果出现交互确认、权限不足或 blocked，停止并原样报告，不要修改权限设置或
   使用 --plugin-dir 代替正式安装。

完成后汇报：实际执行的命令、退出码、安装前后的版本、还需要我做什么。
不要声称安装成功，除非 `claude plugin list` 已显示 enabled 和正确版本。
```

安装或更新后重启 Claude Code，再在新会话中使用 `/agent-collab:project-collaboration`。上面的命令会写入用户级 Claude Code 设置；如果团队要求固定版本，应由团队在接入说明中给出该版本，而不是让 agent 自行选择。

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

## Agent 评估与派单画像

每个 Attempt 结束后向项目 `.herdr/agent-evaluations.jsonl` 追加一条记录，结构见 `templates/agent-evaluation.json`。最终判决前用 `stage: "final"` 记录在 Plugin 上下文中校验，派单前汇总同类历史：

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/agent_eval.py" check <record.json> --final
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/agent_eval.py" rollup .herdr/agent-evaluations.jsonl --min-samples 3
```

`${CLAUDE_PLUGIN_ROOT}` 的替换规则同上文：只在 Plugin 流程内有效，普通终端需换成实际安装绝对路径。

退出码按命令区分：

- `check`：`0` 为 `pass`；`1` 为结构有效但结论非通过（`fail` 或 `insufficient_evidence`）；`2` 为记录结构或命令错误。
- `rollup`：`0` 为已产出汇总（包括空汇总）；`2` 为 JSONL 格式错误、记录结构错误、`evaluation_id` 重复、correction 链非法或命令错误。

记录写错时不改写历史：追加一条使用新 `evaluation_id` 并通过 `correction_of` 指向被更正记录的 correction；汇总只统计每条链最新的记录。字段、门禁、归因、correction、benchmark 与反 Goodhart 口径见 [Agent 评估](skills/project-collaboration/references/evaluation.md)。

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

- v0.2 在目标、沟通、状态恢复和任务卡校验基础上新增 Agent 评估与画像汇总；不声称 prompt 或分数能强制所有行为。
- 校验脚本不能判断目标是否正确、测试是否有业务价值，也不能替代独立复审和人的业务验收。
- 评估脚本只校验记录结构与确定性门禁，不能证明引用文件真实、命令确实执行或业务已验收；画像不用于自动淘汰、自动部署或对外发布。
- 初始方案不启用组织强制安装；如未来要求所有成员不可关闭，再单独设计 managed settings。
- 公共正本通过 GitHub `xiaobin/agent-collab` 分发；提交、推送和版本发布仍需明确授权，agent 不得自行改写或发布公共正本。
