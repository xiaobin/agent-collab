# 飞书任务与评论定时同步：方案留档

状态：**只读采集、常驻循环与分诊/人读输出（manual）已实施，Herdr 自动分诊派发和部署仍为设计留档**。已实现只读 `feishu` 适配器、`run --once`/`run --resident`、单实例锁、心跳/租约、认证停止、指数退避、run.log、作者姓名缓存、附件判重键、项目/用户批准绑定，以及确定性分诊批次、结果校验、处置记录和 Markdown/面板报告；CLI 不调用模型，分诊结论由按需 Agent 提供。Herdr/cron 自动派发、Agent 常驻分诊和 token 自动续期尚未实现。

本文是通用 Bot 框架下的 `feishu-sync` 场景实例；通用层负责定义、批准、运行时、生命周期和权限，本文保留飞书配置、游标、评论分诊和人读输出细节。通用设计见 [`docs/bot-framework-design.md`](bot-framework-design.md)。

## 目标与边界

未来可新增一个可选的飞书同步组件，通过现有 `meegle` CLI 定时拉取当前用户的飞书任务与已跟踪工作项评论，维护本地增量状态；确定性 CLI 产出机器可读事件，现有协作 Agent 按批分诊并生成人读报告，面板只展示精简摘要和运行异常。

本轮只确定设计与配置契约：

- 只读拉取，不新增、修改或回复飞书任务和评论；
- 项目标识、人员标识、路径和运行策略全部配置化，换项目只改配置，不改代码；
- 真实项目 ID、用户 ID、认证信息、游标、任务快照和报告只留在使用方项目的本地目录，不进入公共仓；
- 评论分页与排序、任务变化字段、作者姓名和附件判重已完成真实只读验证；附件名和工作项前端链接已确认当前接口不提供，不猜造。只读常驻循环、分诊批次和人读报告已实现；Herdr/cron 自动派发、Agent 常驻分诊和 token 自动续期仍未实现。

## 落位与形态

### 对现有仓库的判断

本仓当前不是“所有能力都做成独立 Skill”的结构，而是明确区分两类载体：

- `project-collaboration` 是唯一公共 Skill，承载需要模型理解的目标对齐、生命周期、沟通、Herdr 调度和验收规则；Claude Code 从 Plugin 根目录发现它，Pi 通过 `package.json` 的 `pi.skills` 显式加载它，Codex 通过 `.codex-plugin/plugin.json` 的 `./skills/` 导出它。
- 确定性逻辑放在公共 Python CLI 中，例如 `scripts/validate_task.py` 和 `scripts/agent_eval.py`；Skill 内的薄 shell 包装器从自身位置定位仓库根目录，再调用公共脚本。三端实装验证会直接运行安装副本中的包装器，而不是调用模型。
- 模板只保存项目接入或机器可读结构；Herdr 是按需参考，不是 manifest 依赖。本仓没有通用 Agent 定义目录，也刻意不注册自动 hook。

飞书同步的主要工作是配置校验、分页、去重、游标、锁、状态写入和定时循环，这些都要求确定性和可重复测试，不需要模型每次参与。因此它应沿用现有的“公共 CLI + 薄包装器 + 按需参考”模式，而不是另建一个以提示词为主的 Skill。

### 选择的形态

**选择：通用 `bot.py` 控制面 + 确定性 `bot_runtime.py` + `bot_adapters/feishu_sync.py` 场景适配器；复用现有 `project-collaboration` Skill 提供按需操作参考，不新增第二个 Skill，不新增 Agent 定义或自动 hook。**

未来实施时的规划落位如下，当前均不创建：

```text
scripts/
├── bot.py                                 # 定义、批准、生命周期控制
├── bot_runtime.py                         # 锁、调度、批次、恢复
└── bot_adapters/
    ├── base.py                            # 适配器接口与错误分类
    └── feishu_sync.py                     # 飞书只读适配：配置、分页、去重、游标

skills/project-collaboration/
├── scripts/
│   ├── bot.sh                             # 自定位包根目录的薄包装器
│   └── feishu-sync.sh                     # 场景薄包装器（可选）
└── references/
    ├── bot-framework.md                   # 通用创建、运行、停止与恢复说明
    └── feishu-sync.md                     # 飞书接入与异常处理说明

templates/
├── bot-definition.json                    # 只含占位符的通用定义模板
├── bot-policy.json                        # 项目策略模板
└── bots/
    └── feishu-comment-triage.json         # 飞书场景模板

tests/
├── test_bot_runtime.py
├── test_feishu_sync.py
└── fixtures/feishu-sync/                  # 合成任务、评论、配置与错误场景
```

飞书适配器只负责场景相关部分；调度、锁、批准、生命周期、健康、批次和输出契约由通用层提供，避免第二个场景再复制一套循环。主 Skill 将来只增加到 `bot-framework.md` 和 `feishu-sync.md` 的按需链接；固定轮询不加载 Skill，也不调用模型。Pi 的 `pi.skills` 仍只声明现有 `project-collaboration`，Codex 和 Claude Code 继续随同一个包分发脚本，不增加第二套流程正文。实装时再按现有模式扩展三端安装副本的包装器测试。

