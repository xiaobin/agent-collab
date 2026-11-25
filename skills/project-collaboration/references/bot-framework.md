# Bot 框架（阶段 1）

只在项目明确要建立自动化 bot，且用户已给出 `goal_ref` 时使用。阶段 1 只实现定义、批准、单次运行和本地状态，不连接真实飞书、不调用模型、不启动常驻循环。

完整设计见 [通用 Bot 框架](../../../docs/bot-framework-design.md)；飞书场景见 [飞书同步设计](../../../docs/feishu-sync-design.md)。

## 1. 边界

- Bot 定义和项目策略都在使用方项目内，公共包只提供 Schema、CLI、占位模板和测试。
- 真实 `project_key`、`user_key`、凭据、游标、事件和报告不得写入公共包。
- 阶段 1 的 `run` 只接受 `--once`；`agent_batch` 和常驻模式会明确拒绝。
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

`validate` 在缺少 `.herdr/bot-policy.json` 时只做结构校验并警告；`approve` 和 `run` 必须有策略文件。批准快照同时记录定义、项目策略和适配器配置的 SHA-256；运行前任一文件变化都会要求重新批准。批准还绑定 `bot_id`、`revision`、项目根、运行用户、批准人、时间和权限范围；快照不可覆盖，跨项目复制无效。

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

## 5. 阶段 1 的假适配器

`adapter.name` 只能是 `fake`。`adapter-config.json` 只用于本地测试：

```json
{
  "scenario": "ok",
  "fixture_path": ".herdr/bots/<bot-id>/events.json"
}
```

支持 `ok`、`auth_expired`、`partial_failure`。它不访问网络、不读凭据、不调用模型；`fixture_path` 必须位于工作区或显式 `read_scope` 内，事件必须有非空且不超过 256 字符的 `event_id`。真实适配器接入前，必须补上认证过期、部分失败和游标不变的行为验证。