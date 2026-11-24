# Herdr 编排适配

## 定位

Herdr 是可选的任务编排层，不是本公共包的依赖，也不是所有项目的默认拓扑。只有用户明确要求使用 Herdr，或项目 `AGENTS.md` 已声明使用 Herdr 时，才加载本文；不使用 Herdr 的项目仍然可以用任务卡、PROGRESS 和 ledger 手工维护协作状态。

Herdr 的命令和生命周期状态由安装版本决定；本文给出规则和命令形态，实际执行前以当前环境的 `herdr --help`、`herdr --skill` 和返回的 JSON 为准。不要在未确认的版本上假设参数或状态完全相同。

在 Herdr 管理的 pane 内操作前先确认：

```bash
test "${HERDR_ENV:-}" = 1
```

检查失败时，说明当前不在 Herdr 受管 pane 内并停止，不要从外部探测或控制用户正在使用的 Herdr 会话。确认通过后，使用 `herdr` 管理 pane、tab、workspace 和 agent；不要直接运行裸 `herdr` 做发现，它会启动或附着 TUI。

## 派单前置

**没有目标就不派单。** 派单前必须能指出目标来源：任务卡、PRD、设计、Issue 或用户明确指令，并写入 `goal_ref`。派单内容、验收和修复范围都必须能回溯到该目标。

每个任务至少写清：

```text
task_id
attempt_id
goal_ref
repo / absolute cwd
允许修改范围（write_scope）
非目标
依赖任务
验收命令与业务验收人
超时时间
允许使用的权限与外部影响
```

规则：

- 一个 agent 同一时间只做一个任务，任务和 agent 名称唯一。
- `cwd` 使用目标仓库的绝对路径；agent 必须在目标仓库内工作，不在仓库外的临时副本或未经指定的 worktree 改文件。
- 同一仓库同一路径同一时间只能有一个 writer。
- 无依赖任务可并行，有依赖任务严格串行；默认并行度 2–4 个 agent。
- 写范围完全不重叠才并行；共享契约、`pom.xml`、`application.yaml`、schema、启动/安全配置、公共接口、锁文件、代码生成和数据库 migration 必须单点串行。
- 构建、测试数据和缓存必须隔离；不要让多个 lane 抢同一个 `target/`、端口、数据库或队列。
- 派单前确认 agent 处于可接收输入的状态；正在 `working` 的 agent 追加 prompt 会把当前任务切走。

## 单 lane 生命周期

每条 lane 从创建到销毁都必须显式登记：

| 阶段 | 进入条件 | 出口判据 |
| --- | --- | --- |
| 创建 | 目标、写范围、验收和依赖明确 | pane 可用，`cwd` 正确 |
| 派单 | agent 已启动且空闲 | prompt 已提交，task_id/attempt_id 已记 ledger |
| 运行 | Herdr 观察到 `working` | 回到稳定状态，或进入异常对账 |
| 回收 | `idle` / `done` / `blocked` / `exited` | 已读回执、结果落盘、状态已记录 |
| 验收 | 回执归属正确 | Git diff、测试证据和独立复审通过 |
| 集成 | 相关 writer 已验收 | 冲突清零，完整回归通过 |
| 销毁 | 没有后继任务 | 资源已释放，证据已保留，ledger 已写 verdict |

创建后立即登记 pane、agent 名、写范围、测试数据、后台命令和预期回收动作。暂停或无人值守时不能让 lane 悬在未知运行态；先回收或保留现场并写清恢复入口。

默认的独立复核在同一实现 agent 的 pane 内分屏启动，不另开无关 tab：

```bash
herdr pane split <实现-pane> --direction right --cwd "<绝对仓库路径>" --no-focus
herdr agent start <agent>-review --kind <kind> --pane <复核-pane>
```

复核者只读，写范围与实现者不重叠；完成后按收尾规则关闭该分屏。若项目文档规定了其他复核拓扑，以项目文档为准。

## 创建与派单

默认在当前 tab 创建同级 pane，保持用户焦点和当前工作目录：

```bash
herdr pane split --current --direction right --cwd "<绝对仓库路径>" --no-focus
```

从返回 JSON 的 `.result.pane.pane_id` 取 pane ID。确认它是可用 shell 后启动 agent：

```bash
herdr agent start <agent-name> --kind <agent-kind> --pane <pane-id>
```

agent 的 `kind` 和启动参数由任务与项目决定；不要假定某个工具已登录、可用或具有相同能力。

