# agent-collab

给人 + 多个 coding agent 共用、可版本化维护的项目协作规范包：把目标确认、过程沟通、人机决策边界、状态恢复和证据验收放进同一正本，项目仓库只保留自己的事实与状态。

它统一“怎么合作”，不负责“谁来干活”：不是 agent 本身，也不是调度器、CI 或权限系统。文字修订、测试和文档可由 agent 维护；目标、契约、权限和发布仍由人确认。

同一份公共流程支持三种入口：

| 工具 | 分发形式 | 调用入口 |
| --- | --- | --- |
| Claude Code | Plugin + marketplace | `/agent-collab:project-collaboration` |
| Pi | Pi package + Agent Skill | `/skill:project-collaboration` |
| Codex | Plugin + marketplace | `$agent-collab:project-collaboration` |

## 包含什么

- `skills/project-collaboration/`：按需加载的项目协作主流程及专项参考。
- `skills/project-collaboration/scripts/`：从 Skill 目录定位公共 Python 脚本的三端包装器。
- `templates/`：薄 `AGENTS.md`、当前任务卡、进度与恢复入口模板。
- `scripts/validate_task.py`：无第三方依赖的任务卡结构与开工就绪校验。
- `scripts/agent_eval.py`：评估记录校验与历史画像汇总；只校验结构、门禁和确定性规则。
- `scripts/verify.sh`：单元测试、任务卡与评估样例校验、rollup 快照和 Claude Code Plugin 校验。
- `scripts/verify_pi.sh`：隔离安装 Pi package，确认 Skill 发现和已安装包装器。
- `scripts/verify_codex.sh`：隔离安装 Codex Plugin，确认 Skill 发现和已安装包装器。
- `docs/`：接入、架构和团队治理约定。

## 可选：只读 Bot 运行内核与飞书分诊

仓库还包含一套与 Skill 体系独立的声明式运行内核，用于把「定时拉取 → 分诊 → 人读报告 → 按需派发」这类长期任务做成可批准的 bot。**bot 是长期身份，不是长期模型会话**：持久的是定义、批准快照和游标，模型只按批做判断。

### 最小试用：让飞书待办每天自动拉下来

不用记命令，对 agent 说一句「帮我接入飞书同步」即可；也可以直接调用 Skill，三端语义相同：

- Claude Code：`/agent-collab:bot-onboarding`
- Pi：`/skill:bot-onboarding`
- Codex：`$agent-collab:bot-onboarding`

agent 会在你的项目里完成探测、生成配置、校验和首次只读运行，你只需要在两类事情上回一句：

1. **确认批准**：agent 先给你数据来源、空间、身份、频率、写范围、落盘位置和配置哈希；你看完回一句「批准」。没有这句话，它不会启动。
2. **飞书登录**：meegle 未登录时 agent 会停下，请你自己登录一次；令牌不经过 agent，也不写进任何配置文件。

第一次接入时，agent 还会问一次你的 owner 标识，登记到项目 `.herdr/bot-owner.json`，作为批准人记录；它不会拿 git email 或系统用户名顶替。

**它做什么**：只读拉取你本人的飞书待办、详情和评论，写进项目 `.herdr/bots/feishu-sync/`；不评论、不改状态、不建单，也不向其他 agent 派单。

**结果在哪看**

- 报告：`.herdr/bots/feishu-sync/reports/triage/` 下的 `*.agent.md` 是人读报告，同名 `.agent.panel.txt` 是面板摘要。报告要等这一批事件由 agent 判断完才生成；采集本身只把原始事件写进 `.herdr/bots/feishu-sync/state/events.jsonl`。
- 状态：直接问 agent，或让它把 `state`（第一次是 `uninitialized`，正常跑过是 `ok`，被停或认证失效是 `paused`）、`last_success_at`、`last_error` 念给你。

**边界**：只读飞书、只写项目本地 `.herdr/`、失败原样报出；`--resident` 单实例锁基于本地文件系统，NFS 挂载未验证；token 失效需人工重新登录；配置一改，旧批准立即失效，必须重新确认。

<details>
<summary>开发者：agent 实际执行的命令、模块清单与占位符（可跳过）</summary>

上面那条 Skill 命令背后的完整命令、退出码和失败映射见 [Bot 接入命令](skills/bot-onboarding/references/commands.md)；下面是速查。

模块：

