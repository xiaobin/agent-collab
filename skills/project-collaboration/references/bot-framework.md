# Bot 框架（阶段 1–3 语义分诊与报告）

只在项目明确要建立自动化 bot，且用户已给出 `goal_ref` 时使用。当前实现定义、批准、单次/常驻运行、本地状态、真实飞书适配器的只读拉取，以及确定性分诊批次校验和人读报告渲染；CLI 本身不调用模型，分诊结论必须由真实 Agent 结合评论和交付上下文逐条给出。

完整设计见 [通用 Bot 框架](../../../docs/bot-framework-design.md)；飞书场景见 [飞书同步设计](../../../docs/feishu-sync-design.md)。

## 1. 边界

- Bot 定义和项目策略都在使用方项目内，公共包只提供 Schema、CLI、占位模板和测试。
- 真实 `project_key`、`user_key`、凭据、游标、事件和报告不得写入公共包。
- 当前 `run` 接受 `--once` 或 `--resident`；`agent_batch` 和 `agent_resident` 仍会明确拒绝。常驻模式只重复确定性只读采集，不调用模型。
- `feishu` 适配器只调用 `auth status`、`user me`、`mywork todo`、`comment list`、`workitem get` 等只读命令；不登录、不重试、不发送任何写请求。
- 创建 Bot 不是 Agent 的自治权限：Agent 起草，用户批准，运行时启动。
- 阶段 1 只验证逻辑边界，不提供 OS 级隔离。

## 2. 文件

```text
<project>/.herdr/bot-policy.json
<project>/.herdr/bots/<bot-id>/definition.json
<project>/.herdr/bots/<bot-id>/adapter-config.json
<project>/.herdr/bots/<bot-id>/state/{state.json,health.json,resident.json,events.jsonl,run.log,lock,resident.lock}
<project>/.herdr/bots/<bot-id>/state/triage/{batches,results.jsonl,dispositions.jsonl,ready.jsonl}
<project>/.herdr/bots/<bot-id>/reports/triage/<batch-id>.{agent,heuristic}.{md,panel.txt}
<project>/.herdr/bots/approved/<bot-id>/<revision>.json
<project>/.herdr/bots/lifecycle.jsonl
```

模板在插件 `templates/bot-definition.json` 和 `templates/bot-policy.json`。模板中的值是占位符，不构成默认项目配置。

## 3. 命令

从项目根目录运行，`<skill_dir>` 是本 Skill 目录：

```bash
sh "<skill_dir>/scripts/bot.sh" validate --definition .herdr/bots/<bot-id>/definition.json
sh "<skill_dir>/scripts/bot.sh" explain  --definition .herdr/bots/<bot-id>/definition.json
sh "<skill_dir>/scripts/bot.sh" approve  --definition .herdr/bots/<bot-id>/definition.json \
  --approval-id <approval-id> --approver-ref <user-ref> --source-ref <message-ref>
sh "<skill_dir>/scripts/bot.sh" run      --bot-id <bot-id> --once
sh "<skill_dir>/scripts/bot.sh" run      --bot-id <bot-id> --resident --interval-seconds <seconds>
sh "<skill_dir>/scripts/bot.sh" status   --bot-id <bot-id>
sh "<skill_dir>/scripts/bot.sh" stop     --bot-id <bot-id> --reason <reason>
sh "<skill_dir>/scripts/bot.sh" resume   --bot-id <bot-id>
sh "<skill_dir>/scripts/bot.sh" triage prepare --bot-id <bot-id> [--force]
sh "<skill_dir>/scripts/bot.sh" triage claim   --bot-id <bot-id> --batch-id <batch-id> --attempt-id <attempt-id>
sh "<skill_dir>/scripts/bot.sh" triage apply   --bot-id <bot-id> --batch-id <batch-id> --file <results.json>
sh "<skill_dir>/scripts/bot.sh" triage dispose --bot-id <bot-id> --triage-id <triage-id> \
  --action <accepted|dismissed|deferred|noted|handed_off> --actor <actor-ref>
sh "<skill_dir>/scripts/bot.sh" triage report  --bot-id <bot-id> [--batch-id <batch-id>] [--source agent|heuristic]
```

