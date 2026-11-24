# 状态与交接

## 文件分工

- `AGENTS.md`：项目事实、项目特例和入口，保持精简。
- `.herdr/current-task.json`：当前目标、范围、非目标、验收、权限与状态。
- `.herdr/PROGRESS.md`：人类可读的进度快照与恢复入口。
- 项目 ledger：调度、结果、验证、审查和决策的追加式事实记录。
- 项目评估记录：`.herdr/agent-evaluations.jsonl`，每个 Attempt 一条，最终记录与 correction 也追加；画像由它派生。
- 范围清单：跨模块任务的逐项覆盖状态。

状态是否纳入 Git 由项目决定；若 `.herdr/` 落在某个 Git 仓内，必须把 `.herdr/` 加入该仓 `.gitignore`，避免运行状态污染 diff。运行状态、评估历史和证据不得被插件安装或升级覆盖。

## Dispatch Ledger

调度事实使用项目根目录的 append-only JSONL：`.herdr/dispatch-ledger.jsonl`。一行一个事件，不改写、不删除、不重排历史；发现记错时追加 `correction`，写明被更正的事件、任务和原因。

公共字段：`ts`、`event`、`task_id`、`attempt_id`、`actor`、`note`。每个新派单或修复 Attempt 递增 `attempt_id`，修复任务不得复用原 Attempt 的 ID。派单前或同一时刻先写 `dispatch`，任务结束后不得凭记忆补记。

| event | 记录时机 | 关键字段 |
| --- | --- | --- |
| `dispatch` | 发出 prompt 之前或同一时刻 | `goal_ref` `agent` `pane` `cwd` `goal` `write_scope` `acceptance` `timeout_ms` `prompt_digest` |
| `state` | 每次观察到运行状态变化 | `herdr_state` `business_state` |
| `result` | 收到回执 | `status` `result_path` `files` `summary` |
| `verify` | 协调者亲自重跑验收 | `command` `exit_code` `output_digest` `git_summary` |
| `review` | 独立审查完成 | `reviewer` `verdict` `findings` |
| `decision` | 协调者代替人做中途决策 | `goal_ref` `question` `options` `chosen` `rationale` `reversible` |
| `verdict` | 给出业务结论 | `verdict`（`SUCCEEDED`/`FAILED`/`BLOCKED`/`UNKNOWN`）`note` |
| `integration` | 集成与联合验收 | `scope` `commands` `exit_code` `verdict` |

记录必须与真实返回一致，不得编造 agent、pane、退出码或时间戳。`result_path` 指向仓库外的临时回执时，要在事件中保留该路径。`verdict` 只能在 Git 证据、测试证据和独立审查齐备后写入；`done` 只触发回收，不触发成功。

常用读取：

```bash
tail -n 20 .herdr/dispatch-ledger.jsonl | jq -c .
jq -r 'select(.task_id=="<task_id>") | [.ts,.event,.attempt_id,(.herdr_state//""),(.verdict//"")] | @tsv' \
  .herdr/dispatch-ledger.jsonl
```

## PROGRESS 最小内容

1. 一句话结论；
2. 总目标和当前生命周期阶段；
3. 已验证覆盖数和证据；
4. 下一动作与进入条件；
5. 需要用户决定的事项；
6. 当前阻塞与风险；
7. 在飞任务、agent、写范围和占用资源；
8. 恢复第一条命令。

PROGRESS 是快照，不替代原始命令输出、diff、ledger 和审查记录。

## 恢复顺序

1. 阅读当前任务卡，确认目标和授权仍有效。
2. 阅读 PROGRESS、最近 ledger 和项目特有约束。
3. 检查当前目录、分支、工作树和未提交修改。
4. 对账运行中的 agent、后台命令、pane、worktree、端口和测试数据。
5. 核对哪些结果只有自述、哪些已有验证和独立复审；对照评估记录确认每个 Attempt 的最终门禁状态。
6. 从最近一个出口判据明确的阶段继续，不重复已完成工作，不盲目重发在飞任务。

## 中断与异常

超时、失联、崩溃或输出异常先取证和对账。保留现场、结果文件、日志和资源清单；确认原任务仍在运行时继续等待或监控，不创建替代 writer。

## 复盘沉淀

复盘机制和 harness，不归咎个人。只有可迁移且有证据的教训才进入公共规则：

```text
情境：什么形状的问题会再次触发。
观察：实际失败行为和证据。
修改：改了哪个 Skill、脚本、hook 或项目入口。
验证：什么对比或测试证明有效。
```

一次性的项目事实留在项目文档；无法隔离效果的多个同时修改，不编造单条规则的因果结论。评估记录与归因是复盘输入，流程改进按版本化提案、独立复审、试用对比后保留或回滚。