- `scripts/bot_onboarding.py`：`plan` 只读探测 meegle 与 `.herdr/bot-owner.json`，渲染三个配置文件并给出 SHA-256；`write` 按计划原样落盘，拒绝被篡改的计划和静默覆盖漂移配置。
- `scripts/bot.py`：统一 CLI，子命令 `validate` / `explain` / `approve` / `run`（`--once` 或 `--resident`）/ `status` / `stop` / `resume` / `triage`（`prepare` / `claim` / `apply` / `dispose` / `report` / `dispatch`）。
- `scripts/bot_schema.py`：定义与项目策略校验、适配器配置解析、身份绑定与定义摘要计算。适配器配置优先级为 `MEEGLE_*` 环境变量 > 定义中 `adapter.config_ref` 指向的适配器配置文件 > 内置默认（仅非身份项）。飞书适配器要求 `project_key`、`expected_user_key` 必填且无默认值，绝不兜底；配置错误在首次远程调用前非零退出。批准快照记录定义、项目策略和适配器配置的 SHA-256，任一漂移都要求重新批准，运行中进入 `approval_invalidated` 后必须重新批准；`stop` 之后必须显式 `resume`。
- `scripts/bot_runtime.py`：状态机、游标、幂等事件、单实例锁、心跳租约、指数退避、优雅停止、失败预算与运行统计。
- `scripts/bot_adapters/`：`fake`（合成数据测试）与 `feishu`（飞书只读采集：我的待办 + 评论，可按 `work_item_id` 定向）。实际只调用 `meegle auth status` / `user me` / `user search` / `mywork todo` / `workitem get` / `comment list`，任何写操作直接拒绝；姓名缓存 TTL 为成功 24 小时、未命中 5 分钟。网络抖动与 token 失效分开处理：前者按退避重试，后者停机并要求人工重新登录。
- `scripts/bot_triage.py`：分诊、人读报告与结果应用。结论必须由真实 Agent 逐条产出并标 `source=agent`；启发式结论只能标 `source=heuristic`，由 `--source heuristic` 单独渲染，不混入默认报告。
- `scripts/bot_dispatch.py`：`triage dispatch --watch [--prepare]` 派发就绪批次；批次由 `triage prepare` 按 `triage.batch_interval_seconds` 或 `triage.max_batch_events` 生成。派发经 Herdr 按 workspace label + cwd 选空闲 Agent pane，排除派发方自身与 shell/unknown pane，目标 working 时保持 `pending` 并重试。
- `templates/bot-definition-feishu.json`、`templates/bot-policy-feishu.json`、`templates/adapter-config.example.json`：飞书接入模板（带 `REPLACE-WITH-*` 占位符，由 `bot_onboarding.py plan` 填充）；`templates/bot-definition.json`、`templates/bot-policy.json` 是通用骨架。接入流程见 [Bot 接入说明](skills/project-collaboration/references/bot-framework.md)。
- 设计正本：[通用 Bot 框架](docs/bot-framework-design.md)（身份与分级创建、L0–L4 权限取交集、审批绑定），[飞书场景实例](docs/feishu-sync-design.md)。

全部子命令（在项目根目录运行，`$AC` 同上）：

```bash
python3 "$AC/scripts/bot_onboarding.py" plan  --project-root . --bot-id <bot-id> --goal-ref <goal-ref> \
  [--project-key <project-key>] --output .herdr/bots/<bot-id>/onboarding-plan.json
python3 "$AC/scripts/bot_onboarding.py" write --project-root . --plan-file .herdr/bots/<bot-id>/onboarding-plan.json
python3 "$AC/scripts/bot.py" validate --definition .herdr/bots/<bot-id>/definition.json
python3 "$AC/scripts/bot.py" explain  --definition .herdr/bots/<bot-id>/definition.json
python3 "$AC/scripts/bot.py" approve  --definition .herdr/bots/<bot-id>/definition.json \
  --approval-id <approval-id> --approver-ref <user-ref>
python3 "$AC/scripts/bot.py" run      --bot-id <bot-id> --once
python3 "$AC/scripts/bot.py" run      --bot-id <bot-id> --resident --interval-seconds <seconds>
python3 "$AC/scripts/bot.py" status   --bot-id <bot-id>
python3 "$AC/scripts/bot.py" stop     --bot-id <bot-id> --reason <reason>
python3 "$AC/scripts/bot.py" resume   --bot-id <bot-id>
python3 "$AC/scripts/bot.py" triage prepare  --bot-id <bot-id> [--force]
python3 "$AC/scripts/bot.py" triage claim    --bot-id <bot-id> --batch-id <batch-id> --attempt-id <attempt-id>
python3 "$AC/scripts/bot.py" triage apply    --bot-id <bot-id> --batch-id <batch-id> --file <results.json>
python3 "$AC/scripts/bot.py" triage dispose  --bot-id <bot-id> --triage-id <triage-id> \
  --action <accepted|dismissed|deferred|noted|handed_off> --actor <actor-ref>
python3 "$AC/scripts/bot.py" triage report   --bot-id <bot-id> [--batch-id <batch-id>] [--source agent|heuristic]
python3 "$AC/scripts/bot.py" triage dispatch --bot-id <bot-id> [--watch --prepare]
```

