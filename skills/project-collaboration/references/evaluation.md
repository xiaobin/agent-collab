# Agent 评估与派单画像

评估回答的是“这次 Attempt 在什么证据下、由谁、判成什么”，不是给 agent 打一个可跨项目流通的总分。记录为项目所有、追加式保存；画像只是派单参考，不能替代验收。

## 三层责任

| 角色 | 负责 | 不负责 |
| --- | --- | --- |
| 协调者 | 亲自重跑验收命令，记录真实退出码、测试数量和输出，写评估记录 | 替 reviewer 下复审结论 |
| 独立 reviewer | 只读评估实现与证据，判断正确性、范围、测试充分性 | 以业务价值代替技术结论，或接受实现者自证 |
| 用户 / 业务责任人 | 按真实业务链路做业务验收 | 被技术绿灯自动代表 |

Agent 永远不自证。脚本只检查记录结构、门禁取值和确定性规则；它不能证明被引用的文件真实存在、命令确实执行过，也不能判断业务价值。脚本通过不等于任务成功。

## 存储与版本

- 唯一事实来源：项目内 `.herdr/agent-evaluations.jsonl`，一行一条 JSON，只追加，不改写、不删除、不重排。
- 记错时追加一条 correction 记录：使用新的全局唯一 `evaluation_id`，并用 `correction_of` 指向被更正记录的 `evaluation_id`；原行保持原样。
- `correction_of` 指向的记录必须在同一输入中存在，不能自指，不能成环；同一条被更正记录只能有一个直接 correction（更正错了就再更正那条 correction，形成链）。correction 与原记录的 `task_id`、`attempt_id`、`stage` 必须一致。
- `check` 只能校验单条记录的 `correction_of` 类型和自指；目标存在、成环、重复 correction 与身份一致性依赖完整历史，由 `rollup` 校验。追加 correction 前，先把现有 JSONL 与候选记录合并到临时文件跑一次 `rollup` 预检，通过后再追加。
- correction 只更正判断内容，不修复记录结构。已写入的结构非法记录（例如 `fail` 却没有 findings）不能靠追加 correction 绕过：`rollup` 会继续拒绝整份历史。此时停止汇总、保留现场和证据，由用户授权后按受控修复流程处理（备份原文件、定位非法行、在独立副本中修正并记录原因）；不得静默改写或删除主账本。
- 汇总前先解析 correction 链，只统计每条链最新的记录；被替代的记录不进入分组或样本，数量记入顶层 `corrected_record_count`。
- Plugin 安装和升级只处理公共文件，不得清空、重建或覆盖项目的评估历史、原始日志和证据。
- 画像（rollup 输出、缓存的统计结果）是派生工件。派生工件可以重建，原始记录和证据才是权威；两者冲突时以原始记录为准。
- 评估记录是否纳入 Git 由项目决定；证据文件通常放仓库外临时路径，记录引用其路径和摘要。

## 记录字段

结构以插件 `templates/agent-evaluation.json` 为准。字段分五组：

| 组 | 字段 | 说明 |
| --- | --- | --- |
| 身份 | `schema_version` `evaluation_id` `correction_of` `mode` `stage` `task_id` `attempt_id` `goal_ref` `agent` `evaluator` `reviewer` `task_type` `complexity` `created_at` | `correction_of` 可选，为 `null` 或指向被更正记录的 `evaluation_id`，缺省等同 `null`；`mode` 为 `project` 或 `benchmark`；`stage` 为 `attempt` 或 `final`；`goal_ref` 必须能回溯到任务卡、PRD 或用户指令 |
| 证据 | `evidence`：`dispatch_ref` `result_ref` `review_ref` `business_acceptance_ref`；`verification[]`：`acceptance_id` `command` `cwd` `exit_code` `test_count` `output_ref` | 引用 ledger 锚点、回传文件、验证日志和业务验收记录；没有执行的证据写 `null`，不写“通过” |
| 门禁 | `gates` | 见下节；硬门禁是不可抵消的 |
| 维度 | `dimensions`：`goal_alignment` `implementation_correctness` `verification_discipline` `communication_handoff` `recovery` | 每项含 `level` `rationale` `evidence_refs`；`level` 为 0–3 或 `null` |
| 结论 | `findings[]` `metrics` `benchmark` `notes` | 每个 finding 含 `severity`（`low`/`medium`/`high`/`critical`）、`category`（`authorization`/`external_impact`/`security`/`scope`/`integrity`/`correctness`/`other`）、`summary`、`evidence_ref`、`attribution`、`blocking`（布尔）、`gate`（null 或七个门禁名之一）；`metrics` 含 `repair_rounds` `duration_seconds` `token_usage`，未知一律 `null`，不填 0；`benchmark` 见下节 |