### 为什么不选其他形态

| 候选形态 | 判断 |
| --- | --- |
| 新增独立 `feishu-sync` Skill | 不选为主体。它能让用户通过专门的 Skill 名称发现能力，但会把一个主要无需模型的守护任务绑定到 Skill 发现和模型会话，还要扩展 Pi 清单及三端入口测试，容易让“模型指令”与“确定性执行”职责混杂 |
| 只有独立脚本，没有任何 Skill 参考 | 不选。虽然运行最纯粹，但用户和 Agent 难以从现有协作入口发现接入、停止、认证失效和恢复规则；保留一个按需参考文档可以补上这一缺口，而不增加第二个 Skill |
| Agent 定义 | 不选。仓库没有跨项目通用 Agent 定义的既有模式；Herdr 的 agent 名、pane 和 cwd 是使用方项目运行事实，不应固化到公共包。常驻执行体应是进程，不是一个等待 prompt 的模型 Agent |
| 仅模板 | 不足。模板能描述配置和状态，不能实现分页、去重、锁和错误语义 |
| 自动 hook | 不选。本仓明确避免未经验证的一刀切 hook；定时访问外部服务也不应借 hook 隐式启动 |

与“新增独立 Skill”相比，这个形态**多解决**了：无模型运行、跨三端一致执行、可离线单元测试、统一支持 Herdr/cron/launchd、避免每轮模型成本，以及公共清单和入口膨胀。

它**少解决**的是：没有专门的 `/feishu-sync` 式模型入口，也不提供由模型持续对话驱动的采集过程。接入和排障仍可由现有 `project-collaboration` 引导；脚本路径和子命令必须在参考文档中明确，不能依赖用户猜测。

### 对既有配置契约的影响

原有配置项的语义、环境变量覆盖顺序、严格失败行为、本地状态隔离和只读边界保持不变；分诊与人读输出新增的配置项见后文“配置化设计”，默认值不降低只读和授权边界。形态变化主要改变调用主体：

- 定义与项目策略的结构校验属于 `bot.py`，飞书专属配置校验属于 `bot_adapters/feishu_sync.py`，都不属于模型提示词；
- `check-config` 在任何远程调用前执行同样的配置校验；
- `once` 与 `run` 共用同一套拉取、事件、游标和运行状态逻辑，避免定时与单次行为分叉；
- 配置模板可以落在公共 `templates/`，但真实配置仍只在项目本地；
- 没有 Skill 加载、模型可用性或模型上下文这一额外前置条件。

## 最小运行方案

首选仍是由 Herdr 管理的专用 pane，但调整为 **shell pane 直接运行确定性 CLI 的常驻循环**，不要求 Claude Code、Pi 或 Codex Agent 常驻。脚本负责认证检查、拉取、游标、去重、锁、结构化事件和运行状态写入；面板只接收简短的批次待分诊提示或运行异常，不接收原始评论堆叠。现有协作 Agent 只在接入、批量事件分诊、生成人读报告、异常需要判断、恢复和验收时按需参与。

该 pane 作为一个长期运行资源登记启动、停止和恢复入口，不把每次轮询伪装成一次新的 Agent 派单，也不为每轮重复写 dispatch。常规轮询事实进入运行日志和增量事件文件，启动、停止、异常和人工处理按项目 Herdr 规则留痕。

CLI 同时保留单次 `once` 形态，便于接入验证和故障重放。`cron + headless claude` 不再作为首选对照，因为定时执行不需要 headless 模型；以后如需开机恢复，可让 `cron` 或 `launchd` 直接调用同一个 CLI，而不是改变采集实现。

每轮重新枚举“我的全部待办”和“我参与的需求及其子任务”，与本地已跟踪集合取并集，因此新工作项进入上述来源后可自动被发现。工作项离开来源后按配置的保留期继续跟踪。

评论以 `comment_id` 为主要去重键，`created_at` 用于增量判断。第一版不依赖评论排序；每个工作项只有完整拉取成功后才推进自己的游标。任务快照、评论游标和报告均为项目本地运行数据。

认证失效时必须明确报告“未登录”，不得将其解释为零任务或零评论，不得推进游标；恢复认证后从原状态继续。自动续期未验证前按人工重新登录设计。`meegle auth status` 在断网或上游超时时也会返回 `authenticated:false`，因此只有 reason 明确是 401/token expired/not logged in 等鉴权问题才停轮询；server unreachable/timeout/network 按上游错误退避，无法归类按 `unknown_auth_state` 退避，均不写 `auth_required`。

## 事件分诊与自治边界

### 事件队列与批量触发

CLI 与 Agent 之间通过项目本地的持久化事件队列连接，不让常驻采集进程直接变成模型会话：