占位符：

| 占位符 | 含义 |
| --- | --- |
| `<bot-id>` | 这个采集任务的本地标识，飞书接入默认 `feishu-sync` |
| `<approval-id>` | 这次批准的编号，自己起名即可，用来区分多次批准 |
| `<user-ref>` | 批准人的标识，会记进批准快照 |
| `<batch-id>` | 一批待分诊事件的编号，由 `triage prepare` 生成 |
| `<triage-id>` | 单条分诊结论的编号，用来记录后续处置 |
| `<actor-ref>` | 做处置的人或 agent 的标识 |
| `<seconds>` / `<reason>` / `<results.json>` | 常驻间隔秒数 / 停止原因 / agent 写出的分诊结果文件 |

</details>

## 运行前置

三端共用包装器需要 `python3`。安装前运行 `python3 --version`；命令不可用时先安装 Python 3，不要让 agent 通过修改权限或替换脚本绕过。

## 与 Herdr 的关系

`agent-collab` 不依赖 Herdr，也不是 Herdr 插件。它定义跨项目的协作流程；Herdr 是可选的任务编排层，负责创建 pane、派发任务、等待 agent 和回收结果。

采用 Herdr 的项目，按需加载 [Herdr 编排适配](skills/project-collaboration/references/herdr.md)：公共包给出无项目事实的 lane 生命周期、派单、状态处理、回执和 ledger 规则；项目自己的仓库路径、agent 分工、命令、验收环境和特例仍留在项目 `AGENTS.md` 与 `.herdr/` 文档中。`agent-collab` 约定项目状态文件，例如 `.herdr/current-task.json`、`.herdr/PROGRESS.md` 和评估记录；不使用 Herdr 时，这些文件也可以手工或其他调度方式维护，只是没有 Herdr 提供的自动派单与回收能力。

## Claude Code 安装

执行前先确认安装范围：`user`、`project` 或 `local`，并让 marketplace 与 Plugin 使用同一范围。下面是用户级示例：

```bash
claude plugin marketplace add /absolute/path/agent-collab --scope user
claude plugin install agent-collab@agent-collab --scope user
claude plugin list
```

安装后，通过 `/agent-collab:project-collaboration` 显式调用，也可由 Claude 根据目标确认、开工、恢复、进度或验收场景自动加载。

从公开正本安装：

```bash
claude plugin marketplace add xiaobin/agent-collab --scope user
claude plugin install agent-collab@agent-collab --scope user
```

团队需要项目级或本地级安装时，把两条命令的 `user` 一起改为 `project` 或 `local`。

仅在本次开发目录中试用时，可使用：

```bash
claude --plugin-dir /absolute/path/agent-collab
```

## Pi 安装

Pi 1.6 及以上通过 `package.json` 的 `pi.skills` 加载同一个 Skill。跟踪正本主分支时安装：

```bash
pi install git:github.com/xiaobin/agent-collab
pi list
```

只有团队已经发布对应 tag 时，才可固定版本：在来源后追加 `@<已发布的 tag>`，先确认该 tag 真实存在，不要假定当前版本已有 tag。`pi install` 默认写入用户级设置；项目级安装使用 `--local`，执行前先确认范围。安装或更新后在 Pi 会话中执行 `/reload`，再用 `/skill:project-collaboration` 显式调用；Pi 也会根据 Skill 的 `description` 匹配适用场景。

`CLAUDE_PLUGIN_ROOT` 不属于 Pi 或 Codex。三端统一通过 `<skill_dir>/scripts/` 下的包装器定位 Python 脚本，Pi 和 Codex 中的 `<skill_dir>` 以技能加载时显示的绝对路径为准。

## Codex 安装

Codex 支持 Plugins 的 CLI（本次实装验证为 0.172.0）通过 `.codex-plugin/plugin.json` 加载同一个 Skill。安装公开正本：