维度等级口径：

- 0：未达成，或行为与目标相悖；
- 1：部分达成，需要返工或补证据；
- 2：达成，无明显缺陷；
- 3：达成且有可复用证据或超出要求的扎实表现。

`null` 表示未知或不适用（例如没有发生恢复事件），不能当作 0 参与平均，也不能在汇报中隐去。

`metrics` 只是辅助观察。缺失的 `token_usage`、`duration_seconds` 保持 `null`，不得为了补齐而估造。

## 硬门禁

| 门禁 | 检查什么 |
| --- | --- |
| `authorization` | 派单和写操作在用户授权内 |
| `external_impact` | 无未授权的外部影响或共享环境写操作 |
| `write_scope` | diff 未越出允许范围 |
| `evidence_integrity` | 引用可核对，回传与命令输出一致，无自述代替证据 |
| `coordinator_rerun` | 协调者亲自重跑验收，退出码和副作用符合预期 |
| `independent_review` | 独立 reviewer 给出结论并留痕 |
| `business_acceptance` | 业务责任人按真实链路确认 |

七个门禁取值相同：`pass` / `fail` / `unknown` / `pending`。

- `unknown` 是证据不足，`pending` 是尚未执行；两者都不能当作 `pass`。
- 中间 Attempt 允许 `business_acceptance: pending`，表示业务尚未走查；最终记录仍须业务确认才能 pass。
- 门禁失败不能被高维度分数抵消。维度 3 分不能把 `write_scope` 或 `evidence_integrity` 的 `fail` 改成通过。

## 结论与退出码

`check` 只按门禁、findings、验证退出码和最终证据完整度判定 `outcome`；维度是诊断信息，不参与判定。

| outcome | 判定条件 | 退出码 |
| --- | --- | --- |
| `fail` | 任一 gate 为 `fail`；或有 `blocking: true` 的 finding；或任一 verification 的 `exit_code` 非 0 | 1 |
| `insufficient_evidence` | 无硬失败，但任一 gate 为 `unknown` / `pending`；或 `stage: "final"`（含 `--final`）时最终证据不完整 | 1 |
| `pass` | 七个 gate 全为 `pass`、无 blocking finding、所有 verification 退出码 0，且最终记录的必需证据齐全 | 0 |
| 结构或 CLI 错误 | 记录不符合 schema、字段类型错误、命令用法错误 | 2 |

`check` 的退出码与上面的 outcome 一一对应：`0` 为 pass，`1` 为 fail 或 insufficient_evidence，`2` 为结构或 CLI 错误。任何被判为 `fail` 的记录必须至少有一条 finding 说明失败原因与 `attribution`；缺失时按结构错误处理（exit 2），避免失败记录被静默归入排除项、抬升 `pass_rate`。`rollup` 对同样情况也必须拒绝（exit 2），并校验输入中的全部记录。

最终记录的必需证据：`evidence.dispatch_ref`、`result_ref`、`review_ref`、`business_acceptance_ref` 非空；至少一条 verification 含非空 `cwd` 与 `output_ref`；`reviewer` 有名字。

`reviewer` 的身份规则：

- `reviewer` 与 `evaluator` 不能是同一个名字，两者也都不能等于 `agent`；违反为结构错误（exit 2）。
- `gates.independent_review` 为 `pass` 或 `fail` 时，`reviewer` 必须非空；`reviewer: null` 且该门禁为 `pass` / `fail` 是结构错误（exit 2），因为已给出的复审结论没有留名。
- `gates.independent_review` 为 `pending` 或 `unknown` 时，`reviewer: null` 合法；最终记录因此缺 reviewer 属于最终证据不足，`outcome` 为 `insufficient_evidence`（exit 1），不是结构错误。

