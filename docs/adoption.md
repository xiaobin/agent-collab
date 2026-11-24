# 接入指南

## 适用对象

本指南用于团队成员把某个项目接入 `agent-collab`。接入是项目写操作，应先确认项目边界和授权；不能借接入之名覆盖现有任务状态。

## 新项目

1. 安装团队指定版本的公共包：Claude Code 和 Codex 使用各自的 Plugin marketplace，Pi 使用 Pi package。安装范围和是否固定版本必须先由用户确认，agent 不得自行假定；三种入口见 [README](../README.md)。
2. 复制 `templates/AGENTS.md` 到项目根目录，填写目标来源、写权限和真实验收命令。Claude Code 项目同时复制 `templates/CLAUDE.md`；Pi 和 Codex 可直接读取 `AGENTS.md`。
3. 创建 `.herdr/`，从模板复制 `current-task.json` 和 `PROGRESS.md`。评估记录 `.herdr/agent-evaluations.jsonl` 在第一次 Attempt 结束后由流程追加，不预填、不从其他项目复制。
4. 填写任务卡后加载对应入口：
   - Claude Code：`/agent-collab:project-collaboration`
   - Pi：`/skill:project-collaboration`
   - Codex：`$agent-collab:project-collaboration`
5. 从项目根目录执行开工校验。

   Claude Code Plugin 流程：

   ```bash
   sh "${CLAUDE_PLUGIN_ROOT}/skills/project-collaboration/scripts/validate-task.sh" .herdr/current-task.json --phase dispatch
   ```

   Pi 或 Codex 流程：

   ```bash
   sh "<skill_dir>/scripts/validate-task.sh" .herdr/current-task.json --phase dispatch
   ```

   `${CLAUDE_PLUGIN_ROOT}` 只在 Claude Code Plugin 组件上下文（例如 Skill 加载时的 Markdown 替换）中可用，普通终端通常未设置该变量。脱离 Plugin 流程、在普通 shell 中直接运行这条命令时，必须把它替换为 Plugin 的实际安装绝对路径，不能假定它已被导出。Pi 和 Codex 中的 `<skill_dir>` 也必须替换为技能加载时显示的实际安装绝对路径。

6. 确认状态文件能被下一会话恢复。

## 已有项目

先做内容分类，再改入口：

| 现有内容 | 去向 |
| --- | --- |
| 通用目标确认、生命周期、沟通、权限、验收规则 | 使用公共正本，不在项目复制第二份 |
| 当前项目目标、进行中阶段、写范围 | 保留在项目任务卡和 PROGRESS |
| 项目接口、数据、命令、环境约束 | 保留在项目文档或薄 AGENTS.md |
| 当前 diff、ledger、测试输出、失败证据 | 原样保留，不参与公共模板迁移 |
| 比公共规则更严格的项目要求 | 保留为项目特例 |

不要整份删除旧 AGENTS.md 后替换模板。先给出“保留、由公共包接管、项目特有”的对照，确认没有丢失项目约束，再分步精简。

## 验收接入

至少验证：

1. Claude Code 中 `claude plugin details agent-collab` 能看到 `project-collaboration` Skill；Pi 中 `pi list` 能看到对应包，并能在会话中调用 `/skill:project-collaboration`；Codex 中 `codex plugin list` 显示已启用，`codex debug prompt-input` 能发现 `agent-collab:project-collaboration`，并可在会话中使用 `$agent-collab:project-collaboration`。
2. 项目入口没有复制公共 Skill 正文，也没有建立 `.claude/skills/`、`.agents/skills/` 或 `.pi/skills/` 长期分叉。
3. 当前任务卡通过 `--phase dispatch`。
4. PROGRESS 能回答当前阶段、下一动作、阻塞、资源和恢复第一条命令。
5. 项目原有验收命令仍真实可执行。
6. 现有 `.herdr/`、ledger 和证据未被安装或升级覆盖。
7. 若已有 `.herdr/agent-evaluations.jsonl`，升级后逐行保留、未重排；新评估只追加，不覆盖历史。

评估记录的结构、门禁和汇总口径见 [Agent 评估](../skills/project-collaboration/references/evaluation.md)。脚本通过只代表记录结构成立，不代表业务已验收。

## 多项目统一

每个项目保存自己的状态和验收命令，但公共 Skill 只从已安装的 Claude Code Plugin、Pi package 或 Codex Plugin 加载。发现公共流程缺陷时，在 `agent-collab` 正本修复、测试、发布；不要先在某项目建立一个长期分叉。