```bash
codex plugin marketplace add xiaobin/agent-collab
codex plugin add agent-collab@agent-collab
codex plugin list
```

只有团队已经发布对应 tag 时，才可给 marketplace add 增加 `--ref <已发布的 tag>`；先确认该 tag 真实存在，不要假定当前版本已有 tag。Codex 当前只提供用户级 Plugin 安装；执行前先征得用户同意。安装后在新会话中使用 `$agent-collab:project-collaboration`；Codex 也会根据 Skill 的 `description` 匹配适用场景。

可用下面的无模型调用确认 Skill 已进入 Codex 的 prompt input：

```bash
codex debug prompt-input | grep agent-collab:project-collaboration
```

## 让 agent 自行安装

### 发给 Claude Code agent

把下面整段发给 Claude Code agent。它会先检查现有安装，再添加公开 marketplace、安装或更新 Plugin，并用 `claude plugin list` 核对版本；遇到权限确认或命令失败会停下来报告，不绕过。

```text
请帮我安装团队协作 Plugin agent-collab，只做安装与验证：不要修改项目代码或
CLAUDE.md/AGENTS.md，不要把 Plugin 的 Skill 复制到项目目录，不要 commit 或 push。

来源：https://github.com/xiaobin/agent-collab

开始前先向我确认，不要自行假定：
1. 安装范围：由我在 user、project 或 local 中选择；
2. 是否固定版本；若固定，给出具体版本。
未得到答复前只做只读检查，不添加 marketplace、不安装、不更新。

按顺序执行：
1. 先运行 `python3 --version`，再运行 `claude plugin marketplace list` 和 `claude plugin list`，
   判断是否已添加 marketplace `agent-collab`、是否已安装 `agent-collab@agent-collab`。
   `python3` 不可用时停止并报告，不要修改脚本绕过。
2. marketplace 未添加时，使用我确认的范围运行：
   claude plugin marketplace add xiaobin/agent-collab --scope <确认的范围>
   已添加时运行：
   claude plugin marketplace update agent-collab
3. Plugin 未安装时，使用同一范围运行：
   claude plugin install agent-collab@agent-collab --scope <确认的范围>
   已安装时运行：
   claude plugin update agent-collab@agent-collab
4. 运行 `claude plugin list`，确认 agent-collab@agent-collab 的 Status 为 enabled，
   并报告实际安装范围和版本。
5. 如果我要求固定版本而当前来源无法满足，停止并报告，不要改用其他版本。
6. 如果出现交互确认、权限不足或 blocked，停止并原样报告，不要修改权限设置或
   使用 --plugin-dir 代替正式安装。

完成后汇报：实际执行的命令、退出码、安装前后的版本、还需要我做什么。
不要声称安装成功，除非 `claude plugin list` 已显示 enabled 和正确版本。
```

安装或更新后重启 Claude Code，再在新会话中使用 `/agent-collab:project-collaboration`。

### 发给 Pi agent

```text
请帮我安装团队协作 Skill agent-collab 到 Pi，只做安装与验证：不要修改项目代码或
AGENTS.md，不要把公共 Skill 复制到项目目录，不要 commit 或 push。

来源：https://github.com/xiaobin/agent-collab

开始前先向我确认，不要自行假定：
1. 安装范围：用户级，还是项目级（`--local`）；
2. 是否固定版本；若固定，给出具体 tag。
未得到答复前只做只读检查，不安装、不更新。

按顺序执行：
1. 先运行 `python3 --version`，再运行 `pi --version` 和 `pi list`，检查现有安装。
   `python3` 不可用时停止并报告，不要修改脚本绕过。
2. 未安装时，使用我确认的来源和范围运行：
   固定 tag：pi install git:github.com/xiaobin/agent-collab@<确认的 tag>
   不固定：pi install git:github.com/xiaobin/agent-collab
   项目级安装追加 --local；已安装同一来源时运行对应的 `pi update`。
3. 运行 `pi list`，核对安装来源和版本；`pi list` 不展示 Skill 时，不据此声称 Skill 已加载。
4. 在 Pi 会话中执行 `/reload`，再确认可调用 `/skill:project-collaboration`。
5. 如果我要求固定 tag 而该 tag 不存在，停止并报告；不要改装 main 冒充指定版本。
6. 如果出现交互确认、权限不足或 blocked，停止并原样报告，不要修改权限设置。

完成后汇报：实际执行的命令、退出码、安装前后的来源和版本、还需要我做什么。
不要声称安装成功，除非 `pi list` 已显示对应包，且重载后的会话能发现
`skill:project-collaboration`。
```