1. CLI 把通过去重的新增评论和任务变化追加到 `state_dir/events.jsonl`，事件包含稳定 `event_id`、类型、工作项标识、发生时间、首次发现时间和原始结构化内容引用。
2. 当存在未分诊事件，并且距上一批次达到 `triage_batch_interval_seconds`，或待分诊数量达到 `triage_max_batch_events` 时，CLI 原子生成一个待分诊批次。空轮询不创建批次。
3. CLI 只在面板输出一条简短的 `TRIAGE_READY` 摘要，包含批次 ID、事件数和待分诊文件路径，不逐条调用模型。
4. 默认 `triage_trigger=herdr_batch` 时，`triage dispatch` 按配置中的 workspace label + cwd 解析目标 pane，优先选择 `agent_status=idle` 且 agent 为 `claude`/`codex`/`pi` 的 Agent；协调者不得打断仍在执行其他任务的 Agent，忙时保持队列等待。派发前先排除派发方自身：`HERDR_PANE_ID`、本项目 `state/pane.pid` 对应 pane、`exclude_panes`；`allow_pane_labels` 非空时只允许匹配项。只剩自身时保持 pending 并写明原因，绝不把批次派回自己。派发前先 claim，同一批次只允许一个派发生效；`pane run` 的 0 退出只表示入队，必须等 Agent 写出完整结果并通过 `apply` 校验。没有可用协调者时保持持久化待办，不假装已经分诊，也不反复发新派单。
5. `triage_trigger=manual` 时只生成批次和提示，由用户手动触发同一分诊流程。`triage dispatch --watch --prepare` 是项目侧完整自动入口：每个周期先按批次阈值幂等建批，再按配置派发；不带 `--prepare` 时只消费已有 ready 批次。配置缺失、Herdr 不可用、无 idle pane 都保持 `pending` 并记录原因。

每个派单必须有 `goal_ref`、`batch_id`、`attempt_id`、绝对项目 cwd 和明确写范围。第一版分诊 Agent 对项目代码只读，只允许写本组件的分诊状态和报告目录。认证失效、配置错误等运行异常由 CLI 直接报警，不消耗一次模型分诊；业务事件才进入上述批次。

这样保留“shell pane 运行 CLI、Agent 不常驻”的前提，并把模型调用粒度控制在**每批一次或少量大批次分片**，而不是每条事件一次。超过单批合理上下文的，由协调者按事件顺序分片，仍不复用每条事件一个 Agent 的方式。

### 分诊输入与取数职责

CLI 负责提供可重放的事件包，Agent 负责需要判断的上下文补充：

| 输入 | 主要来源与责任 |
| --- | --- |
| 事件本体 | CLI：事件 ID、事件类型、任务字段差集或新评论、来源 ID、时间、作者和已有链接 |
| 工作项快照 | CLI：尽可能保存标题、类型、当前节点/状态、负责人、父子关系、优先级及原始字段引用；接口未提供的字段标为未知，不猜测 |
| 评论上下文 | CLI：同一工作项近期评论的有界上下文及其事件 ID，保留时间顺序；超出窗口时 Agent 可只读补拉 |
| 工作项链接 | CLI 使用接口真实返回的链接；没有链接时保留标识，不拼接未经验证的 URL |
| 历史分诊与处置 | Agent：读取该事件及相关工作项已有 triage/disposition 记录，识别已回答、已关闭和已 supersede 的事项 |
| 项目目标与权限 | Agent：读取项目薄 AGENTS、当前任务卡、写范围、非目标和验收命令；没有目标时不得把评论自动变成开发任务 |
| 本地代码与测试 | Agent：仅在判断可修复性时按需只读检索，CLI 不扫描代码、不运行测试 |
| 业务背景 | 用户或项目文档；缺失时输出 `needs_more_context`，不得由模型补造 |

评论分页、节点字段和链接能力仍需真实响应验证。补拉只能使用只读接口；分诊 Agent 不通过评论、状态修改等方式“确认已读”。

### 分诊输出

每条事件必须产生一条机器可读结论。分类回答的是“这条评论相对已交付内容和当前问题是什么意图”，不是关键词命中：

- `correction_of_delivered_work`：针对已交付物指出缺陷、回归、不符合约定或需要返工；默认高优先，必须给出可修复路径。
- `answer_to_my_question`：回答我方提出的问题，包括逐条答复；通常关闭对应待确认项或仅作知会。
- `clarification_request`：向我方提问、要口径或要决策，才允许标为需要人确认。
- `handoff_or_notice`：交接、知会、公告、完成回报，没有待我方处理的动作。
- `scope_or_contract_change`：改变范围、对外契约、接口或错误码；需要人确认并标注契约影响。
- `needs_more_context`：缺少工作项、业务背景、交付上下文或附件内容，判断不了时使用，不得硬套其他类别。