`triage prepare` 只从 `events.jsonl` 生成尚未入批的事件；认证失效退出 6、部分失败退出 5、health 缺失或损坏退出 5，均不建批。达到 `triage.max_batch_events`（含恰好达到）立即切批，未达到时等 `triage.batch_interval_seconds`；`--force` 只用于受控验证。`triage_trigger=manual` 时批次保持 `pending`，由人或协调者认领；`herdr_batch` 目前同样只生成待办，不自动假装已分诊。prepare/claim/apply/dispose 共用 `state/triage/lock`，并发操作返回 4；`ready.jsonl` 缺失时 prepare 会补发。

`triage apply` 校验事件覆盖、六类语义分类、`source`、urgency、同 `event_id + triage_policy_version` 唯一、同批 `triage_id` 唯一，以及纠正必须 `supersedes` 当前有效结论；`correction_of_delivered_work` 必须是高优先级；批次 `done` 后只接受 supersede 纠正。结果整批原子追加；中途失败后原输入可幂等续跑，已存在且内容一致的结论会跳过。`triage report` 默认只用 `source=agent` 的结论，启发式结果必须用 `--source heuristic` 单独渲染，不混在同一份报告；按需返工/需人决策/需确认契约范围/已回答知会/仅知会/待补上下文分组，每组默认 5 项、总计 40 行；面板顶部只给高优先级总数和分组分布，每个事件只在所属分组出现一次，组头标“其中 N 项标 [高]”，截断时保留分组计数与完整报告路径；无结果且批次未完成时显示 `TRIAGE_PENDING`，空事件静默。结果必须带 `author`（缺失写 `unknown`），`evidence_refs`/`uncertainties` 必须是 JSON 数组；渲染时剥离 `<!-- mention:... -->`，只保留 `@姓名`。`dispositions.jsonl` 记录后续处置，不修改原事件或原结论。

`validate` 在缺少 `.herdr/bot-policy.json` 时只做结构校验并警告；`approve` 和 `run` 必须有策略文件。批准快照同时记录定义、项目策略和适配器配置的 SHA-256；运行前任一文件变化都会要求重新批准。批准还绑定 `bot_id`、`revision`、项目根、运行用户、批准人、时间、权限范围，以及适配器声明的运行绑定；`feishu` 的绑定是 `project_key + user_key`，批准后若配置或环境变量改成另一组身份，运行退出 3。快照不可覆盖，跨项目复制无效。

`stop` 与 `run` 使用同一把锁；`stopped`/`paused` 状态不会被 `run --once` 或 `run --resident` 隐式恢复，必须显式 `resume`。`max_bots` 在批准时执行，`max_active_bots` 在运行时执行，`min_interval_seconds` 只限制 interval 触发器，不限制人工 `--once`。

`run --resident` 另持 `state/resident.lock` 整个生命周期：同一 bot 重复启动退出 4；每轮采集仍持短 state 锁和配额槽，所以 `stop`/`resume` 不必等待整段睡眠。`resume` 清除 `last_run_at`，显式恢复后立即跑一轮；未到最小间隔或配额被占用时输出 `status=skipped`，不伪报成功。每轮开始前重算批准快照摘要、定义/策略/适配器配置哈希和 `binding`；过期或任一文件漂移进入 `approval_invalidated`、退出 3，不产生新一轮动作。间隔来自 `--interval-seconds` 或定义中的 interval 触发器；`--max-rounds` 只用于受控验证。`resident.json` 记录 PID、心跳、租约和停止原因；认证失效立即写 `paused`/`auth_required` 并退出 6，不自动登录；普通失败指数退避（1 秒起、30 秒封顶）；SIGTERM/SIGINT 在当前轮结束后收尾。收尾写 `state.json`/`health.json` 时持短 state 锁，避免覆盖并发 once/stop/resume 的新游标；`status` 对 active 记录核对 PID 与租约，进程已死或租约过期显示 `resident.state=stale`，不把死进程报成 running。`resident.json` 中仍存活且状态为 active 的 PID 是第二道单实例检查：即使 `resident.lock` 被手动删除，重复启动仍退出 4。`--max-rounds` 内所有轮次都失败时返回最后一次失败码并写 `degraded`/`failed`，不伪装成 stopped/0。`state/run.log` 只记录轮次、增量、游标摘要哈希和错误码，不写评论正文、作者或附件 URL。