派单 prompt 必须包含边界和回传要求：

```text
任务：<具体目标>。
仓库：<绝对 cwd>。
允许修改：<write_scope>。
禁止：修改其他模块、升级依赖、提交、推送、部署或执行未授权外部操作。
验收：<command>。
完成后汇报：
1. 修改摘要；
2. 修改文件；
3. 测试命令与真实结果；
4. 未完成项、风险或阻塞。
```

批量派单时先让每一路不带 `--wait` 提交 prompt，全部发完后再逐个等待；不要让多个 agent 共用同一写范围：

```bash
herdr agent prompt <agent-name> "<prompt>"
herdr agent prompt <other-agent> "<prompt>"
herdr agent wait <agent-name> --timeout <ms>
herdr agent get <agent-name>
herdr agent read <agent-name> --source recent-unwrapped --lines 160
```

`agent prompt` 提交成功只表示文本已送达，不表示任务已执行。不要在批量派发循环里使用 `--wait`：它会等到该 agent 首次进入 `idle`/`done`/`blocked`，把后续 lane 的派发阻塞成串行。需要状态专用等待时使用 `agent wait --until`；不要重复发送默认等待参数。

## Herdr 状态处理

| Herdr 状态 | 动作 |
| --- | --- |
| `working` | 继续等待；不做并行写操作 |
| `idle` / `done` | 回收结果；这不等于成功，也不等于业务验收 |
| `blocked` | 读取审批或问题，交给用户确认；不自动批准 |
| `unknown` | 检查 agent、pane 和进程，不把未知当完成 |
| `timeout` / `stalled` | 对账原始输入和运行状态；不盲目重发 |
| `exited` | 检查 pane、历史输出和退出原因 |

`stalled` 或超时后先检查是否只是显示残留：

```bash
herdr agent get <agent-name>
herdr agent read <agent-name> --source recent-unwrapped --lines 160
```

如果确认 agent 仍在 `working`，等待原 Attempt，不重发。只有确认文本已在输入框但未提交时，才可补发提交键：

```bash
herdr agent send-keys <agent-name> enter
```

不允许重新 prompt 原任务来猜它有没有执行。不要为了绕过 `blocked` 自动点击未知审批，也不要通过关闭沙箱、放开白名单或跳过鉴权来换取通过。

## 回传协议

普通结果可以使用 `herdr agent read` 读取。长输出或自动调度时，要求 agent 把结构化结果写到仓库外的临时文件：

```text
/tmp/herdr-result-<task_id>-<attempt_id>.json
```

推荐格式：

```json
{
  "task_id": "<task_id>",
  "attempt_id": "<attempt_id>",
  "status": "done",
  "summary": "完成了什么",
  "files": ["<允许范围内的文件>"],
  "tests": [
    { "command": "<验收命令>", "exitCode": 0 }
  ],
  "risks": [],
  "remaining": []
}
```

只有 `status: "done"` 才进入验收；`blocked`、`failed` 或格式不合法进入人工处理。回执必须能对应到具体 task、agent、pane 和 Attempt；回执原文路径写入 ledger，不把临时文件写进仓库。

结果归属、Git 证据和测试证据不能由 agent 自述替代。协调者必须亲自重跑验收；重要改动交给独立只读 reviewer。完整门禁见 [证据验收](acceptance.md)。

## 调度 ledger

每次派单、状态变化、回执、验证、审查、决策、判决和集成都要追加记录。没有 ledger 记录的派单不进入验收；同一缺陷的修复使用新的 `attempt_id`，不复用原 Attempt。

最小闭环：

```text
记录 dispatch
→ 派单
→ 记录 state（done/idle/blocked/timeout）
→ 读取回执并记录 result
→ 检查 Git diff 与写范围
→ 重跑验收并记录 verify
→ 独立审查并记录 review
→ 记录 verdict
→ 全部任务完成后集成与 E2E，记录 integration
```

事件字段、append-only 和纠正方式见 [状态与交接](handoff.md)。

## 收尾

- 先确认 agent 为 `idle`、回执已落盘、ledger 已写 `result` 和 `verdict`，再关闭本次创建且确认不再使用的 pane/tab。
- 不关闭用户或其他 agent 创建的 pane；不关闭仍有后继任务的 lane。
- 保留失败证据、测试输出、diff、日志和冲突信息；不要为了“干净”删除现场。
- Herdr 是调度工具，不是验收者。`done` 只触发回收，测试和业务验收才决定能否交付。