分类必须由真实 Agent 结合评论正文、相关工作项上下文和我方交付/待确认记录逐条判断。规则只允许做前置过滤，例如空评论或纯表情；启发式兜底若存在，必须标 `source=heuristic`，与 `source=agent` 分开统计和渲染，不得混在同一份报告里冒充同一口径。

每条结论至少包含：

```text
event_id
triage_id / batch_id / triage_policy_version
classification
source: agent | heuristic
urgency: high | normal | low
rationale
evidence_refs
recommended_action
required_authorization
uncertainties
status: proposed
```

`urgency` 与分类分开：需要人确认不等于紧急，仅知会也可能因生产影响而紧急。紧急程度必须引用期限、阻塞关系、明确 @、影响范围等依据，不能只凭模型语气。

机器结果追加到 `state_dir/triage/results.jsonl`；批次原文和 Agent 回执保存在 `state_dir/triage/batches/`。后续批准、拒绝、转任务、执行和验证均写入 `state_dir/triage/dispositions.jsonl`，通过 `event_id`、`triage_id` 和后续 `task_id` / `attempt_id` 关联，不回写或删除原事件。

### 去重、批次占用与幂等

- 评论事件的 `event_id` 由空间、工作项和 `comment_id` 确定性生成；任务变化事件由工作项、变化字段、前后快照及来源版本生成。相同来源重放得到相同 ID，CLI 不重复追加。
- 同一 `event_id + triage_policy_version` 只能有一个当前有效结论。需要纠正时追加新 `triage_id` 并标记 `supersedes`，不能静默反复分诊。
- 批次使用原子 `pending → claimed → done` 状态和 `attempt_id` 租约。Agent 失联后先核对已有逐事件结果，再只恢复缺失部分，不盲目整批重跑。
- 每个后续处置使用独立 `action_id`，并引用 `event_id` 和 `triage_id`。相同事件已有处置结果时不得再次创建等价动作。
- 合并在一条人读报告中的多个事件仍保留全部 `event_id`，不能因为展示合并而丢失后续处置关联。

### 自治分级与第一版边界

| 级别 | 能力 | 默认边界 |
| --- | --- | --- |
| L0 采集与知会 | 只读拉取、结构化事件、本地游标、异常提示 | CLI 可执行；远程只读，本地只写组件目录 |
| L1 分诊与建议 | 读取事件和项目上下文、只读检索代码、输出分类及建议 | 按需 Agent 可执行；只写分诊与报告文件 |
| L2 本地可逆处置 | 修改本地代码、补测试、生成补丁 | **第一版禁止自动执行**；以后也只能在已有目标、明确写范围和验收命令下作为独立 Attempt 运行 |
| L3 契约或外部写操作 | 修改 API/数据契约、回复飞书评论、改变任务状态、commit/push、部署或共享环境写入 | 每次都必须用户明确授权，不因分诊标签自动获得权限 |
| L4 禁止自治 | 自动批准未知权限、绕过鉴权/沙箱、泄露凭据、覆盖他人改动、销毁数据、关闭非本次创建的资源、无法界定影响的批量外发 | 永远不自动执行 |

**第一版只实现 L0 + L1。** `read_only=true` 和 `triage_mode=suggest_only` 保持不变，所有分类都只是建议，不是执行授权。这样处理是因为评论中的请求不天然等于已确认需求，当前任务也只有拉取、分诊和报告范围；直接自动修复会把上下文不足、业务验收缺失和外部影响引入一个只读同步组件。

未来若支持 L2，必须另增默认关闭的 `execution_mode`、独立任务卡、写范围、验收命令、授权引用和处置记录，不能靠把 `triage_mode` 改成某个值就放开。若支持飞书写操作，则需要新配置 Schema 明确 `read_only=false`、外部写入 allowlist 和逐动作审批；当前实现应继续拒绝 `false`，不能预留一个隐式后门。

未来外部写入还必须先读后写、记录前后状态和动作 ID，接口支持时使用幂等键；遇到超时先对账，不盲目重试。回滚只能针对本 Attempt 自己产生的改动，不能用 destructive reset 覆盖他人工作；外部操作可能没有可靠补偿动作，必须标为不可保证回滚并交人处理。

## 人读输出

### 分层产物与责任

采用**同源双产物，而不是两次独立判断**：

1. CLI 只产生结构化事件、分诊批次和运行状态，不把原始 JSON 或整段评论直接倒给人。
2. 分诊 Agent 对每个批次先写机器可读 `results.jsonl`，再从同一批结论生成 `reports_dir/triage/<batch_id>.agent.md`（启发式兜底为 `.heuristic.md`，不与 Agent 结论混排）人读报告。
3. 面板只展示该报告的精简版：需要决策、紧急事项、建议动作和完整报告路径。完整报告与机器结果通过相同 `batch_id` 和 `event_id` 对照。
4. 如果没有待分诊事件，不启动 Agent，也不生成空洞的人读报告。CLI 的无新增静默、认证失效和部分失败属于运行状态层，不伪造成业务分诊结论。

