# 接入指南

## 适用对象

本指南用于团队成员把某个项目接入 `agent-collab`。接入是项目写操作，应先确认项目边界和授权；不能借接入之名覆盖现有任务状态。

## 新项目

1. 安装团队指定版本的 Plugin。
2. 复制 `templates/AGENTS.md` 和 `templates/CLAUDE.md` 到项目根目录，填写目标来源、写权限和真实验收命令。
3. 创建 `.herdr/`，从模板复制 `current-task.json` 和 `PROGRESS.md`。
4. 填写任务卡后，在 Claude Code 会话中通过 `/agent-collab:project-collaboration` 开始任务，并在该 Plugin 流程内执行开工校验：

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/validate_task.py" .herdr/current-task.json --phase dispatch
   ```

   `${CLAUDE_PLUGIN_ROOT}` 只在 Plugin 组件上下文（例如 Skill 加载时的 Markdown 替换）中可用，普通终端通常未设置该变量。脱离 Plugin 流程、在普通 shell 中直接运行这条命令时，必须把它替换为 Plugin 的实际安装绝对路径，不能假定它已被导出。

5. 确认状态文件能被下一会话恢复。

## 已有项目

先做内容分类，再改入口：

| 现有内容 | 去向 |
| --- | --- |
| 通用目标确认、生命周期、沟通、权限、验收规则 | 使用 Plugin 正本，不在项目复制第二份 |
| 当前项目目标、进行中阶段、写范围 | 保留在项目任务卡和 PROGRESS |
| 项目接口、数据、命令、环境约束 | 保留在项目文档或薄 AGENTS.md |
| 当前 diff、ledger、测试输出、失败证据 | 原样保留，不参与公共模板迁移 |
| 比公共规则更严格的项目要求 | 保留为项目特例 |

不要整份删除旧 AGENTS.md 后替换模板。先给出“保留、由 Plugin 接管、项目特有”的对照，确认没有丢失项目约束，再分步精简。

## 验收接入

至少验证：

1. `claude plugin details agent-collab` 能看到 `project-collaboration` Skill。
2. 项目入口没有复制公共 Skill 正文。
3. 当前任务卡通过 `--phase dispatch`。
4. PROGRESS 能回答当前阶段、下一动作、阻塞、资源和恢复第一条命令。
5. 项目原有验收命令仍真实可执行。
6. 现有 `.herdr/`、ledger 和证据未被安装或升级覆盖。

## 多项目统一

每个项目保存自己的状态和验收命令，但公共 Skill 只从已安装的 Plugin 加载。发现公共流程缺陷时，在 `agent-collab` 正本修复、测试、发布；不要先在某项目 `.claude/skills/` 建一个长期分叉。