维度 `level` 为 `null` 只表示该维度未知，写入画像的 `unknown` 计数，不改变 `outcome`；不得把 `null` 说成“阻断通过”，也不得补成 0。

区分 `fail` 与 `insufficient_evidence` 很重要：前者是已知缺陷，后者是“不知道”，不能混写成失败，也不能默认为通过。退出码 2 表示记录本身不可用，先修记录再评估。

## 归因

不通过或证据不足时，在 findings 的 `attribution` 中记录归因类别，供复盘和派单画像使用：

| 类别 | 典型情形 |
| --- | --- |
| `agent` | 实现错误、越界、自证、漏测、未按验收交付 |
| `dispatch` | 目标、写范围、验收命令或依赖描述不清，任务本身不可完成 |
| `requirements` | 目标冲突、验收标准变化或业务规则未定 |
| `environment` | 环境、依赖、权限、网络、外部服务不可用 |
| `mixed` | 多因素共同导致，无法拆成单一原因 |
| `unknown` | 现有证据不足以归因 |

外部阻塞（环境、需求、派单缺陷）要在画像中可见，但从 agent 能力通过率的分母中排除；排除项和数量必须显式列出，不能静默丢弃。归因是复盘输入，不是追责结论。

## 记录、终评与汇总

### 每个 Attempt

1. 协调者在 Attempt 结束后亲自重跑验收，收集回传、验证日志和复审记录。
2. 写一条 `stage: "attempt"` 记录：身份、证据引用、门禁、维度、findings、metrics，归因写进 `findings` / `notes`。
3. 失败证据保留在原处；后续修复用新的 `attempt_id`，不复用、不覆盖。

### 终评

一个任务在给出最终判决时，再写一条 `stage: "final"` 记录，引用同一批证据和最终门禁状态。Attempt 记录保留，用于看返工；汇总只统计 `final` 记录，避免同一 Attempt 被重复计数。

最终 `SUCCEEDED` 前必须在 Plugin 上下文中运行：

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/agent_eval.py" check <record.json> --final
```

`<record.json>` 是本次最终记录的单文件路径，不是整个 jsonl；`--final` 要求记录本身为 `stage: "final"`，并同时校验最终证据。对 Attempt 记录使用 `--final` 会返回记录错误（exit 2），不会被当成最终记录放行。`${CLAUDE_PLUGIN_ROOT}` 只在 Plugin 组件上下文（例如 Skill 加载时的 Markdown 替换）中有效；脱离 Plugin、在普通终端直接执行时，必须替换为 Plugin 的实际安装绝对路径，不能假定该变量已导出。

`check` 通过只说明记录结构和确定性门禁成立，不证明引用的文件真实或业务已验收。

### 汇总画像

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/agent_eval.py" rollup .herdr/agent-evaluations.jsonl --min-samples 3
```