### 人读报告结构

报告先置顶所有 `urgency=high` 的事项，不论其分类；其余事项按“是否需要人决策”分组，每组内部再按紧急程度排序：

1. **需返工或需人决策**：列出缺陷/契约影响、在问什么、建议动作和所需授权；
2. **可由 Agent 另开任务处理**：说明建议修复、涉及范围和所需验收，不宣称已执行；
3. **需要补充上下文**：明确缺什么、向谁补；
4. **仅知会**：合并低价值状态变化和已解决事项；
5. **运行异常**：只汇总本轮真实失败，与业务事项分开。

高紧急事项同时保留其分类，置顶展示不改变机器结果中的分类或授权要求。

每个独立事项尽量压缩为“发生了什么 → 对方在问什么 → 建议动作 → 需要谁授权”，并附工作项标题、真实链接和评论时间。面板默认每组最多 5 项、总计不超过约 40 行，超出部分只给数量和完整报告路径；文件报告保留全量分组结果。

### 保真与合并边界

必须准确保留：

- 关键问句、明确请求、条件和截止时间的原意；容易歧义时直接保留关键短句；
- 评论作者、原始时间及其时区、工作项链接、来源事件 ID；
- `@` 了谁，尤其是否明确 @ 当前用户；不得把旁听描述成点名要求；
- 附件原始文件名及接口提供的链接，未读取附件内容时不得推断其正文；
- 不同人的相反意见、尚未解决的疑问，以及“建议”与“已决定”的区别。

可以归纳合并：

- 同一工作项同一讨论串的重复提及和相同诉求；
- 已被后续评论明确覆盖的旧状态或旧结论，但保留“已被覆盖”的关系；
- 同类纯状态变化，可聚合数量和代表性链接；
- 同一事项的人读展示可合并多个 `event_id`，机器结果仍逐项保存。

不得把冲突意见合并成虚假共识，不得把旧评论的时间改成报告生成时间，也不得为了缩短而删掉仍待回答的问题。

### 空结果、部分失败与频次

- 常规轮询无新增：**默认静默**，只写运行日志，不每 10 分钟输出“无新增”。用户手动执行单次检查时，可返回一句“无新增”。
- 无新评论但有任务变化：明确写“无新增评论，有 N 项任务变化”，不能统称为空结果。
- 认证失效：立即显示“未登录/认证失效、本轮未拉取、游标未推进”，不等下一个分诊批次。
- 部分工作项失败：立即显示“部分拉取失败，结果可能不完整”，给出成功/失败数量和失败详情路径；失败项游标不推进。它绝不能显示成“没有新评论”。
- 有待分诊事件时按批次窗口提醒，未变化的旧批次不在每次轮询重复刷屏。只有新事件加入、批次状态变化或失败恢复时才更新提示。
- 默认不额外生成每日摘要；常规业务输出只来自增量分诊批次。若以后增加日报，应单独配置并复用已分诊结果，不重新调用模型分诊全部历史事件。

## 阶段 2 实测元数据结论

以下是一次真实只读拉取的结果，不包含项目或人员标识：

- 评论作者：`meegle user search --project-key <pk> --user-keys <key>...` 可按 `user_key` 批量解析，单次最多 20 个；本次 4 个唯一作者全部有 `name_cn`/`name_en`，161 条评论姓名解析成功 161 条、失败 0 条。适配器只使用 `name_cn` 或 `name_en`，不拿登录用户姓名兜底；解析失败写 `creator_name=null`、`creator_name_status=unknown` 和原因。姓名缓存默认在工作区 `state/user-cache.json`（`0600`），查不到的键按较短 TTL 重试。
- 附件名：`comment list` 只返回 `file_url`，`attachment prepare-download` 只返回签名下载计划，不返回文件名；对签名地址做 HEAD 也没有 `Content-Disposition`。因此事件写 `name=null`、`name_status=unavailable`，不下载附件正文。同一工作项重复拉取时 11/11 个 `file_url` 稳定且互不相同，可生成 `dedupe_key` 用于判重，但不能当文件名。
- 工作项前端链接：`mywork todo` 与 `workitem get` 的真实响应中没有任何 http(s) 字段；事件不生成 `web_url`，也不根据项目/工作项 ID 拼猜链接。
- 批准绑定：`approve` 要求 `project_key` 和 `expected_user_key`，快照记录 `binding`；运行时从当前配置和环境变量重算，任一值变化退出 3。

## 阶段 3 实测分诊验证

以下是一次真实项目的只读验证结果，测试副本位于临时目录，不包含项目或人员标识：

