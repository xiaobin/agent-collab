---
name: bot-onboarding
description: 把飞书待办与评论等只读数据接入项目本地 Bot：探测环境、生成定义与策略、校验、请求批准、首次只读运行并交付报告。用户说「帮我接入飞书同步」「让飞书待办每天自动拉下来」「建一个只读 Bot」时使用。全程由 agent 执行命令，只在批准确认和交互式登录时打断用户。
---

# Bot 接入（唯一用户入口）

三端各一条命令，语义相同：

- Claude Code：`/agent-collab:bot-onboarding`
- Pi：`/skill:bot-onboarding`
- Codex：`$agent-collab:bot-onboarding`

用户用自然语言提出同样需求时也走本 Skill。**用户不需要看到或复制任何 python 命令**；所有命令由 agent 在目标项目根执行，完整命令、参数、退出码见 [命令与失败映射](references/commands.md)。架构与状态机见 `project-collaboration` 的 [Bot 框架](../project-collaboration/references/bot-framework.md)。

记 `<plugin>` 为插件根（Claude Code 即 `${CLAUDE_PLUGIN_ROOT}`），`<project>` 为目标项目根，`<bot>` 默认为 `feishu-sync`。

## 只允许两类暂停

1. **批准确认**：给出摘要与配置哈希，等用户一句明确确认。
2. **交互式凭据**：meegle 未登录等只能由人完成的登录。

其余步骤（探测、生成、校验、首次只读运行、报告）由 agent 自己完成，不把命令推回用户，也不要求用户手动编辑 JSON。

## 流程

1. **定目标与写范围**：`goal_ref` 取当前任务卡或用户明确指令，没有就先问。写范围只限 `<project>/.herdr/`：`bot-owner.json`、`bot-policy.json`、`bots/<bot>/**`。不改业务代码，不碰其他仓库。
2. **只读探测**：
   - owner：读 `<project>/.herdr/bot-owner.json` 的 `owner_ref`。缺失或为空就停下问「用哪个 owner 标识登记」，把用户给出的原话写进该文件；**禁止**用 git email、`whoami` 或自编标识顶替。
   - 登录：`meegle auth status`。未登录就停下请用户自己登录，不代跑交互式登录、不伪造身份。
   - 空间与身份：从 meegle 待办收集 `project_key`，唯一则采用，多个则列出候选让用户选；`expected_user_key` 取 `meegle user me` 的 `user_key`，身份不符必须报出而不是继续。
3. **出计划**：运行 `bot_onboarding.py plan`，渲染 definition / policy / adapter-config 并计算三个 SHA-256。此步**不写任何文件**，输出即给用户摘要的事实底稿。
4. **写入与校验**：`bot_onboarding.py write`（幂等，内容相同不改动，计划被篡改则拒绝）→ `bot.py validate` → `bot.py explain`。
5. **批准（必须停）**：向用户展示数据来源、空间、当前身份、频率、写范围、落盘路径与三个哈希，等一句确认；确认后执行 `approve`，`--approver-ref` 用登记的 `owner_ref`，`--source-ref` 指向该确认消息。**没有用户确认消息不得批准**，agent 不得自称批准人，不得编造 `source_ref`。
6. **首次运行**：批准后由 agent 执行 `run --once`（只读、只写本地 `.herdr/`），再取 `status` 与 triage 报告路径。
7. **交付报告**：数据来源、空间、身份、频率、落盘位置、本次结果、如何暂停/恢复、配置变更需重新批准，以及未验证项。

## 硬性边界

- 不做任何飞书写操作：不评论、不改状态、不建单、不回写；不调用模型改写用户数据。
- 不替用户批准、不伪造 `source_ref`、不放宽 policy。配置、policy 或 adapter-config 漂移导致 `approval_invalidated` 时，回到第 3–5 步重新请求批准，**禁止自动重批**。
- 不自动向其他 pane 派单；分诊保持 `suggest_only`，除非用户另行授权。
- 凭据只经 meegle 自身读取；agent 不打印、不复制、不落盘 token。
- 失败原样报告：退出码、stderr、卡在哪一步，不找绿、不静默降级。