### 发给 Codex agent

```text
请帮我安装团队协作 Plugin agent-collab 到 Codex，只做安装与验证：不要修改项目代码或
AGENTS.md，不要把公共 Skill 复制到项目目录，不要 commit 或 push。

来源：https://github.com/xiaobin/agent-collab

开始前先向我确认，不要自行假定：
1. Codex 当前只支持用户级 Plugin 安装，是否同意；
2. 是否固定版本；若固定，给出具体 tag。
未得到答复前只做只读检查，不添加 marketplace、不安装、不更新。

按顺序执行：
1. 先运行 `python3 --version`，再运行 `codex --version`、`codex plugin marketplace list` 和
   `codex plugin list`，检查现有安装。`python3` 不可用时停止并报告，不要修改脚本绕过。
2. marketplace 未添加时，使用我确认的版本运行：
   固定 tag：codex plugin marketplace add xiaobin/agent-collab --ref <确认的 tag>
   不固定：codex plugin marketplace add xiaobin/agent-collab
   已添加时运行：
   codex plugin marketplace upgrade agent-collab
3. 运行 `codex plugin add agent-collab@agent-collab`。
4. 运行 `codex plugin list`，确认 agent-collab@agent-collab 为 installed, enabled，
   并报告实际版本。
5. 运行 `codex debug prompt-input`，确认输出包含
   `agent-collab:project-collaboration`；该命令只渲染 prompt，不调用模型。
6. 如果我要求固定版本而实际版本不同，停止并报告；不要改装 main 冒充指定版本。
7. 如果出现交互确认、权限不足或 blocked，停止并原样报告，不要修改权限设置。

完成后汇报：实际执行的命令、退出码、安装前后的版本、还需要我做什么。
不要声称安装成功，除非 `codex plugin list` 显示 enabled 和正确版本，且
`codex debug prompt-input` 能发现该 Skill。
```

安装后在新会话中使用 `$agent-collab:project-collaboration`。

三种工具的默认安装范围不同：Claude Code 默认 `user`，Pi 默认用户级，Codex 当前只支持用户级。agent 必须先取得用户对安装范围和是否固定版本的确认，不能替用户选择。团队要求固定版本时，应在接入说明中给出明确版本。

## 接入项目

不要复制公共 Skill 到项目 `.claude/skills/`、`.agents/skills/` 或 `.pi/skills/`。按 [接入指南](docs/adoption.md) 操作：

1. 安装并启用同一团队版本的公共包。
2. 用薄 `templates/AGENTS.md` 保留项目目标来源、写权限和验收命令。
3. 从模板建立 `.herdr/current-task.json` 与 `.herdr/PROGRESS.md`，已有文件必须合并而不是覆盖。
4. 加载对应入口：
   - Claude Code：`/agent-collab:project-collaboration`
   - Pi：`/skill:project-collaboration`
   - Codex：`$agent-collab:project-collaboration`

   然后从项目根目录执行开工校验：

   Claude Code Plugin 流程：

   ```bash
   sh "${CLAUDE_PLUGIN_ROOT}/skills/project-collaboration/scripts/validate-task.sh" .herdr/current-task.json --phase dispatch
   ```

   Pi 或 Codex 流程：

   ```bash
   sh "<skill_dir>/scripts/validate-task.sh" .herdr/current-task.json --phase dispatch
   ```

`${CLAUDE_PLUGIN_ROOT}` 只在 Claude Code Plugin 组件上下文中被替换（对 Skill 而言，是在加载 Skill 的 Markdown 时替换），普通终端通常没有导出该变量，直接粘贴会解析到错误路径。因此上述命令必须在已加载的 Plugin 流程内运行；如果确实要脱离 Plugin 在普通 shell 中执行，请把 `${CLAUDE_PLUGIN_ROOT}` 替换为 Plugin 的实际安装绝对路径。Pi、Codex 或普通终端中的 `<skill_dir>` 同样必须替换为实际安装绝对路径，不能保留占位符。

## Agent 评估与派单画像

每个 Attempt 结束后向项目 `.herdr/agent-evaluations.jsonl` 追加一条记录，结构见 `templates/agent-evaluation.json`。最终判决前用 `stage: "final"` 记录校验，派单前汇总同类历史：