- 输入：常驻采集产生的 169 条事件（161 条评论、4 条工作项发现、4 条工作项更新），健康状态正常。
- 批次：按 `triage_max_batch_events=20` 生成 9 个待分诊批次（20×8 + 9），空事件不建批。
- 分诊：由 5 个独立 Agent 在真实事件正文、作者、时间、附件和工作项上下文中逐条判断，全部标记 `source=agent`，没有启发式结果。最终有效结论 169 条：需返工 11、澄清请求 18、需确认契约/范围 17、已回答 23、仅知会 92、待补上下文 8；高优先级 26。
- 作者：`results.jsonl` 171 行均有 `author`；其中 8 条为 `unknown`，因为原事件本身没有作者姓名或 `user_key`，没有用登录用户姓名兜底。
- 去重：`results.jsonl` 171 行（169 条有效结论 + 2 条 `supersedes` 纠正）、169 个唯一事件、169 个唯一有效 `triage_id`；`evidence_refs` 和 `uncertainties` 均为 JSON 数组，不是字符串；没有重复追加或旧口径混入。
- 人工抽样：独立复核抽检发现 2 条误判（空正文附件误判知会、混合契约意图误判回答型），已用 `supersedes` 纠正；修正后 11 条样例分类与理由一致，其中 3 条为 `correction_of_delivered_work`。
- 报告：全量 Markdown 报告 1538 行；面板摘要 38 行，低于 40 行上限。面板顶部只给高优先级总数和分组分布（需返工 11、需人决策 6、需确认契约/范围 8、仅知会 1），每个事件只在自己的分组出现一次，组头标“其中 N 项标 [高]”；`@姓名<!-- mention:... -->` 中的 HTML 元数据已剥离，只保留 `@姓名`。
- 未执行任何飞书写操作；分诊结论仍为 `suggest_only`，分类标签不是执行授权。

### 阶段 3 Herdr 派发验证

- 使用一个临时 workspace label 和临时 pane 验证，不占用调度会话，也不按 pane 编号配置目标。
- 假 Herdr 覆盖：idle 正常派发、working 时保持 pending、Herdr 不存在时保持 pending、重复调用不重复发送、Agent 未产出时超时并记录缺失事件、只对缺失事件发一次补救提示、输出非法时不标记已分诊。
- 真实临时 pane：派发一个无害任务，Agent 读取 batch 文件并写出结果；`triage dispatch` 等到输出后调用现有 `apply` 和 `report`，记录 `state=applied`、`pane_id`、`classification_counts` 和 `result_count`。真实 pane 验证后关闭，不影响常驻采集进程。

## 配置化设计

### 配置项

配置格式使用 JSON，`schema_version` 当前固定为整数 `1`。`triage_trigger`、`triage_batch_interval_seconds`、`triage_max_batch_events`、`triage_mode`、`human_report_max_items_per_group`、`human_report_max_lines` 已由分诊 CLI 使用；其余字段仍是场景适配器的设计契约，未全部实现。

