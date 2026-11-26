# Bot 接入：命令与失败映射（agent 内部使用）

用户主路径只有 Skill 入口，本文件的命令全部由 agent 执行。`<plugin>` 为插件根（Claude Code 即 `${CLAUDE_PLUGIN_ROOT}`），`<project>` 为目标项目根，`<bot>` 默认 `feishu-sync`。所有命令在 `<project>` 下运行；插件脚本用绝对路径调用，因此不需要把任何东西复制进项目。

## 0. 只读探测

```bash
meegle auth status                 # 未登录：停下，请用户自己完成交互式登录
meegle user me                     # 取 user_key 作为 expected_user_key
meegle mywork todo --action todo   # 收集 project_key；多个空间时列出候选让用户选
```

`MEEGLE_BIN` 可覆盖 meegle 可执行文件路径（离线测试用桩程序）。owner 标识读 `<project>/.herdr/bot-owner.json`：

```json
{"owner_ref": "owner:alice"}
```

文件缺失或 `owner_ref` 为空 → 问用户并把用户原话写入；禁止回退 git email / `whoami`。

## 1. 出计划（不写定义文件）

```bash
python3 "<plugin>/scripts/bot_onboarding.py" plan \
  --project-root . --bot-id feishu-sync --goal-ref <GOAL_REF> \
  [--project-key <KEY>] [--interval-seconds 86400] \
  --output .herdr/bots/feishu-sync/onboarding-plan.json
```

模板来源：`templates/bot-definition-feishu.json`、`templates/bot-policy-feishu.json`、`templates/adapter-config.example.json`。plan 会替换全部 `REPLACE-WITH-*` 占位符（goal_ref、owner_ref、workspace_label、cwd_marker、project_key、expected_user_key），并输出 JSON：

| 字段 | 含义 |
| --- | --- |
| `files[]` | `path`（相对项目根）、`content`、`sha256` |
| `summary[]` | 给用户看的人话摘要：数据来源、空间、身份、频率、写范围、落盘路径 |
| `approve` | `approver_ref`（= 登记的 owner_ref）、`definition` 路径 |
| `probes` | 探测事实：meegle 身份、候选空间、owner 来源 |

退出码：`0` 成功；`2` 探测失败或空间不唯一（stderr 带候选列表）；`3` 需要人处理（owner 缺失、未登录）。plan 只写 `--output` 指定的计划文件，不写 definition/policy/adapter-config。

## 2. 写入与校验

```bash
python3 "<plugin>/scripts/bot_onboarding.py" write \
  --project-root . --plan-file .herdr/bots/feishu-sync/onboarding-plan.json

python3 "<plugin>/scripts/bot.py" validate \
  --definition .herdr/bots/feishu-sync/definition.json --project-root .

python3 "<plugin>/scripts/bot.py" explain \
  --definition .herdr/bots/feishu-sync/definition.json --project-root .
```

`write` 先校验计划内每个文件的 `sha256` 与 `content` 一致（被篡改即退出码 `4`），再落盘：目标不存在则创建，内容相同则报 `unchanged`，内容不同则拒绝覆盖并退出 `4`（要求重新 plan，绝不静默覆盖手改配置）。

## 3. 批准（必须先拿到用户确认消息）

```bash
python3 "<plugin>/scripts/bot.py" approve \
  --definition .herdr/bots/feishu-sync/definition.json --project-root . \
  --approval-id "apr-feishu-sync-1" \
  --approver-ref "<owner_ref>" --source-ref "<用户确认消息引用>"
```

`--approver-ref` 必须等于 `.herdr/bot-owner.json` 的 `owner_ref`；`--source-ref` 必须能回溯到用户那句确认（面板消息引用、任务卡条目或 Issue 评论）。没有确认消息就不要执行本步。重复批准同一 revision 会报 `already approved; increase revision`，属于正常防护，不要靠改 revision 绕过讨论。

## 4. 首次只读运行与结果

```bash
python3 "<plugin>/scripts/bot.py" run --once --project-root . --bot-id feishu-sync
python3 "<plugin>/scripts/bot.py" status --project-root . --bot-id feishu-sync
python3 "<plugin>/scripts/bot.py" triage prepare --project-root . --bot-id feishu-sync [--force]
python3 "<plugin>/scripts/bot.py" triage report --project-root . --bot-id feishu-sync
```

- 未批准就 `run --once` 返回退出码 `3`，这是批准门禁生效，不是故障。
- `prepare` 在批次窗口未到时输出 `TRIAGE_WAITING`；首次想立刻出批次用 `--force`。
- 尚无分诊结果时 `report` 输出 `TRIAGE_PENDING`，不产生报告文件，属正常。结果经 `triage claim` + `triage apply` 写入后，报告才落盘（结果必填字段：`event_id`、`triage_id`、`batch_id`、`triage_policy_version`、`classification`、`source`、`urgency`、`rationale`、`evidence_refs`、`recommended_action`、`required_authorization`、`uncertainties`、`status="proposed"`）。

报告：`.herdr/bots/<bot>/reports/triage/*.agent.md` 与同名 `.agent.panel.txt`；原始事件：`.herdr/bots/<bot>/state/events.jsonl`；状态字段：`state`、`last_success_at`、`last_error`。

## 5. 失败与漂移映射

| 现象 | 处理 |
| --- | --- |
| `approval binding changed (project_key/user_key); re-approve the bot` | 身份或空间变了，回到第 0 步重新探测，再重新请求用户批准 |
| `approval_invalidated` / `definition changed since approval` / `project policy changed` / `adapter config changed since approval` | 配置漂移：**禁止自动重批**，重新出计划、展示差异、等用户确认 |
| meegle 未登录或 token 失效 | 停下请用户登录；不要代跑交互式登录，不要重试轰炸 |
| `adapter identity mismatch`（退出码 6） | 当前 meegle 身份与 `expected_user_key` 不符，报给用户，不要改成别人身份继续 |
| 空间不唯一 | 列出候选 `project_key` 让用户选，不猜 |
| owner 未登记 | 问用户，登记用户原话；禁止自编 |
| 运行 lock 被占用 | 报告并等待，不删锁、不并行强跑 |

任何失败都原样带回退出码与 stderr，不找绿、不降级为「部分成功」。