框架自身的批准、状态、事件和生命周期写入使用 dirfd、`O_NOFOLLOW` 和原子提交，拒绝 symlink 越界；私有状态文件权限为 `0600`，新建目录为 `0700`。这仍是逻辑边界，不是 OS 沙箱。

## 4. 退出码

| 码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 2 | 定义、参数或模式错误 |
| 3 | 缺少批准、批准过期、摘要不匹配、状态/策略门禁拒绝 |
| 4 | 运行锁被占用 |
| 5 | 运行时或适配器一般失败 |
| 6 | 适配器认证失效 |

## 5. 适配器

### fake

`adapter.name` 为 `fake` 时，`adapter-config.json` 只用于本地测试：

```json
{
  "scenario": "ok",
  "fixture_path": ".herdr/bots/<bot-id>/events.json"
}
```

支持 `ok`、`auth_expired`、`partial_failure`。它不访问网络、不读凭据、不调用模型；`fixture_path` 必须位于工作区或显式 `read_scope` 内，事件必须有非空且不超过 256 字符的 `event_id`。

### feishu

`adapter.name` 为 `feishu` 时，项目策略必须允许 `feishu`，定义中 `permissions.network_read` 必须为 `true`、`network_write` 必须为 `false`。推荐把身份项留给项目本地配置或环境变量，不要写进公共模板：

```json
{
  "project_key": "${PROJECT_KEY}",
  "work_item_id": "${WORK_ITEM_ID}",
  "expected_user_key": "${USER_KEY}",
  "fetch_details": true,
  "resolve_user_names": true,
  "user_cache_path": "state/user-cache.json",
  "user_cache_ttl_seconds": 86400,
  "user_cache_miss_ttl_seconds": 300,
  "max_pages": 20,
  "comment_max_pages": 20,
  "timeout_seconds": 30
}
```

对应环境变量为 `MEEGLE_PROJECT_KEY`、`MEEGLE_WORK_ITEM_ID`、`MEEGLE_USER_KEY`、`MEEGLE_FETCH_DETAILS`、`MEEGLE_RESOLVE_USER_NAMES`、`MEEGLE_USER_CACHE_PATH`、`MEEGLE_MAX_PAGES`、`MEEGLE_COMMENT_MAX_PAGES`、`MEEGLE_TIMEOUT_SECONDS`、`MEEGLE_BIN`。身份项没有内置默认值；`work_item_id` 缺少 `project_key` 直接退出 2。`approve` 要求 `project_key` 和 `expected_user_key` 都存在，缺失退出 2；运行时认证用户必须与 `expected_user_key` 一致，不一致退出 6。认证状态由用户自己的 `meegle` 登录提供；未认证或 token 过期退出 6，不自动登录、不重试。

`feishu` 适配器只做只读拉取：枚举待办（或用 `work_item_id` 指定单项）、逐项读取详情和评论，产出 `work_item.discovered`/`work_item.updated`/`comment.created` 事件；可单次运行，也可由 `run --resident` 按间隔重复。评论按 `(created_at, comment_id)` 排序并以 `comment_id` 去重；分页超过 `max_pages`/`comment_max_pages` 视为不完整，退出 5 且不推进游标。工作项前端链接只使用真实响应字段；当前 `mywork todo` 和 `workitem get` 都没有 URL 字段，因此不生成 `web_url`，也不猜链接。

作者姓名来自 `user search` 的 `name_cn`/`name_en`，不拿登录用户姓名兜底。姓名缓存在 `user_cache_path`（默认工作区内 `state/user-cache.json`，权限 `0600`），命中和未命中分开 TTL；查不到时事件写 `creator_name=null`、`creator_name_status=unknown` 和 `creator_name_reason`，不猜名字。附件名当前不可得：事件写 `name=null`、`name_status=unavailable`，并给 `dedupe_key`（`file_url` 的 SHA-256 前缀）；`file_url` 在本次真实重复拉取中稳定，可用于判重，但不能当文件名。