| 配置项 | 环境变量 | 必填 | 内置默认 | 约束与含义 |
| --- | --- | --- | --- | --- |
| `schema_version` | 不覆盖 | 是 | 无 | 只能为整数 `1`；布尔值和浮点数不合法 |
| `project_key` | `AGENT_COLLAB_FEISHU_PROJECT_KEY` | 是 | **无** | 非空字符串；空间标识，不允许内置任何真实项目值 |
| `user_key` | `AGENT_COLLAB_FEISHU_USER_KEY` | 是 | **无** | 非空字符串；当前用户标识，不从其他项目或历史状态继承 |
| `poll_interval_seconds` | `AGENT_COLLAB_FEISHU_POLL_INTERVAL_SECONDS` | 否 | `600` | 正整数；轮询间隔，建议不低于 60 秒 |
| `tracking_retention_days` | `AGENT_COLLAB_FEISHU_TRACKING_RETENTION_DAYS` | 否 | `7` | 非负整数；工作项离开自动发现来源后的继续跟踪天数，`0` 表示不额外保留 |
| `state_dir` | `AGENT_COLLAB_FEISHU_STATE_DIR` | 否 | `.herdr/feishu-sync/state` | 相对于使用方项目根目录；必须留在项目内，不允许绝对路径或 `..` 越界 |
| `reports_dir` | `AGENT_COLLAB_FEISHU_REPORTS_DIR` | 否 | `.herdr/feishu-sync/reports` | 相对于使用方项目根目录；必须留在项目内，不允许绝对路径或 `..` 越界 |
| `active_hours` | `AGENT_COLLAB_FEISHU_ACTIVE_HOURS` | 否 | `null` | `null` 表示全天；否则为下述运行时段对象 |
| `read_only` | `AGENT_COLLAB_FEISHU_READ_ONLY` | 否 | `true` | 只能为 JSON 布尔值；当前方案仅支持 `true`，`false` 直接报“不支持写模式”，不得隐式放开飞书写操作 |
| `initial_report_mode` | `AGENT_COLLAB_FEISHU_INITIAL_REPORT_MODE` | 否 | `full` | `full` 为首轮建基线并由 Agent 生成首份人读报告，`silent` 为静默建基线；之后均只报增量 |
| `report_targets` | `AGENT_COLLAB_FEISHU_REPORT_TARGETS` | 否 | `["herdr_panel", "file"]` | 人读报告的输出位置；至少一项，当前只允许 `herdr_panel` 和 `file`，不允许 `feishu_comment` |
| `triage_trigger` | `AGENT_COLLAB_FEISHU_TRIAGE_TRIGGER` | 否 | `herdr_batch` | `herdr_batch` 表示由现有 Herdr 协调者按批派发；`manual` 只生成待分诊批次；其他值非法 |
| `triage.dispatch.workspace_label` | 无 | `herdr_batch` 时必填 | 无 | Herdr workspace 的 `label`；只允许 label/约定名，禁止 `w4:p1` 这类 pane 编号；缺失时保持 pending |
| `triage.dispatch.cwd_marker` | 无 | `herdr_batch` 时必填 | 无 | 目标 Agent 工作目录；相对路径按项目根解析，只匹配该 workspace 内 cwd 相同或位于其下的 pane |
| `triage.dispatch.ack_timeout_seconds` | 无 | 否 | `300` | 正整数；派发后等待 Agent 结果文件的最长秒数，超时记录缺失事件，不标记已分诊 |
| `triage.dispatch.output_dir` | 无 | 否 | `state/triage/output` | 相对于 bot 目录；Agent 结果文件目录，必须留在 bot 目录内 |
| `triage.dispatch.exclude_panes` | 无 | 否 | `[]` | 额外排除的 pane ID 或 label；派发方自身由 `HERDR_PANE_ID` / `state/pane.pid` 自动排除 |
| `triage.dispatch.allow_pane_labels` | 无 | 否 | `[]` | 空表示不限制；非空时只允许匹配 label、terminal title 或 pane_id 的 pane |
| `triage_batch_interval_seconds` | `AGENT_COLLAB_FEISHU_TRIAGE_BATCH_INTERVAL_SECONDS` | 否 | `1800` | 正整数，建议不低于 300 秒；存在未分诊事件时，距上一批达到该间隔才生成新批次 |
| `triage_max_batch_events` | `AGENT_COLLAB_FEISHU_TRIAGE_MAX_BATCH_EVENTS` | 否 | `20` | 正整数；待分诊事件达到该数量时提前生成批次，避免持续积压 |
| `triage_mode` | `AGENT_COLLAB_FEISHU_TRIAGE_MODE` | 否 | `suggest_only` | 当前只允许 `suggest_only`；只输出分类和建议，不执行本地处置或飞书写操作，其他值直接报不支持 |
| `human_report_max_items_per_group` | `AGENT_COLLAB_FEISHU_HUMAN_REPORT_MAX_ITEMS_PER_GROUP` | 否 | `5` | 正整数；面板人读摘要每组最多展示项数，文件报告仍保留全量 |
| `human_report_max_lines` | `AGENT_COLLAB_FEISHU_HUMAN_REPORT_MAX_LINES` | 否 | `40` | 正整数；面板人读摘要总行数上限，超出只显示分组计数和完整报告路径 |

`active_hours` 非空时的结构：

```json
{
  "start": "09:00",
  "end": "18:00",
  "timezone": "<IANA_TIMEZONE>",
  "days": ["MON", "TUE", "WED", "THU", "FRI"]
}
```

- `start`、`end` 使用 24 小时制 `HH:MM`；允许跨午夜时段；起止相同表示该天不运行，避免与“全天”混淆。
- `timezone` 必填且使用明确的 IANA 时区名，不能依赖运行机器隐含的本地时区。
- `days` 可省略，默认一周七天；只能使用 `MON` 至 `SUN`，不得重复。
- 非运行时段只休眠，不调用 `meegle`，不推进游标。

认证 token、cookie 和其他凭据不属于本配置，不写入上述文件，继续由 `meegle` 自己的认证机制管理。

### 配置文件与解析优先级

项目根目录必须由 Herdr lane 的明确工作目录或启动参数确定，不能仅凭一个可能变化的进程当前目录猜测。

默认配置文件为相对于该项目根目录的：

```text
.herdr/feishu-sync/config.json
```

可通过 `AGENT_COLLAB_FEISHU_CONFIG` 指定另一个项目本地配置文件。该变量显式指向的文件不存在或不可读时直接报错，不能悄悄退回默认路径。

单个配置项按以下优先级合并，高优先级覆盖低优先级：

1. `AGENT_COLLAB_FEISHU_*` 环境变量，用于当前进程的临时覆盖；
2. 项目级 `config.json`；
3. 上表的内置默认值，仅适用于标注了默认值的非身份配置。

`project_key`、`user_key` 和 `schema_version` 没有内置默认。不得从其他项目配置、公共模板、历史游标或示例文档补齐。认证环境变量与本组件的配置变量分开处理，不把 `meegle` 的认证状态当作项目身份来源。

### 缺失或非法配置