```bash
sh "<skill_dir>/scripts/agent-eval.sh" check <record.json> --final
sh "<skill_dir>/scripts/agent-eval.sh" rollup .herdr/agent-evaluations.jsonl --min-samples 3
```

Claude Code 中 `<skill_dir>` 为 `${CLAUDE_PLUGIN_ROOT}/skills/project-collaboration`；Pi 和 Codex 中为技能加载时显示的绝对路径。普通终端需换成实际安装绝对路径。

退出码按命令区分：

- `check`：`0` 为 `pass`；`1` 为结构有效但结论非通过（`fail` 或 `insufficient_evidence`）；`2` 为记录结构或命令错误。
- `rollup`：`0` 为已产出汇总（包括空汇总）；`2` 为 JSONL 格式错误、记录结构错误、`evaluation_id` 重复、correction 链非法或命令错误。

记录写错时不改写历史：追加一条使用新 `evaluation_id` 并通过 `correction_of` 指向被更正记录的 correction；汇总只统计每条链最新的记录。字段、门禁、归因、correction、benchmark 与反 Goodhart 口径见 [Agent 评估](skills/project-collaboration/references/evaluation.md)。

## 团队升级

正本仓库发布新版本后，成员主动更新。

Claude Code：

```bash
claude plugin marketplace update agent-collab
claude plugin update agent-collab@agent-collab
```

Pi：

```bash
pi update git:github.com/xiaobin/agent-collab
```

Codex：

```bash
codex plugin marketplace upgrade agent-collab
codex plugin add agent-collab@agent-collab
```

固定 release 的团队把 Pi source 换成指定 tag，把 Codex marketplace ref 固定到指定 tag。更新后重启 Claude Code 或打开新的 Codex 会话，或在 Pi 中执行 `/reload`。升级只更新公共流程，不应覆盖任何项目的 `.herdr/`、目标卡、证据或项目特例。版本与变更审批见 [治理约定](docs/governance.md)。

## 开发验证

```bash
./scripts/verify.sh
```

需要 Python 3 和 Claude Code CLI。测试包含三端 manifest、Skill frontmatter 和共享包装器，不会安装包或修改用户设置。

需要额外实装验证 Pi 时，运行：

```bash
./scripts/verify_pi.sh
```

该脚本使用临时 `PI_CODING_AGENT_DIR` 安装仓库副本，通过 Pi RPC 的 `get_commands` 确认 `skill:project-collaboration` 来自该 package，并运行已安装包装器；不调用模型，也不修改真实 `~/.pi/agent/settings.json`。公开 Git 来源、固定 tag 和真实会话中的 `/skill:project-collaboration` 仍应在发布前单独验证。

需要额外实装验证 Codex 时，运行：

```bash
./scripts/verify_codex.sh
```

该脚本使用临时 `CODEX_HOME` 安装仓库副本，通过 `codex debug prompt-input` 确认 `agent-collab:project-collaboration` 已被发现，并运行已安装包装器；不调用模型，也不修改真实 Codex 配置。公开 Git 来源、固定 tag 和真实会话中的 `$agent-collab:project-collaboration` 仍应在发布前单独验证。

## 当前边界

- Bot 运行内核只实现只读采集、分诊建议与建议派发；本地处置只记录 `triage dispose`，外部写操作未开放，飞书写操作在适配器层直接拒绝。
- 分诊语义由真实 Agent 判断，单元测试只保证管道、幂等、授权与字段形状，不能保证分类正确；同一批事件换 agent 会话可能得到不同分类，语义验收需要按批抽样人工核对。
- v0.4 在同一正本上增加可选 Herdr 编排规范、完整 dispatch ledger 和验收/修复门禁；沿用 Pi 与 Codex 分发和三端脚本入口。
- Herdr 适配只定义项目无关的调度纪律；具体仓库、pane、agent、测试环境和项目特例必须由项目自己维护，不能复制成公共通用值。
- 三种工具的 Skill 都是模型指令，不是权限系统；校验脚本不能判断目标是否正确、测试是否有业务价值，也不能替代独立复审和人的业务验收。
- 评估脚本只校验记录结构与确定性门禁，不能证明引用文件真实、命令确实执行或业务已验收；画像不用于自动淘汰、自动部署或对外发布。
- 初始方案不启用组织强制安装；如未来要求所有成员不可关闭，再单独设计 managed settings 或对应工具的组织管控。
- 公共正本通过 GitHub `xiaobin/agent-collab` 分发；提交、推送和版本发布仍需明确授权，agent 不得自行改写或发布公共正本。
