# Bot 框架（阶段 1–2 第一步）

只在项目明确要建立自动化 bot，且用户已给出 `goal_ref` 时使用。当前实现定义、批准、单次运行、本地状态，以及真实飞书适配器的只读单次拉取；不启动常驻循环，不调用模型，不做分诊批次。

完整设计见 [通用 Bot 框架](../../../docs/bot-framework-design.md)；飞书场景见 [飞书同步设计](../../../docs/feishu-sync-design.md)。

## 1. 边界

- Bot 定义和项目策略都在使用方项目内，公共包只提供 Schema、CLI、占位模板和测试。
- 真实 `project_key`、`user_key`、凭据、游标、事件和报告不得写入公共包。
- 当前 `run` 只接受 `--once`；`agent_batch` 和常驻模式会明确拒绝。
- `feishu` 适配器只调用 `auth status`、`user me`、`mywork todo`、`comment list`、`workitem get` 等只读命令；不登录、不重试、不发送任何写请求。
- 创建 Bot 不是 Agent 的自治权限：Agent 起草，用户批准，运行时启动。
- 阶段 1 只验证逻辑边界，不提供 OS 级隔离。

## 2. 文件

```text
<project>/.herdr/bot-policy.json
<project>/.herdr/bots/<bot-id>/definition.json
<project>/.herdr/bots/<bot-id>/adapter-config.json
<project>/.herdr/bots/<bot-id>/state/{state.json,health.json,events.jsonl,lock}
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
sh "<skill_dir>/scripts/bot.sh" status   --bot-id <bot-id>
sh "<skill_dir>/scripts/bot.sh" stop     --bot-id <bot-id> --reason <reason>
sh "<skill_dir>/scripts/bot.sh" resume   --bot-id <bot-id>
```

`validate` 在缺少 `.herdr/bot-policy.json` 时只做结构校验并警告；`approve` 和 `run` 必须有策略文件。批准快照同时记录定义、项目策略和适配器配置的 SHA-256；运行前任一文件变化都会要求重新批准。批准还绑定 `bot_id`、`revision`、项目根、运行用户、批准人、时间、权限范围，以及适配器声明的运行绑定；`feishu` 的绑定是 `project_key + user_key`，批准后若配置或环境变量改成另一组身份，运行退出 3。快照不可覆盖，跨项目复制无效。

`stop` 与 `run` 使用同一把锁；`stopped`/`paused` 状态不会被 `run --once` 隐式恢复，必须显式 `resume`。`max_bots` 在批准时执行，`max_active_bots` 在运行时执行，`min_interval_seconds` 只限制 interval 触发器，不限制人工 `--once`。

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

当前 `feishu` 适配器只做单次只读拉取：枚举待办（或用 `work_item_id` 指定单项）、逐项读取详情和评论，产出 `work_item.discovered`/`work_item.updated`/`comment.created` 事件。评论按 `(created_at, comment_id)` 排序并以 `comment_id` 去重；分页超过 `max_pages`/`comment_max_pages` 视为不完整，退出 5 且不推进游标。工作项前端链接只使用真实响应字段；当前 `mywork todo` 和 `workitem get` 都没有 URL 字段，因此不生成 `web_url`，也不猜链接。

作者姓名来自 `user search` 的 `name_cn`/`name_en`，不拿登录用户姓名兜底。姓名缓存在 `user_cache_path`（默认工作区内 `state/user-cache.json`，权限 `0600`），命中和未命中分开 TTL；查不到时事件写 `creator_name=null`、`creator_name_status=unknown` 和 `creator_name_reason`，不猜名字。附件名当前不可得：事件写 `name=null`、`name_status=unavailable`，并给 `dedupe_key`（`file_url` 的 SHA-256 前缀）；`file_url` 在本次真实重复拉取中稳定，可用于判重，但不能当文件名。