配置错误必须在第一次调用 `meegle` 之前发现，进程以非零状态退出，并逐项说明：

- 缺少哪个必填项；
- 应设置的项目配置键或对应环境变量；
- 哪个值类型非法、越界或不在允许集合中；
- 哪个路径不是项目内相对路径；
- 哪个配置键未知，避免拼写错误被静默忽略。

错误示例只使用占位符：

```text
CONFIG_ERROR: missing required config "project_key".
Set "project_key" in the project config or AGENT_COLLAB_FEISHU_PROJECT_KEY.
No project-specific fallback is available.
No remote request was made and no state was changed.
```

失败时不得创建“看似成功”的报告，不得新建或推进游标，不得启动后续循环，也不得用公共仓中的任何项目或人员 ID 兜底。环境变量中的数字、布尔值、时段和数组同样按严格类型解析；空字符串不等于有效值。

### 示例配置

以下全部是占位符，不可直接作为真实项目配置运行：

```json
{
  "schema_version": 1,
  "project_key": "<PROJECT_KEY>",
  "user_key": "<CURRENT_USER_KEY>",
  "poll_interval_seconds": 600,
  "tracking_retention_days": 7,
  "state_dir": ".herdr/feishu-sync/state",
  "reports_dir": ".herdr/feishu-sync/reports",
  "active_hours": {
    "start": "<START_HH_MM>",
    "end": "<END_HH_MM>",
    "timezone": "<IANA_TIMEZONE>",
    "days": ["MON", "TUE", "WED", "THU", "FRI"]
  },
  "read_only": true,
  "initial_report_mode": "full",
  "report_targets": ["herdr_panel", "file"],
  "triage_trigger": "herdr_batch",
  "dispatch": {
    "mode": "herdr",
    "workspace_label": "<WORKSPACE_LABEL>",
    "cwd_marker": "<PROJECT_CWD>",
    "ack_timeout_seconds": 300
  },
  "triage_batch_interval_seconds": 1800,
  "triage_max_batch_events": 20,
  "triage_mode": "suggest_only",
  "human_report_max_items_per_group": 5,
  "human_report_max_lines": 40
}
```

公共仓最多保存使用占位符的文档或模板。真实配置文件、任务快照、评论游标、事件 JSONL、分诊结果、Markdown 人读报告和运行日志必须放使用方项目本地目录，并在首次运行前确认不会被提交。`state_dir` 与 `reports_dir` 也不得通过环境变量逃逸出项目根目录。升级公共包不得覆盖这些本地文件。

## 新项目最小接入步骤

以下是未来实现完成后的接入步骤，当前不可据此启动任务：

1. 由用户确认安装范围和版本，在使用方项目安装公共包；不要复制或分叉公共 Skill。
2. 明确使用方项目根目录，在项目本地创建 `.herdr/feishu-sync/config.json`，至少填写 `schema_version`、`project_key` 和 `user_key`。
3. 检查该项目的忽略规则，确保真实配置、状态、游标、日志和报告不会进入版本控制；不要覆盖已有忽略规则。
4. 由用户完成 `meegle` 登录，并确认当前身份与配置的 `user_key` 对应；配置中不保存 token。
5. 实现提供配置检查、只读单次拉取和只读常驻循环后，先运行这两项检查，再核对任务范围、评论去重、首轮事件和首份人读报告；具体命令以最终实现文档为准。
6. 确认 `triage_trigger`：`manual` 时由用户手动分发批次；`herdr_batch` 时必须已有可用协调者和明确的按需 Agent 写范围。先验证批次占用、逐事件结论、面板精简摘要和完整报告路径，再配置常驻采集。
7. 用户确认报告位置、运行时段和轮询策略后，才在指定 Herdr pane 启动常驻循环。
8. 用受控场景验证新工作项自动发现、单项失败不推进游标、认证失效明确报警、部分失败不会被显示为“无新增”、Agent 失联后只补缺失结论，以及停止 pane 后不再产生远程请求。

## 后续实施前的未验证项

- 已确认：评论分页以 `total_pages` 终止、页序为旧到新、排序用 `(created_at, comment_id)`；作者姓名与附件判重已真实验证；附件名和工作项前端链接当前接口不提供；任务变化检测使用规范化详情哈希。
- 尚未验证：跨空间/离职/空名用户的姓名解析，附件实际下载与未来可能出现的文件名字段，`update_time` 在评论新增或字段编辑时是否可靠变化。
- token 是否可自动续期及续期失败语义；
- Herdr 协调者在忙碌、无空闲 Agent 和 pane 异常时的批次保留行为，以及面板对 `TRIAGE_READY` 和人读摘要的实际展示效果；
- 分诊分类在真实审批、跨团队请求和模糊意见上的稳定性，不能只以合成事件验证；
- Herdr pane 退出、机器重启后的恢复策略；
- 报告中的个人信息保留周期与清理方式。

这些项目未验证前不得写成已支持能力。