- 汇总前先解析 `correction_of` 链：只统计每条链最新的记录，被替代记录计入顶层 `corrected_record_count`，不进入分组或样本。`evaluation_id` 在输入中仍必须唯一。
- 只用 `stage: "final"` 记录做能力样本；Attempt 记录计入 `skipped_attempt_records`，不重复计数。correction 不改变这一点：Attempt 的 correction 仍是 Attempt，Final 的 correction 才是样本。
- 分组键：`mode`、`agent`、`task_type`、`complexity`；benchmark 另加 `benchmark_id`、`case_version`。`trial_id` 标识组内试次，不作为分组维度。
- 输出字段：`sample_count`、`task_outcome_counts`、`agent_outcome_counts`、`excluded_outcome_counts`、`pass_rate`、`status`、各维度等级分布（0/1/2/3/unknown）、`average_repair_rounds`、`latest_evaluation_at`；顶层另有 `schema_version`、`min_samples`、`record_count`、`final_record_count`、`skipped_attempt_records`、`corrected_record_count`。
- `sample_count` 是该组 correction 解析后的最新 final 记录数，包含被判为 `fail` / `insufficient_evidence` 的样本；`pass_rate` 的可归因分母另行计算。分母为 0（没有可归因样本）时，即使 `status` 为 `observed`，`pass_rate` 也是 `null`，不补 0。
- `outcome` 为 `fail` 时，若存在 blocking findings，只用这些 blocking findings 的 `attribution` 判断是否属于 `agent` / `mixed`（即是否进入 agent 硬失败）；若没有 blocking findings，则用全部 findings 判断。这样 gate fail 加非 blocking 的 agent 归因 finding 也会进入 agent 硬失败，而不会被排除。
- `pass_rate` 的分母只含 `pass` 与归因为 `agent` 或 `mixed` 的硬失败；环境、需求、派单缺陷和证据不足计入 `excluded_outcome_counts`，分母与排除数都必须可见。
- 样本量低于 `--min-samples` 时 `status` 为 `insufficient_data`，`pass_rate` 必须为 `null`，不给数值或排名，但 `sample_count` 与各计数照常保留。
- 项目评估与 benchmark 评估分开呈现，不合并成一张总榜，不生成跨项目“通用 agent 分数”。

## Benchmark

`mode: "benchmark"` 的 `benchmark` 是对象，含三个扁平字段：

```json
"benchmark": {"benchmark_id": "...", "case_version": "...", "trial_id": "..."}
```

`mode: "project"` 时 `benchmark` 必须为 `null`。

- `benchmark_id`：基准集标识；
- `case_version`：基准集版本；
- `trial_id`：同一快照、同一 case 下的第几次试次。

公平比较要求同一 `benchmark_id`、同一 `case_version`、同一任务与验收命令、同一预算口径；`benchmark_id` 与 `case_version` 必须代表同一任务和同一验收口径，不同版本或不同任务的结果不得放在同一张表里排名。Benchmark 结果只用于选型和实验，不自动升级为项目交付证据。

## 派单选择

派单前查阅历史画像，而不是凭印象选 agent：

1. 找可比样本：相同或相近 `task_type`、`complexity`、`mode: "project"`。
2. 记录选择理由：为什么该 agent 适合这类任务，样本量多少，已知风险是什么。
3. 写入 ledger 的 `decision` 事件：`goal_ref`、备选、选择、理由、可逆性。
4. 样本不足时写 `insufficient_data`：表示没有可信排名，不是禁用该 agent。可以派，但要说明依据是任务匹配而非历史通过率。

禁止自动上传、自动淘汰、自动部署或发布个人与项目数据。画像不进入外部服务，不用于跨团队公开排名。

## 反 Goodhart

不得以下列单一指标排名、淘汰或替代验收：

- 代码行数、提交数、token 数、工具调用次数；
- 测试数量、覆盖率百分比；
- 速度或耗时；
- 自述完成率、无失败记录的“全绿”历史。

技术绿灯不能替代业务验收。指标必须带样本量、口径和证据；未知保持未知，不补零、不猜测。发现有人为优化指标而降低验收质量时，按证据完整性失败处理。

## 基于评估的自我改进

评估证据可以驱动流程改进，但必须走版本化提案：

```text
提案 → 独立复审 → 小范围试用 → 对比评估 → 保留或回滚
```

- 提案写清要改的 Skill、脚本或 hook，预期效果和失败信号。
- 独立复审检查是否放宽了安全、权限或验收要求。
- 试用使用同一快照和同一验收口径，对比前后画像。
- 无法证明效果的修改保留基线，不把“改得多”当改进。
- 核心契约、安全、发布和数据处理决策仍由人决定，不由评估分数自动触发。

## 局限

- 记录可以被不诚实的人伪造；脚本只能校验结构和取值，不能证明真实性。
- 维度等级是判断，不是测量；不同 reviewer 之间会有差异。
- 项目样本通常很小，画像容易受单个任务影响。
- Benchmark 与真实项目不同分布，横向结果不能直接外推。
- 评估记录只覆盖被记录的工作；未记录的返工和口头修复不会自动出现。
- 评估结果不改变权限、不自动放行提交、推送、发布或生产写操作。