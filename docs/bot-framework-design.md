# 通用 Bot 自动创建与运行框架：从远景到落地

状态：**阶段 1、阶段 2 第一步与元数据补齐已实施；常驻运行时、Agent 调用和分诊仍未实施**。已落地定义校验、批准快照、`fake` 适配器、`run --once`、本地状态/健康/生命周期、无模型测试，以及真实 `feishu` 适配器的只读单次拉取、作者姓名缓存、附件判重键和项目/用户批准绑定；不启动常驻循环、不调用模型、不创建或分诊真实 bot。

阶段 1 实现位置：`scripts/bot.py`、`scripts/bot_schema.py`、`scripts/bot_runtime.py`、`scripts/bot_adapters/`、`skills/project-collaboration/scripts/bot.sh`、`skills/project-collaboration/references/bot-framework.md`、`templates/bot-definition.json`、`templates/bot-policy.json`、`tests/test_bot_*.py`。阶段 2 第一步实现位置：`scripts/bot_adapters/feishu_sync.py`、`tests/test_feishu_adapter.py`；真实只读验收只在临时项目运行，结果不进入公共仓。

调研基线：DS-Bot（`github.com/Fei-Craft/DS-Bot`，commit `d104d9c`）、OpenMausBot（`github.com/milind-soni/OpenMausBot`）、Hermes Bot Mode（`github.com/NousResearch/Hermes-Bot-Mode`）。三者都是桌面应用或桌面插件，形态与本仓不同；本文只抽取可复用的机制，不复制产品。

## 0. 一页结论

1. **目标不是复制一个 DS-Bot。** 目标是让本仓安装到任意项目后，项目的协调者能根据用户目标，生成一个带目标、权限、触发器和验收边界的 bot 定义，经用户批准后由确定性运行时执行，并在需要语义判断时按批调用现有 Claude Code、Pi 或 Codex。
2. **Bot 的长期身份与模型的常驻会话是两件事。** Bot 的定义、状态、游标、事件、记忆和报告长期存在；模型会话按批创建、按需恢复。默认不需要一个永远开着的模型会话。只有交互式 bot 或需要连续探索的 bot 才考虑常驻模型会话，而且它是另一种运行时，不是默认形态。
3. **创建 Bot 与运行 Bot 分开。** Agent 可以起草、校验、解释差异和请求批准；批准、启动、扩权、外部动作和回收是独立门禁。Bot 不能创建 bot，不能修改自己的定义，不能自行扩权。
4. **公共仓只放规范、Schema、CLI、占位模板和测试。** 项目 ID、人员 ID、路径、凭据、游标、事件、记忆和报告都留在使用方项目的本地目录。换项目只改配置，不改代码。
5. **MVP 只做“通用控制面 + 一个飞书只读实例”。** 先证明定义、批准、锁、游标、批次、分诊、人读报告和异常恢复，再谈第二个适配器、L2 本地动作和多 bot 协作。
6. **距离目标：阶段 1 与阶段 2 第一步已落地，后续仍未实现。** 已完成 Schema 校验器、批准与生命周期 CLI、单次进程运行时、`fake` 适配器、只读 `feishu` 适配器和无模型测试；还缺常驻运行时、Agent 调用契约、分诊批次、部署与恢复、跨三端契约和真实基准。本文第 16 节给出阶段与进入条件。

## 1. 六个问题，先给答案

| 用户的问题 | 结论 | 详见 |
| --- | --- | --- |
| 通用 bot 自动创建框架如何从远景到落地 | 先定义对象和门禁，再做单项目、单适配器、进程运行时；每个阶段有退出条件，不一次做成平台 | §16、§17 |
| 离目标还有多远 | 概念和边界已足够清楚；代码、测试、部署和真实适配器都还没有 | §16 |
| 还需要什么 | Schema 与校验器、批准记录、运行时、适配器接口、Agent 调用契约、锁与恢复、观测、基准、三端包装器 | §9、§16 |
| 基于本仓架构怎么设计 | 单一 Skill + 公共 Python CLI + 项目本地状态 + Herdr 可选；不新增第二套主流程 | §5、§14 |
| 落地阶段、部署、bot 实例、运维边界 | 五类部署拓扑、十个场景模板、运行手册和事故分类 | §11–§13 |
| 入口是什么，入口后怎么执行 | 用户从现有 `project-collaboration` 进入；协调者按固定步骤起草、校验、请求批准；批准后 CLI 启动运行时 | §6 |

## 2. 调研：三个参考实现真正值得学什么

### 2.1 DS-Bot

DS-Bot 是建立在 DeepSeek Harness（DSH）上的桌面应用/插件，Bot 是 DSH 会话里的长期身份，有独立模型、思考档位、工作目录、权限预设、记忆和后台任务。它值得学的是机制，不是产品外壳：

- **身份与运行分离。** 一个 Bot 是长期身份，但可以有多段 DSH Session；上下文管家在阈值处选择压缩或换段，旧段保留并可检索。换段时写交接笔记，而不是让一个会话无限增长。
- **派活是树，不是聊天。** `message_bot` 的 `request_id` 幂等；子活未完成，父回复先不交；禁止把任务发给正在等它的上游；等待关系持久化，重启续等；不设自动超时。
- **异步工作是一等公民。** 前台忙时，用户插话会把整轮移入后台任务；后台任务有独立会话、稳定 `taskId`、审批冒泡和重启对账。Bot 可以开自己的后台任务，但仍解禁 DSH 子代理。
- **群 Goal 用证据门禁推进。** Goal 先与人对齐“done”的定义，再拆阶段；阶段通过必须附证据；连续无进展或 admin `[PASS]` 才停下等人。
- **权限分两层。** 角色权限（谁能管理 Bot/群/团队记忆）与沙盒权限（只读、工作区修改、完全权限）独立；只有用户能设沙盒权限。
- **隐私 Bot 是单向边界，不是 OS 沙箱。** 其他 Bot 看不到它的目录、记忆和会话；它向外分享需要用户批准。文档明确承认 shell 动态路径仍可能绕过。
- **消息账本单一写入口。** 每群、每对 Bot 一本只追加的 JSONL；先写账本，其他视图从账本读；`group_relay` 只记锚点，不把长回复塞进工具结果。
- **本地 Agent 作为 Bot。** Claude Code、Codex 等 CLI 可以作为 bot，复用其登录和模型；每个 DS Session 对应一个 CLI session，系统提示作为追加指令，团队工具经 loopback MCP 回到宿主执行并走审批。

DS-Bot 不适合直接搬的部分：Electron/DSH 桌面应用、3.2 MB 客户端产物、云电脑、浏览器/电脑操作、模型供应商与密钥管理、群聊 UI、移动端、MCP 市场。它用几个月和大量产品代码解决的是“桌面 Bot 产品”的问题，不是“项目内可审计自动化”的问题。

### 2.2 OpenMausBot

OpenMausBot 是本地优先的桌面 Bot 应用：一个 `127.0.0.1` 上的 harness server 拥有所有 agent 进程，驱动 Claude、Codex、Grok 等 CLI，把不同提供方的事件归一化；桌面端只发 HTTP 命令、消费一条 SSE。值得学的是：

- **Harness 与 UI 分离。** 所有 agent 进程由 harness 拥有，驱动注册表把不同 CLI 的原生协议归一化；未知驱动降级为不可用，不拖垮整个 fleet。
- **权限代理。** CLI 的权限请求进入 broker，统一展示和批准。
- **触发器有“跳过重叠”语义。** 间隔计划与起始时间对齐；上一轮还在跑时跳过本次，不堆积无界队列；可选运行上限，但默认不强加超时。
- **控制面故意收窄。** 对外 MCP 只暴露查看、创建/配置 bot、派活、等待、切模型、打断；不暴露批准、删除、凭据、电脑生命周期。
- **可观测事件流。** 每个 thread 的事件以 NDJSON 记录，UI 从一个 reducer 折叠状态。

不适合直接搬的部分：桌面 UI、云电脑、Composio 应用连接、语音、手机配对、多 provider 密钥管理、自托管公网入口。

### 2.3 Hermes Bot Mode

Hermes Bot Mode 是 Hermes Desktop 的插件，后来并入主仓。它的结构最简单，也最能说明“Bot 不必常驻模型会话”：

- **Bot 就是一个 profile。** 独立配置、记忆、技能、凭据和聊天历史都在 `~/.hermes/profiles/<name>/` 下；插件只是这个原语的 UI。
- **Routines 就是 cron。** 每个 bot 的定时任务就是带命名空间的 Hermes cron job，运行结果落回该 bot 的聊天历史；没有额外守护进程。
- **Bot 间消息就是 CLI handoff。** 发消息实际执行 `hermes -p <bot> chat --in ~ -c "Bot Chat" -Q -q "..."`；接收方下次运行时在收件箱看到。
- **群聊有硬上限。** 最多 2–6 个 bot，最多三轮串行，每轮最多 10 条消息，全员沉默即结束；`@user` 升级给人。

它证明了一个重要结论：**长期身份可以由 profile + cron + handoff 组成，不需要一个永远在线的模型进程。** 代价是实时性和“打断正在说话的 bot”能力较弱；这正好对应我们默认的自动化 bot 与可选的交互式 bot。

### 2.4 三个实现的共同模式

1. **长期身份，短期认知。** 三者都把 bot 的身份、配置、记忆、历史和运行状态持久化；模型会话可以压缩、换段、后台化或下次再开。
2. **单一写入口。** 消息、任务、状态都有唯一账本或状态文件，其他视图从它派生，避免多份真相。
3. **批准代理。** 外部动作、权限提升、危险工具调用都经过一个 broker 或 approval 卡，而不是让模型直接决定。
4. **有界控制面。** 对外暴露的能力是收窄的；删除、批准、凭据、资源生命周期通常不暴露给普通调用方。
5. **触发器去重与跳过。** 定时任务不能因为上一轮没跑完就无限堆积；重叠运行要跳过或排队，并留下记录。
6. **失败显式化。** 认证失效、部分失败、凭据错误与“没有新内容”必须区分；不能让异常看起来像空结果。
7. **证据门禁。** Goal/阶段完成要附证据，不能只凭 agent 自述。
8. **状态不塞进系统提示。** DS-Bot 明确把 Bot 状态放在工具结果或每轮提示里，避免破坏 prompt 缓存。

### 2.5 本仓不学的部分

- 不做桌面 UI、移动端、云电脑、浏览器/电脑操作、语音、头像、群聊房间。
- 不做多 provider 模型目录、API key 管理、Composio/MCP 市场、动态安装第三方能力。
- 不做“Bot 自治团队”叙事；本仓的协作单位仍是项目、协调者、任务卡、写范围和 Attempt。
- 不把外部 IM 当成控制面；第一版的外部 IM 只是未来的输出适配器，不是审批入口。

## 3. 目标模型

### 3.1 七个对象

| 对象 | 含义 | 权威记录 | 生命周期 |
| --- | --- | --- | --- |
| Bot Definition | 用户希望长期存在的自动化定义：目标、适配器、运行时、权限、触发器、输出 | 项目本地定义文件 + 批准快照 | 可多次修订；修订产生新 revision |
| Bot Revision | 某个不可变版本的定义内容 | `approved/<bot_id>/<revision>.json` 及其摘要 | 批准后不可改；只能新建 revision |
| Bot Runtime | 当前运行资源：进程、锁、pane、租约、健康状态 | `registry.json`、`lifecycle.jsonl`、`health.json` | 可启停；崩溃后按批准快照恢复 |
| Agent Session | 一次模型会话；可以是按批临时会话，也可以是可选的常驻会话 | 运行时记录 `session_ref`；不成为授权事实源 | 默认短命；可选恢复 |
| Attempt | 一次具体的分诊、总结、修复、验证或处置 | 现有 `task_id`/`attempt_id`、dispatch ledger、评估记录 | 有明确输入、输出和验收 |
| Event | 适配器从外部来源规范化出的事实 | `state/events.jsonl` 与批次文件 | 只追加；按保留期清理 |
| Approval | 用户对某个 revision、权限和启动影响的明确同意 | `approved/` 快照 + `lifecycle.jsonl` | 可过期、可失效；不能转让 |

### 3.2 两条不变量

**不变量一：Bot 不是新用户，也不是权限来源。**
Bot 没有独立登录、独立 API key 或独立租户。它借用项目运行环境的 CLI 登录和操作系统权限；框架提供的只是逻辑边界、路径校验、命令 allowlist、审批记录和审计。真正的隔离仍要靠 OS 用户、容器、CI 权限和人工审批。文档和 UI 不能把“私有 bot”说成安全沙箱。

**不变量二：任何长期状态都不能替代当次授权。**
Bot 的定义、记忆、历史批准和“上次也是这么做的”都不能成为本次外部动作的授权。L2 每个动作需要独立任务卡和写范围；L3 每次需要明确批准；L4 永不自动执行。

### 3.3 “Agent 不常驻”与“Bot 常驻会话”的张力

用户最初的直觉是“bot 应该常驻一个会话”。这个直觉对交互式助教、值班问答、需要连续探索的 bot 成立；对定时采集、监控、分诊、报告类 bot 不成立。把两者混为一谈，会导致：

- 每个 bot 都占一个模型会话，成本随 bot 数线性增长；
- 会话长期不关，上下文变脏，压缩/换段本身成为故障源；
- 权限长期热着，任何注入或误操作的影响窗口变大；
- 崩溃恢复、审计和“这一轮到底做了什么”更难说清；
- 多项目安装时，会话目录、模型配置和凭据生命周期互相纠缠。

因此本设计把“常驻”拆成三层：

| 层 | 是否常驻 | 说明 |
| --- | --- | --- |
| Bot 身份与状态 | 是 | 定义、批准、游标、事件、记忆、报告长期存在 |
| 触发器与运行时进程 | 可以常驻，也可以由 cron/launchd 唤醒 | 进程很轻，只做确定性工作；不需要模型 |
| 模型会话 | 默认按批创建；可选恢复；交互式才常驻 | 默认 `agent_batch`；`agent_resident` 是后续独立运行时 |

DS-Bot 自己也不是“一个 bot 一个永不换的会话”：它有上下文管家、换段、后台任务和 CLI handoff。Hermes 更直接：profile + cron + CLI handoff。**我们采用同一结论：长期身份，短期认知。**

如果未来要做交互式 bot（用户在聊天里连续追问、需要秒级响应），新增 `runtime.kind = agent_resident`：由 Herdr pane 或 supervisor 持有一个 agent 会话，空闲超时后换段或退出；它仍受同一套定义、批准、权限、健康检查和回收规则约束，不是另一套特权系统。

## 4. 文档组织

采用两份文档：

- 本文：通用 bot 模型、自动创建、Schema、生命周期、运行时、权限、场景、部署、运维、映射、差距与阶段。
- `docs/feishu-sync-design.md`：第一个场景实例，保留 `project_key`、`user_key`、拉取范围、游标、评论分诊和人读输出细节。

通用层只抽象能力，不复制飞书的评论、分页和字段语义。飞书文档里的只读、批量分诊、同源机器结果与人读报告、异常不能伪装成空结果等结论继续有效。

如果运维手册继续变长，再拆 `docs/bot-framework-operations.md`；当前不提前拆，避免三份文档互相漂移。

## 5. 总体架构

### 5.1 分层

```text
用户 / 协调者 Agent
        │  目标、批准、异常处置
        ▼
project-collaboration Skill  ── references/bot-framework.md（按需）
        │
        ▼
bot.py 控制面（draft / validate / approve / start / stop / status）
        │  只读写项目本地 .herdr/bots
        ▼
bot_runtime.py（锁、触发器、事件、批次、恢复、健康）
        │
        ├── 确定性适配器（feishu_sync 等）── 外部只读来源
        ├── Agent 调用契约（claude / pi / codex）── 分诊、总结、需要语义的处置
        └── 输出（面板、项目本地 JSONL/Markdown、未来外部目标）
```

### 5.2 落位：已创建与仍规划

已创建：

```text
scripts/
├── bot.py                         # validate/explain/approve/run --once/status/stop/resume
├── bot_schema.py                  # 定义、策略、路径与摘要校验
├── bot_runtime.py                 # 锁、配额、轮询、状态、健康、生命周期
└── bot_adapters/
    ├── __init__.py
    ├── base.py                    # 适配器接口与错误分类
    ├── fake.py                    # 无网络测试适配器
    └── feishu_sync.py             # 只读飞书适配器

skills/project-collaboration/
├── scripts/bot.sh                 # 跨三端薄包装器
└── references/bot-framework.md    # 面向用户和协调者的操作说明

templates/
├── bot-definition.json            # 全部使用占位符
└── bot-policy.json                # 项目策略模板

tests/
├── test_bot_schema.py
├── test_bot_approval.py
├── test_bot_runtime.py
├── test_bot_security.py
├── test_bot_wrapper.py
├── test_feishu_adapter.py
└── fixtures/bot/                  # 合成事件、批准、错误与恢复场景
```

仍规划、尚未创建：`feishu-sync.sh`、`references/feishu-sync.md`、场景模板 `templates/bots/*`、常驻运行时、Agent 调用契约和分诊批次。

仍只有一个 `project-collaboration` Skill。需要扩展的是包装器、参考文档和无模型测试，不是为每个 bot 新增一份常驻流程正文。

### 5.3 数据流

```text
触发器
  → 适配器轮询/接收
  → 规范化 Event
  → 追加 events.jsonl（幂等键）
  → 完整成功后推进游标
  → 达到批次阈值
  → 生成 context-brief + 调 Agent（可选）
  → 校验 Agent 输出
  → 写机器结论 + 人读报告
  → 面板摘要 / needs_user
  → 健康与审计记录
```

任何一步失败都要留下明确的错误分类；不能把失败写成“本轮无新增”。

## 6. 自动创建：入口与执行

### 6.1 用户入口

用户不新学一个命令。入口仍是现有协作入口：

- Claude Code：`/agent-collab:project-collaboration`
- Pi：`/skill:project-collaboration`
- Codex：`$agent-collab:project-collaboration`

用户说：“在这个项目里创建一个每 10 分钟检查飞书评论、把需要我决策的事项整理出来的 bot。”协调者加载 `references/bot-framework.md`，按固定步骤执行。不新增拥有最高权限的 Chief，也不新增第二个 Skill。

### 6.2 协调者拿到需求后的执行步骤

1. **识别项目与策略。** 确认项目根目录、`.herdr/bot-policy.json` 是否存在、当前用户、可用运行时、已有 bot 数量。
2. **对齐目标。** 确认 `goal_ref`、目标、非目标、成功标准、谁批准、数据敏感级别。缺目标不进入草稿。
3. **选择场景。** 已有适配器（飞书只读、CI 只读等）或明确“暂不支持”；不允许在创建步骤里现场安装第三方能力。
4. **选择运行时。** 默认 `process`（确定性采集）或 `agent_batch`（按批调用 Agent）；`agent_resident` 只在用户明确要求交互式且项目策略允许时选择。
5. **确定权限。** 自治上限、读取范围、写入范围、网络、外部动作、命令 allowlist、记忆保留期。默认拒绝。
6. **生成草稿。** 写 `.herdr/bots/drafts/<BOT_ID>.json`，同时生成人类可读的“将要发生什么”说明：触发器、每次运行做什么、会读什么、会写什么、什么时候找用户、失败怎么办。
7. **离线校验。** Schema、路径越界、适配器注册、策略允许、触发器下限、并发、输出类型、批准字段；不访问网络、不启动进程、不写批准快照。
8. **请求批准。** 展示定义摘要、权限、触发器、输出、资源影响、批准有效期和“不批准会怎样”。用户明确同意后才写批准快照。
9. **记录批准。** 生成 `approval_id`、定义/项目策略/适配器配置的 SHA-256、批准人、时间、范围、过期时间、来源消息引用；写 `approved/` 和 `lifecycle.jsonl`。
10. **按授权启动。** 只有用户明确要求启动，或批准记录中包含启动授权时，才创建运行时资源；首次启动前再校验策略、摘要、锁和适配器配置。
11. **验证。** 跑一次 `status` 和 `once`；确认没有真实外部写、游标没有错误推进、面板只出现摘要、日志有审计记录。
12. **交接。** 写项目 `.herdr/PROGRESS.md` 的 bot 段落，给用户恢复入口、停止入口和异常处理方式。

### 6.3 命令行入口

阶段 1 已实现 `validate`、`explain`、`approve`、`run --once`、`status`、`stop`、`resume`；完整形态的 `draft`、`start`、`pause`、`retire` 尚未实现。

```bash
# 1. 离线校验；不访问网络
python3 <PACKAGE_ROOT>/scripts/bot.py validate \
  --definition .herdr/bots/<BOT_ID>/definition.json

# 2. 展示摘要
python3 <PACKAGE_ROOT>/scripts/bot.py explain \
  --definition .herdr/bots/<BOT_ID>/definition.json

# 3. 用户确认后写批准快照
python3 <PACKAGE_ROOT>/scripts/bot.py approve \
  --definition .herdr/bots/<BOT_ID>/definition.json \
  --approval-id <APPROVAL_ID> \
  --approver-ref <LOCAL_USER_REF> \
  --source-ref <MESSAGE_REF>

# 4. 阶段 1 只允许单次运行；启动、暂停、回收尚未实现
python3 <PACKAGE_ROOT>/scripts/bot.py run --bot-id <BOT_ID> --once
python3 <PACKAGE_ROOT>/scripts/bot.py status --bot-id <BOT_ID>
python3 <PACKAGE_ROOT>/scripts/bot.py stop --bot-id <BOT_ID>
python3 <PACKAGE_ROOT>/scripts/bot.py resume --bot-id <BOT_ID>
```

三端统一通过 `skills/project-collaboration/scripts/bot.sh` 调用同一个 Python CLI；包装器只做路径定位和参数透传，不复制逻辑。CLI 退出码已冻结：`0` 成功，`2` 配置/校验错误，`3` 未批准/批准失效/状态或策略门禁拒绝，`4` 锁冲突，`5` 运行时错误，`6` 适配器认证失败。

### 6.4 批准记录

批准不是一句“可以”，而是可核对的记录：

```json
{
  "schema_version": 1,
  "approval": {
    "approval_id": "<APPROVAL_ID>",
    "bot_id": "<BOT_ID>",
    "revision": 1,
    "project_root": "<ABSOLUTE_PROJECT_ROOT>",
    "run_user": "<LOCAL_RUN_USER>",
    "approver_ref": "<LOCAL_USER_REF>",
    "approved_at": "<ISO8601>",
    "expires_at": "<ISO8601_OR_NULL>",
    "source_ref": "<MESSAGE_OR_TASK_REF>",
    "scope": {
      "autonomy_ceiling": "L1",
      "triggers": ["interval"],
      "outputs": ["herdr_panel", "local_file"],
      "external_actions": "deny"
    }
  },
  "definition_sha256": "<SHA256>",
  "policy_sha256": "<SHA256>",
  "adapter_config_sha256": "<SHA256>",
  "binding": {
    "adapter": "feishu",
    "project_key": "<PROJECT_KEY>",
    "user_key": "<USER_KEY>"
  },
  "definition": {}
}
```

规则：

- 摘要按规范化 JSON 计算，排除批准时间等易变字段；任何会影响行为的字段变化都产生新摘要。
- 项目策略或适配器配置变化也会使批准失效，必须重新批准；阶段 1 在运行前校验三份 SHA-256。
- 批准绑定 `bot_id`、`revision`、摘要、项目根、运行用户和范围；阶段 2 还绑定适配器声明的 `project_key + user_key`。运行时从当前配置和环境变量重算绑定，与快照不一致退出 3；换项目、换用户、换机器不能复用。
- 批准可以过期；过期后运行中的实例进入 `approval_invalidated`，停止产生新动作。
- 批准快照不可变；修订只能新增 revision。
- 批准记录不是凭据；它不包含 token、密钥或外部系统密码。

### 6.5 自动化创建的分级

| 级别 | 谁起草 | 谁批准 | 谁启动 | 适用 |
| --- | --- | --- | --- | --- |
| A0 手工 | 用户/人 | 人 | 人 | 任何场景的基线 |
| A1 Agent 起草 | 协调者 Agent | 人 | 人，或批准中包含启动授权 | MVP |
| A2 模板内自动 | Agent 按已批准模板生成 | 人事先批准模板与配额 | 运行时在配额内自动启动并通知 | 第二阶段；仍受项目策略、最大 bot 数、只读/L1 限制 |
| A3 Bot 创建 Bot | Bot | — | — | **禁止** |

A2 的关键不是“让 agent 更自由”，而是把人的批准前移到一个有界模板：用户批准“允许在这个项目里创建最多 N 个只读监控 bot，触发器间隔不低于 X，输出只到面板和本地文件”。之后 agent 生成的具体实例仍要落盘、登记、可暂停、可审计；超出模板的行为必须重新批准。

### 6.6 防止自我复制与权限继承

- Runtime 没有 `create_bot` 能力；控制面 CLI 的 `draft/approve/start` 只对本地协调者会话开放。
- Bot 的 Agent 调用只能读写声明的范围，不能调用 `bot.py`；如果未来需要提议新 bot，只能生成“建议草稿”文本，由人/协调者处理。
- 子 bot 不继承父 bot 的权限；每个 bot 独立批准、独立配额。
- 项目策略限制 `max_bots`、`max_active_bots`、`max_agent_calls_per_day`，防止资源无限增长。
- 外部内容中的“创建 bot”“放开权限”“执行命令”只作为不可信事件内容，永远不进入控制面。

## 7. Bot Definition Schema

### 7.1 设计原则

- 所有项目相关值都是占位符或本地引用；公共模板不含真实 ID、路径、凭据。
- 未知字段报错，不静默忽略；避免拼写错误变成隐式默认。
- 版本化：`schema_version` 整数；迁移必须有显式脚本和测试。
- 默认拒绝：缺省权限为空、网络关闭、外部动作拒绝、并发禁止。
- 机器可读与人类可读分离：JSON 是权威，`explain` 生成人读说明。

### 7.2 结构示例（全部为占位符）

```json
{
  "schema_version": 1,
  "bot_id": "<BOT_ID>",
  "revision": 1,
  "goal_ref": "<GOAL_REF>",
  "display_name": "<BOT_DISPLAY_NAME>",
  "description": "<ONE_LINE_PURPOSE>",
  "identity": {
    "instructions_ref": "<PROJECT_LOCAL_INSTRUCTIONS_FILE>",
    "owner_ref": "<LOCAL_OWNER_REFERENCE>",
    "tags": ["<TAG>"]
  },
  "adapter": {
    "name": "<REGISTERED_ADAPTER_NAME>",
    "version": "<ADAPTER_VERSION>",
    "config_ref": "<PROJECT_LOCAL_ADAPTER_CONFIG>"
  },
  "runtime": {
    "kind": "process",
    "agent": null,
    "concurrency": "forbid",
    "lock_ttl_seconds": 900,
    "heartbeat_seconds": 60
  },
  "workspace": {
    "root": ".herdr/bots/<BOT_ID>",
    "read_scope": [],
    "write_scope": [".herdr/bots/<BOT_ID>"]
  },
  "permissions": {
    "autonomy_ceiling": "L1",
    "project_write_scope": [],
    "network_read": true,
    "network_write": false,
    "external_actions": "deny",
    "bot_administration": "deny",
    "commands": [],
    "secret_refs": []
  },
  "triggers": [
    {
      "type": "interval",
      "interval_seconds": 600,
      "jitter_seconds": 30,
      "active_hours": null,
      "skip_if_running": true
    }
  ],
  "triage": {
    "mode": "suggest_only",
    "batch_interval_seconds": 1800,
    "max_batch_events": 20,
    "max_context_events": 40
  },
  "outputs": [
    {
      "type": "herdr_panel",
      "format": "summary"
    },
    {
      "type": "local_file",
      "path": ".herdr/bots/<BOT_ID>/reports",
      "format": "markdown_and_jsonl"
    }
  ],
  "memory": {
    "mode": "private",
    "path": ".herdr/bots/<BOT_ID>/memory",
    "retention_days": 30,
    "max_bytes": 1048576
  },
  "health": {
    "stale_after_seconds": 1800,
    "max_consecutive_failures": 3
  },
  "escalation": {
    "needs_user_route": "herdr_panel",
    "quiet_hours": null,
    "max_items_per_group": 5
  }
}
```

### 7.3 运行时类型

| `runtime.kind` | 是否调用模型 | 典型用途 | MVP |
| --- | --- | --- | --- |
| `process` | 否 | 采集、去重、游标、健康、文件输出 | 是 |
| `agent_batch` | 按批 | 分诊、总结、生成人读报告、需要语义的只读判断 | 是 |
| `agent_resident` | 长期会话 | 交互式问答、连续探索、需要快速追问 | 否，后续独立设计 |

`agent_batch` 的 `agent` 子对象示例：

```json
{
  "kind": "claude",
  "model_profile_ref": "<LOCAL_MODEL_PROFILE>",
  "session_mode": "ephemeral",
  "timeout_seconds": 300,
  "max_agent_calls_per_day": 24,
  "output_schema_ref": "<LOCAL_OUTPUT_SCHEMA>",
  "permission_profile": "read_only"
}
```

`kind` 只能是项目策略允许的 `claude`、`pi`、`codex` 之一；`model_profile_ref` 是本地命名引用，不是 API key。`session_mode` 默认 `ephemeral`；`resume` 只用于同一 bot 的连续批次，且必须保存 `session_ref` 和恢复检查点。

### 7.4 关键字段约束

| 字段 | 约束 |
| --- | --- |
| `schema_version` | 当前只能为 `1`；未知键、重复键、错误类型报错 |
| `bot_id` | 项目内唯一、稳定、路径安全的 slug；创建后不改；显示名可改 |
| `revision` | 每次行为变化递增；运行记录保存 revision 和摘要 |
| `goal_ref` | 必填；没有目标来源不能批准或启动 |
| `adapter` | 只能引用项目策略允许的已注册适配器；真实配置在本地 `config_ref` |
| `runtime.kind` | MVP 只允许 `process` 和 `agent_batch`；`agent_resident` 未实现 |
| `workspace.root` | 项目内相对路径；禁止绝对路径、`..`、symlink 逃逸 |
| `read_scope` / `write_scope` | 默认空或仅自身目录；项目代码读取需显式声明 |
| `permissions.autonomy_ceiling` | 最高可达级别，不等于已批准全部动作 |
| `commands` | 命名命令 allowlist；不提供任意 shell |
| `triggers` | MVP 支持 `manual`、`interval`；间隔受项目策略下限约束 |
| `triage.mode` | MVP 只允许 `suggest_only`；`read_only` 由适配器和权限共同保证 |
| `memory` | 私有记忆不是目标或授权事实源；共享需单独审查 |
| `outputs` | MVP 只支持面板和项目本地文件；外部消息属于 L3 |
| `health` | 心跳、陈旧阈值、连续失败阈值必须可观测 |

### 7.5 必填项与默认值

| 配置 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `bot_id` | 是 | — | 稳定 slug；不能从显示名自动猜测 |
| `revision` | 是 | `1` | 新定义从 1 开始；不能手工回退 |
| `goal_ref` | 是 | — | 没有目标不能批准 |
| `adapter.name` / `config_ref` | 是 | — | 适配器必须已注册，配置必须存在 |
| `runtime.kind` | 是 | — | MVP 只允许 `process` / `agent_batch` |
| `workspace.root` | 是 | `.herdr/bots/<bot_id>` | 必须在项目内；不能含 `..` |
| `permissions.autonomy_ceiling` | 是 | `L0` | 不能高于项目策略 |
| `triggers` | 是 | — | 至少一个；`manual` 也必须显式写 |
| `outputs` | 是 | — | 至少一个；外部输出默认不允许 |
| `runtime.concurrency` | 否 | `forbid` | 不允许同一 bot 并行 |
| `runtime.lock_ttl_seconds` | 否 | `900` | 必须大于心跳间隔 |
| `runtime.heartbeat_seconds` | 否 | `60` | 用于失联判断 |
| `workspace.read_scope` | 否 | `[]` | 默认不读项目代码 |
| `workspace.write_scope` | 否 | `[自身 root]` | 默认只写自己 |
| `permissions.network_read` | 否 | `false` | 只读适配器需显式打开 |
| `permissions.network_write` | 否 | `false` | MVP 恒为 false |
| `permissions.external_actions` | 否 | `deny` | L3 不走定义默认值 |
| `triage.mode` | 否 | `suggest_only` | MVP 唯一允许值 |
| `triage.batch_interval_seconds` | 否 | `1800` | 空轮询不生成批次 |
| `triage.max_batch_events` | 否 | `20` | 防止单批过大 |
| `memory.mode` | 否 | `none` | 需要时显式设为 `private` |
| `memory.retention_days` | 否 | `30` | 仅私有记忆生效 |
| `health.stale_after_seconds` | 否 | `1800` | 超过即 `degraded` |
| `escalation.max_items_per_group` | 否 | `5` | 面板摘要上限 |

默认值只能收紧权限，不能扩大权限。任何“为了少写几行配置”而把默认值改成可写、可外发或更高自治的做法都不接受。

### 7.6 缺失或非法配置

- 缺必填项：`validate` 失败，退出码 `2`，输出缺少的字段路径，例如 `permissions.autonomy_ceiling`，不启动、不访问网络。
- 缺项目策略：允许 `draft` 和 `validate`（用于准备），但 `approve`/`start` 失败，退出码 `3`，明确“缺少 `.herdr/bot-policy.json`”。
- 缺适配器配置或配置引用不存在：`validate` 失败，输出 `adapter.config_ref` 和期望路径，不回退到示例配置。
- 非法值（未知字段、类型错误、枚举越界、路径越界、间隔低于下限、自治高于策略）：`validate` 失败，列出全部错误，不只报第一个。
- 配置版本不支持：`schema_error`，提示支持的版本和迁移命令；不猜测字段。
- 任何情况下都不得用内置的他人项目 ID、示例人员 ID、默认项目路径或公共仓示例值兜底。缺失就是缺失，必须由使用方项目补齐。
- 运行中发现配置漂移（文件被改、引用失效、策略变化）：进入 `degraded` 或 `approval_invalidated`，停止新动作，保留现场，等待人处理。

### 7.7 项目策略

建议新增项目本地 `.herdr/bot-policy.json`：

```json
{
  "schema_version": 1,
  "allowed_adapters": ["feishu-sync", "ci-readonly"],
  "allowed_runtimes": ["process", "agent_batch"],
  "allowed_agent_kinds": ["claude", "pi", "codex"],
  "allowed_outputs": ["herdr_panel", "local_file"],
  "max_bots": 5,
  "max_active_bots": 2,
  "min_interval_seconds": 60,
  "max_autonomy": "L1",
  "require_approval_for_start": true,
  "allow_network_read": true,
  "allow_network_write": false,
  "data_classification": "internal",
  "state_retention_default_days": 30
}
```

策略是项目所有者写的，不是 bot 写的。没有策略文件时可以生成草稿，但不能批准启动；内置默认值不能变成对任意项目和任意能力的隐式许可。

### 7.8 配置解析优先级

从高到低：

1. CLI 显式参数（只用于路径和一次性诊断，不用于覆盖安全边界）；
2. 项目本地 `.herdr/bot-policy.json` 与 `.herdr/bots/<bot_id>/adapter-config.json`；
3. 项目环境文件或环境变量中的非秘密引用（如 `BOT_*` 前缀）；
4. 公共包内置默认值（只允许安全默认：拒绝、空范围、短保留期）。

秘密不进入配置树：只允许 `secret_ref` 指向已有 CLI 登录、系统钥匙串或项目本地未提交文件；运行时解析引用，不把值写入日志、定义、记忆或报告。

## 8. 生命周期

### 8.1 状态

`draft` → `validated` → `approved` → `starting` → `running` → `paused` / `degraded` / `stopping` → `stopped` → `retired`；另有 `rejected`、`approval_invalidated`、`failed`。

`degraded` 表示进程还在但适配器认证失败、连续错误或队列积压；它不等于停止，也不等于正常。

### 8.2 转换与门禁

| 动作 | 前置条件 | 记录 | 谁可以做 |
| --- | --- | --- | --- |
| 创建草稿 | 有用户目标和项目根 | `drafted` | 协调者 Agent |
| 校验 | Schema、策略、路径、适配器、触发器都通过 | `validated` 或错误 | CLI，无模型 |
| 批准 | 用户明确同意；摘要、范围、有效期确定 | `approved` + 快照 | 人 |
| 启动 | 批准有效；策略允许；锁空闲；配置可解析 | `started` + 资源登记 | 协调者执行，用户授权 |
| 暂停 | 不再产生新触发；在飞批次到安全点 | `paused` | 用户/协调者（安全原因） |
| 恢复 | 同一批准快照；游标和状态完整 | `resumed` | 用户/协调者 |
| 停止 | 不接收新触发；回收本次资源 | `stopped` | 用户/协调者 |
| 修订 | 产生新 revision 和差异说明 | `revision_created` | 协调者起草，人批准 |
| 回收 | 已停止、无在飞 Attempt、资源释放 | `retired` | 用户/协调者 |
| 批准失效 | 摘要/策略/项目/用户变化 | `approval_invalidated`，停止新动作 | Runtime 自动 |

`blocked`、`failed`、`exited` 是观测状态，不等于用户已经同意停止、重试或回收。失联先取证，不能通过不断创建替代 bot 来恢复。

### 8.3 修订与批准失效

- 目标、指令、适配器、触发器、权限、能力、运行时、模型、输出、记忆共享范围中任何一项变化，都产生新 revision。
- 旧批准立即失效；运行中的实例进入 `approval_invalidated`，停止产生新外部动作，保留状态等待人处理。
- 不允许“热改”批准快照；不允许用旧摘要启动新配置。
- 迁移 schema 时先停止、备份、迁移、重新校验、重新批准；不能边跑边迁移。

## 9. Runtime 设计

### 9.1 主循环

```text
start:
  acquire project-level lock for bot_id
  load approved snapshot
  verify policy + digest + adapter config
  write lifecycle started
  loop:
    now = clock
    if not active_hours(now): sleep; continue
    if paused/stop requested: checkpoint; break
    if lock lease expired: re-acquire or exit degraded
    result = adapter.poll(cursor)
    if auth_expired/permission_denied: mark degraded; do not advance cursor; sleep backoff
    if partial failure: append error event; don't advance affected item cursor
    append normalized events with idempotency keys
    advance cursor only after full success
    if batch threshold reached: create batch
    if batch and agent runtime: invoke agent with context brief
    validate agent output; write results + report
    update health/heartbeat
    sleep until next tick
  release lock; write lifecycle stopped
```

### 9.2 锁与租约

- 每个 bot 一个项目级锁：文件锁或目录锁；包含 `bot_id`、PID、主机、启动时间、租约到期。
- 同一 bot 默认 `concurrency=forbid`；上一轮未结束时跳过本次，记录 `skipped_overlap`，不排队堆积。
- 锁过期不等于可以抢占：先检查进程是否还活着、pane 是否存在、最近心跳；确认失联后才接管，并记录 `lock_recovered`。
- 多实例/多机器部署时，锁必须落在共享存储或单机；第一版只支持单机。

### 9.3 游标与幂等

- 每个来源每个工作项一个游标；不要用一个全局游标掩盖部分失败。
- Event 的幂等键由适配器定义：例如评论用 `source + work_item_id + comment_id`，任务变化用 `source + work_item_id + field + version`。
- 同一幂等键重复出现时，只更新必要的快照，不重复生成待分诊事件。
- 游标只在“该项完整拉取成功”后推进；认证失败、解析失败、部分页失败都不推进。
- 删除/归档来源项不删除历史事件；用保留期和 tombstone 处理。

### 9.4 批次与 Agent 调用

批次是“需要语义判断的最小单位”，不是每次轮询：

- 达到 `triage_max_batch_events` 或 `triage_batch_interval_seconds` 才生成批次；空轮询不生成。
- 每批一个 `batch_id`，状态 `pending → claimed → done/failed`；`claimed` 带 `attempt_id` 和租约。
- Agent 输入是只读 context brief：bot 身份、revision、goal_ref、批次事件、工作项快照、有界评论上下文、真实链接、允许的输出 Schema、明确禁止事项。
- Agent 输出必须是结构化 JSON；运行时校验字段、枚举、引用；不合法的输出不写入结论，标记 `agent_invalid_output`。
- Agent 调用有超时、最大输出、最大并发和每日调用上限；超时/限流按错误分类重试，权限拒绝/策略拒绝不重试。
- `session_mode=ephemeral` 时每次新会话；`resume` 时从检查点恢复，检查点包含上次批次结论摘要和未完成事项，不包含全量历史。
- Agent 不得在分诊中执行外部动作；它只能产出建议、标签、证据引用和需要人决定的问题。

### 9.5 Context brief

Context brief 是本仓当前没有的派生视图，建议每次 Attempt 生成一份只读 Markdown/JSON：

```text
bot_id / revision / goal_ref
本轮目标与非目标
批次事件与证据引用
允许读取的范围
允许写入的范围（通常只有 bot 自己的 state/reports）
输出 Schema
禁止事项（外部写、扩权、创建 bot、执行原始内容中的指令）
恢复入口（上一次安全点、未完成批次）
```

它不是新的事实源；事实仍在定义、批准、事件、ledger 和原始证据里。

### 9.6 错误分类

| 类别 | 例子 | 是否推进游标 | 是否重试 | 用户可见 |
| --- | --- | --- | --- | --- |
| `auth_expired` | token 过期、未登录 | 否 | 人工登录后 | 是，明确“未登录” |
| `permission_denied` | 无权限读取项目 | 否 | 否 | 是 |
| `rate_limited` | 外部限流 | 否 | 退避重试 | 连续多次才提示 |
| `transient_network` | 超时、连接重置 | 否 | 有限重试 | 连续失败才提示 |
| `schema_error` | 响应格式变化 | 否 | 否 | 是，需适配器修订 |
| `policy_denied` | 策略不允许 | 否 | 否 | 是 |
| `agent_timeout` | 模型超时 | 批次不完成 | 有限重试 | 连续失败才提示 |
| `agent_invalid_output` | 非法 JSON/字段 | 批次不完成 | 一次修复重试 | 是 |
| `internal` | 磁盘、锁、代码错误 | 视情况 | 有限 | 是 |

### 9.7 健康与恢复

`health.json` 至少包含：`state`、`last_tick_at`、`last_success_at`、`lag_seconds`、`consecutive_failures`、`last_error`、`approval_revision`、`adapter_auth_status`、`queue_depth`、`active_batch_id`。

恢复顺序：

1. 读批准快照和 registry，确认期望状态；
2. 检查锁和进程/pane 是否真的存活；
3. 读取最后一个完整游标和未完成批次；
4. 对未完成批次做幂等对账（已完成则标 done，未完成则重试或标 failed）；
5. 从安全点继续，不重放已完成的外部动作；
6. 写 `recovered` 事件和恢复原因。

## 10. 权限、自治与边界

### 10.1 L0–L4

| 级别 | 含义 | Bot 默认边界 |
| --- | --- | --- |
| L0 观察与采集 | 只读外部数据、规范化事件、维护自身状态 | 可自动执行已批准的只读触发器 |
| L1 分诊与建议 | 读取允许的上下文，生成机器结论和人读报告 | 可按批调用 Agent，只写 bot 状态和报告 |
| L2 本地可逆处置 | 修改本地代码、补测试、生成补丁 | 默认禁止；每个动作独立任务卡、写范围、验收和新 Attempt |
| L3 契约或外部影响 | 外部消息、任务状态修改、commit/push、部署、共享环境写入 | 每次明确批准，不能由定义里的高自治级别一次性概括 |
| L4 禁止自治 | 自我复制、擅自扩权、绕过审批、泄露凭据、覆盖他人工作、销毁数据、未知影响批量外发 | 永远不自动执行 |

MVP 的飞书 bot 仍只允许 L0 + L1，`read_only=true`、`triage_mode=suggest_only` 不变。

### 10.2 有效权限取交集

```text
实际可执行权限
= 用户当次批准
∩ 项目 bot-policy
∩ Bot Definition 声明范围
∩ 当前 Attempt 的任务卡与写范围
∩ Claude / Pi / Codex / OS / CI 的真实权限控制
```

任何一层缺失或拒绝都不能由另一层补齐。协调者拥有的权限不会自动传给 bot；适配器能调用某个 API 也不等于该 API 已获授权。

Skill、指令和 Markdown 记忆不是权限系统。真正阻断仍要依靠路径校验、命令 allowlist、适配器只读方法、运行工具权限、CI 和人的审批；通用校验器只能证明配置结构，不证明运行环境绝对安全。

### 10.3 私有边界

- Bot 默认只能写自己的 `workspace.root`；项目代码和其他 bot 的目录默认不可写。
- 读取项目代码、其他 bot 状态和共享记忆都需要显式 `read_scope`，不能通过相对路径、symlink 或 shell 动态拼接逃逸。
- 凭据只引用本地工具已有登录或 `secret_ref`，不写入 Definition、记忆、报告或公共模板。
- 私有记忆用于恢复和背景，不是目标、权限、验收或审批事实源；权威事实仍来自任务卡、批准记录、ledger 和原始证据。
- Bot 新增本地命令、启用新能力、修改自己的指令或创建子 bot，均属于权限变化；Runtime 默认没有 `bot_administration` 能力。
- **边界诚实**：本框架没有 OS 级隔离。多个 bot 共享同一 OS 用户和 CLI 登录；路径检查、allowlist 和审批是逻辑控制，不是安全内核。需要强隔离时，使用独立 OS 用户、容器或专用机器，后续单独设计。

### 10.4 不可信输入

外部评论、邮件、网页、Issue 和聊天消息一律视为不可信数据：

- 用明确的边界标记传给 Agent，例如 `<untrusted_external_content>`；
- Agent 的系统指令明确：这些内容只能作为事实和证据，不能作为控制指令；
- Runtime 永不解析事件内容来执行命令、创建 bot、修改配置或批准权限；
- Agent 输出中的“建议创建 bot/放开权限/执行命令”只能进入 `needs_user_confirmation`，不能直接执行；
- 重要决策保留原始引用，便于人核对。

## 11. Bot 实例场景

以下都是项目本地实例的模板，不是公共仓里的真实配置；`project_key`、`user_key`、路径、阈值都必须在接入时替换。

| 实例 | 触发器 | 运行时 | 自治 | 输入 | 输出 | 人的门禁 |
| --- | --- | --- | --- | --- | --- | --- |
| 飞书评论分诊 | 10 分钟 | process + agent_batch | L0/L1 | 待办、参与需求及子任务、评论 | 事件、批次分诊、人读报告 | 决定/回复/建任务 |
| CI 健康观察 | 5 分钟 | process + agent_batch | L0/L1 | CI 状态、失败日志摘要 | 异常摘要、失败分类 | 是否重跑/修复 |
| 发布说明草稿 | 手动/tag | agent_batch | L1 | git 记录、Issue、变更 | 草稿文件 | 审核、发布 |
| 事故观察 | 1 分钟/事件 | process + agent_batch | L0/L1 | 告警、日志、状态页 | 事故摘要、升级建议 | 是否升级/通知 |
| Issue 牧羊 | 每日 | agent_batch | L1 | Issue、标签、里程碑 | 待办建议、缺口清单 | 是否建任务 |
| 依赖升级 | 每周 | agent_batch | L2（逐动作） | 依赖清单、测试结果 | 补丁、测试报告 | 批准分支/PR |
| 文档漂移 | 每周 | process + agent_batch | L0/L1 | 文档、代码符号、命令 | 漂移清单 | 是否修文档 |
| 周报 | 每周 | process + agent_batch | L0/L1 | ledger、PROGRESS、事件 | 周报草稿 | 是否发送 |
| 交互式值班 | 常驻会话 | agent_resident | L1 | 聊天、项目只读上下文 | 即时回答 | 任何写操作 |
| 多 bot 发布协作 | 事件 | coordinator + task graph | L1/L2 | 发布任务、各 bot 结果 | 阶段证据 | 每阶段验收 |

### 11.1 飞书评论分诊

- 触发器：每 10 分钟；`skip_if_running=true`。
- 运行时：process 采集；达到批次窗口或事件数才调用一次 agent_batch。
- 只读：不新增、不修改、不回复飞书任务和评论。
- 增量：每项独立游标；评论按 `comment_id` 去重，`created_at` 只作辅助。
- 输出：`events.jsonl`、`triage/results.jsonl`、`reports/triage/<batch_id>.md`、面板摘要。
- 人的门禁：`needs_user_confirmation` 和 `local_action_candidate` 只作为标签，不自动执行。
- 失败：认证失效明确显示，不推进游标；部分失败保留该项旧游标。

### 11.2 CI 健康观察

- 输入：项目 CI 的只读状态接口或本地命令；不拉取 secrets。
- 输出：失败分类（flaky、真实失败、环境失败、未知）、最近一次成功、影响范围。
- 门禁：不自动重跑、不改 CI 配置、不推 commit；人类决定。
- 价值：把“CI 红了”变成带证据的摘要，而不是又一个告警。

### 11.3 发布说明草稿

- 触发：手动或 tag 事件；只读 git 和 Issue。
- 输出：`reports/release-notes-<version>.md`，引用 PR/Issue 编号。
- 门禁：不 commit、不 push、不创建 release；人审核后由人执行。
- 这是 L1 的上限；如果未来要自动开 PR，升级为 L2，需独立任务卡和写范围。

### 11.4 事故观察

- 输入：告警 webhook 或轮询；第一版只读。
- 输出：时间线、影响、已知/未知、建议下一步、需要谁决定。
- 门禁：不自动通知外部、不自动回滚、不自动改配置。
- 重点：异常和“没有新告警”必须区分；告警源失联是 `degraded`，不是安静。

### 11.5 依赖升级（L2 示例）

- 只有用户明确要求“可以改代码”才进入 L2。
- 每个升级是一个独立 Attempt：任务卡、写范围、验收命令、分支、测试证据、复审。
- Bot 不能自行合并、推送或发布；它最多准备补丁和证据。
- 失败回滚到干净分支，不覆盖他人工作。

### 11.6 交互式值班（后续）

- 只有明确需要“用户连续追问、秒级响应”时才用 `agent_resident`。
- 仍由 Herdr pane 或 supervisor 持有；空闲超时换段/退出；权限仍是只读。
- 不能因为常驻就获得写权限或外部动作权限。

## 12. 部署

### 12.1 项目本地目录

```text
.herdr/
├── bot-policy.json                 # 项目所有者维护
└── bots/
    ├── registry.json               # 当前 revision、期望状态、资源
    ├── lifecycle.jsonl             # 追加事实
    ├── drafts/<bot_id>.json
    ├── approved/<bot_id>/<revision>.json
    └── <bot_id>/
        ├── adapter-config.json     # 真实 ID/引用；按项目规则忽略提交
        ├── state/
        │   ├── cursor.json
        │   ├── events.jsonl
        │   ├── health.json
        │   └── triage/
        ├── reports/
        ├── memory/
        ├── logs/
        └── PROGRESS.md
```

提交策略：`bot-policy.json`、使用占位符/引用的 definition 和批准摘要可以提交；真实适配器配置、游标、事件、报告、记忆、日志默认 `.gitignore`。是否提交由项目所有者决定，但公共仓永不接收这些运行数据。

### 12.2 拓扑 A：本地前台（开发/验证）

```bash
python3 <PACKAGE_ROOT>/scripts/bot.py run --bot-id <BOT_ID> --once
```

- 适合接入验证、故障重放、CI 测试。
- 不创建 pane、不常驻；退出即结束。
- 必须使用项目本地状态目录，不写公共包目录。

### 12.3 拓扑 B：Herdr pane（推荐的项目内常驻）

1. 确认 `test "${HERDR_ENV:-}" = 1`；失败则停止，不探测其他会话。
2. 从批准快照启动 `bot.py run --bot-id <BOT_ID>`，在专用 pane 中运行。
3. pane 标题、cwd、bot_id、资源归属写入 registry；只回收本 bot 创建且已停止的 pane。
4. 停止顺序：`bot.py pause/stop` → 等待安全点 → 确认进程退出 → 关闭本次创建的 pane。
5. 乱码、OOM、崩溃：先停止输入，按 Herdr 事故流程只读取证；不盲目重发，不关闭非本次资源。

Herdr 是可选拓扑，不是硬依赖。没有 Herdr 时，同一 CLI 可由 cron、launchd 或 systemd 运行。

### 12.4 拓扑 C：cron / launchd / systemd（无头常驻）

- cron 每次调用 `bot.py run --once`，由锁保证不重叠；适合间隔不小于 1 分钟。
- launchd/systemd 运行 `bot.py run --foreground`，由 supervisor 重启；必须有退避和最大重启次数。
- 退出码和 `health.json` 是主要观测；不要只依赖进程存活。
- 认证失效时不要无限重试；进入 `degraded` 并等待人工。

### 12.5 拓扑 D：CI 定时任务

- 只适合无状态或可重建状态的只读检查；不要用 CI artifact 当长期游标。
- 需要持久状态时，状态必须落在受控存储，并明确并发锁；否则会重复处理。
- 第一版不推荐把有状态 bot 跑在 CI。

### 12.6 多项目接入最小步骤

1. 安装/更新 agent-collab 包，确认三端版本一致。
2. 在项目根创建 `.herdr/`，加入 `.gitignore` 的运行数据规则。
3. 复制 `templates/bot-policy.json`，填写允许的适配器、运行时、最大 bot 数、最小间隔、最高自治。
4. 复制场景模板到 `.herdr/bots/drafts/<bot_id>.json`，替换全部占位符。
5. 在本地写 `adapter-config.json`，只放项目 ID、用户 ID、路径引用；不写密钥。
6. 运行 `bot.py validate` 和 `explain`；确认没有网络访问、没有进程启动。
7. 用户批准；`bot.py approve` 写快照。
8. `bot.py start`（或 Herdr/cron 启动）；跑一次 `once`，检查 health、日志、报告和游标。
9. 把恢复入口、停止入口和异常处理写进项目 `.herdr/PROGRESS.md`。

### 12.7 升级与回滚

- 升级包版本不等于升级 bot；bot 使用已批准 revision，直到重新批准。
- 升级前停止运行时、备份状态、记录当前 revision 和摘要。
- Schema 迁移必须幂等、可回滚、有测试；不能原地改批准快照。
- 回滚使用旧 revision 的新批准（如果策略仍允许），不能恢复已失效的旧批准。

## 13. 运维边界

### 13.1 日常检查

- `bot.py status`：状态、revision、最后成功、滞后、连续失败、队列、当前批次。
- 日志：结构化 JSONL，按大小轮转；错误带分类和证据引用；不记录秘密和完整外部正文。
- 报告：按批次生成；空轮询静默；认证失效、部分失败必须显式。
- 审计：lifecycle、批准、修订、启动/停止、锁恢复、异常处置都可追溯。

### 13.2 指标

MVP 只需要：

- `events_ingested_total`
- `batches_created_total`
- `agent_invocations_total` / `agent_failures_total`
- `triage_latency_seconds`
- `needs_user_pending`
- `cursor_lag_seconds`
- `consecutive_failures`
- `disk_usage_bytes`

不以“进程活着”作为成功指标。健康要能回答：最后一次成功是什么时候、积压多少、哪些项失败、是否需要人。

### 13.3 告警

只对需要人处理的事提示：

- 认证失效/未登录；
- 批准失效或策略变化；
- 连续失败超过阈值；
- 积压超过阈值；
- 磁盘/状态目录异常；
- Agent 输出连续非法。

普通空轮询、单次网络抖动、已自动重试的限流不打扰用户。

### 13.4 事故手册

| 现象 | 先做什么 | 不要做什么 |
| --- | --- | --- |
| 认证失效 | 停止推进游标，显示“未登录”，等人工登录 | 当成零结果；无限重试 |
| 进程/pane 失联 | 只读取证：锁、PID、心跳、日志、pane 状态 | 盲目重发；关闭非本次资源 |
| 锁卡住 | 确认进程是否存活、租约是否过期；记录后接管 | 直接删锁文件 |
| 重复事件 | 用幂等键去重；检查游标是否被错误推进 | 手工改 events 删历史 |
| Agent 输出非法 | 保留原始输出，标 `agent_invalid_output`，一次修复重试 | 把非法输出写进结论 |
| 批准失效 | 停止新动作，保留状态，重新走批准 | 用旧摘要继续跑 |
| 磁盘满 | 暂停摄入，清理过期日志/事件，保留审计 | 删除批准/证据 |
| 成本失控 | 降批、暂停、检查 agent 调用上限 | 让循环继续烧模型 |

### 13.5 配额与资源

- 项目级：`max_bots`、`max_active_bots`、`max_agent_calls_per_day`、`max_state_bytes`。
- Bot 级：批次大小、上下文事件数、Agent 超时、输出上限、记忆保留期。
- 资源：每个 bot 一个锁、一个状态目录、最多一个运行时进程/pane；禁止同一 bot 多实例并行。
- 清理：先过期事件、再旧报告、最后记忆；批准、lifecycle 和审计按项目保留策略保存。

### 13.6 变更管理

- 定义变化 → 新 revision → 重新批准 → 重启。
- 策略变化 → 所有 bot 重新校验；不满足的进入 `approval_invalidated` 或 `degraded`。
- 包升级 → 三端安装副本测试 → 项目内 `once` 验证 → 再常驻。
- 任何外部契约变化（API、消息格式、权限语义）交回用户，不自行决定。

## 14. 映射到本仓现有件

| 现有件 | 用法 | 新增内容 |
| --- | --- | --- |
| `project-collaboration` Skill | 创建需求对齐、协调者决策边界、按需 Agent 分诊和验收 | 一个按需 `references/bot-framework.md`，不新增第二套主流程 |
| `goal_ref` | 创建、启动、每次 Attempt 都回溯到目标来源 | bot 定义保存父目标；事件转开发任务时仍需自己的目标或明确关联 |
| `current-task.json` | 创建 bot、重大配置变更和具体处置都可作为独立任务 | 不把长期 bot 的全部历史塞进一张当前任务卡 |
| task graph | 当前由任务卡的 `dependencies`、计划和派单关系表达 | 本仓没有独立 task graph 引擎；MVP 不新增图引擎，只保留父子目标和依赖引用 |
| `validate_task.py` | 创建/配置变更/处置任务开工前继续校验 | 新增 bot 定义、项目策略和适配器配置校验，不能替代任务卡校验 |
| handoff / PROGRESS | 保存运行资源、最近安全点、恢复入口和需要用户事项 | 每 bot 一个快照，并在项目总 PROGRESS 引用 |
| context-brief | 本仓当前没有同名公共工件 | 新增按 Attempt 生成的只读简报；它是派生视图，不是新事实源 |
| dispatch ledger | 只记录真实派单、状态、回执、验证、审查和判决 | bot 生命周期写独立 lifecycle 日志，通过 bot_id、revision、goal_ref 关联，不伪造 dispatch |
| `agent_eval.py` | 每个 Agent Attempt 继续评估和汇总 | 可为 bot 类型、适配器版本、revision 增加关联字段，不改变门禁语义 |
| Herdr | 可选进程 pane、按需 Agent、状态观察和资源回收 | 新增 bot 资源登记与恢复规则，不把 Herdr 变成硬依赖 |
| 三端入口 | 继续通过同一包分发公共脚本和薄包装器 | 增加 `bot.sh` 和场景包装器的无模型测试，不要求三端各自实现调度器 |

## 15. 参考实现对照

| 维度 | DS-Bot | OpenMausBot | Hermes Bot Mode | 本仓 |
| --- | --- | --- | --- | --- |
| 形态 | DSH 桌面应用/插件 | 桌面应用 + 本地 harness server | Hermes 桌面插件 | 版本化 Skill/CLI 包 |
| Bot 身份 | DSH 会话 + 配置 | harness 中的 agent 实例 | Hermes profile | 项目本地定义 + 批准 |
| 模型会话 | 长期 + 换段/后台 | 每 bot 一个 CLI 进程 | 每次 CLI 调用 | 默认按批，可选常驻 |
| 触发器 | 定时/后台/群 | 定时/webhook/app event | cron routine | manual/interval，后续 event |
| 审批 | 权限 broker + 工具 | 权限 broker + 审批卡 | CLI 原生审批 | 批准快照 + 运行门禁 |
| 私有边界 | 隐私 Bot 单向隔离 | 本机数据 + VM/云电脑 | profile 隔离 | workspace + 路径检查（非 OS 沙箱） |
| 控制面 | 桌面 UI + MCP | HTTP/SSE + 有界 MCP | 桌面 UI | CLI + Skill |
| Bot 间协作 | 群聊/派活树 | 团队/频道 | Bot Chat handoff | 协调者 + 任务卡/依赖，不做群聊 |
| 适合本仓 | 机制参考 | 触发器/控制面参考 | 最小身份模型参考 | — |

### 15.1 采用

- 长期身份 + 短期认知；
- 单写入口/账本；
- 批准代理；
- 有界控制面；
- 触发器跳过重叠；
- 失败显式化；
- 证据门禁；
- 状态不塞系统提示；
- 本地 Agent 复用现有登录。

### 15.2 不采用

- 桌面 UI、Electron、移动端；
- DSH/云电脑/浏览器/电脑操作；
- 多 provider 模型与密钥管理；
- 群聊房间、头像、语音；
- MCP/Connector 市场、动态能力安装；
- 多租户/云同步；
- Bot 自治团队叙事。

## 16. 离目标还有多远

### 16.1 当前事实

| 能力 | 状态 |
| --- | --- |
| 目标模型、边界、场景、部署、运维设计 | 已有（本文） |
| 飞书场景配置、游标、分诊、人读输出设计 | 已有（`docs/feishu-sync-design.md`） |
| Bot Definition Schema | 只有文档示例，没有解析器 |
| 批准/生命周期 CLI | 未实现 |
| 确定性 Runtime | 未实现 |
| 适配器接口与飞书适配器 | 未实现 |
| 锁、游标、批次、恢复 | 未实现 |
| Agent 调用契约（Claude/Pi/Codex） | 未验证 |
| 三端包装器与安装副本测试 | 未实现 |
| 部署（Herdr/cron/launchd/systemd） | 只有设计 |
| 运维手册与事故演练 | 只有设计 |
| 真实基准与验收 | 无 |

### 16.2 关键缺口

1. **Schema 与校验器**：没有它，批准和运行时都没有可信输入。
2. **批准记录与摘要**：没有它，“自动创建”会退化成“agent 自己决定”。
3. **确定性运行时**：锁、触发器、游标、批次、恢复、健康，是 bot 能不能长期跑的核心。
4. **适配器接口**：没有稳定接口，第二个场景就会复制一套逻辑。
5. **Agent 调用契约**：三端 CLI 的非交互调用、会话恢复、权限模式、输出解析都还没有实测。
6. **部署与恢复**：Herdr pane、cron、launchd、systemd 的锁和重启语义不同，必须逐个验证。
7. **可观测与验收**：没有健康、滞后、失败分类，运维只能靠“看起来还在跑”。
8. **基准**：需要固定场景、固定版本、固定预算的 A/B，才能证明框架比“人工写个脚本”更好。

### 16.3 阶段路线

#### 阶段 0：规范冻结（当前）

交付：本文 + 飞书场景文档 + 术语表 + Schema 草案 + 错误分类 + 批准字段。

退出条件：对象、门禁、默认拒绝、MVP 范围、非目标都无歧义；用户确认。

不做：代码、真实 bot、外部调用。

#### 阶段 1：控制面与假适配器（已实施）

实际交付：

- `bot.py` 的 `validate/explain/approve/run --once/status/stop/resume`；`draft/start/pause/retire` 尚未实现；
- Schema 校验、路径越界、策略交集、批准摘要、lifecycle；
- `bot_runtime.py` 的单次 `once`、锁、配额、健康、`fake` 适配器；
- 无模型测试：默认拒绝、批准失效、重复事件、锁冲突、路径逃逸、配置错误。

退出条件已满足：合成 bot 可以在临时项目里 validate → approve → once → stop/resume；没有网络和模型调用；测试可重复。

不做：真实飞书、Herdr、常驻循环。

#### 阶段 2：飞书只读实例（第一步已实施）

已交付：

- `bot_adapters/feishu_sync.py`：配置与环境变量解析、只读命令封装、分页、评论去重、逐项游标、错误分类；
- 合成 fixtures 覆盖认证失效、token 过期、重复评论、分页边界、配置缺失、环境变量覆盖和二次零新增；
- 单次 `once` 的真实只读拉取验收（真实数据只落在临时项目，不进公共仓）。

尚未交付：常驻/定时触发、批次、分诊 Agent 调用、人读报告、真实项目长期连续验收。

退出条件（完整阶段 2）：连续多轮 `once` 不重复、不漏项；认证失效不推进游标；报告与机器结论同源；不写飞书。

不做：外部写、自动回复、L2。

#### 阶段 3：常驻与部署

交付：

- Herdr pane 启动/停止/恢复；资源登记；只回收自有资源；
- cron/launchd/systemd 的 `run --once` / `run --foreground`；
- 崩溃恢复、锁接管、重复批次对账；
- 运维手册和事故演练脚本（只读诊断）。

退出条件：模拟崩溃、锁过期、pane 丢失、磁盘满、认证失效，都能给出明确状态并恢复；不误关他人资源。

#### 阶段 4：泛化与 Agent 契约

交付：

- 第二个只读适配器（如 CI 或 Issue），证明接口不是为飞书定制；
- Claude/Pi/Codex 的 `agent_batch` 调用契约和安装副本测试；
- 跨三端一致的退出码、超时、输出校验；
- 固定场景基准（baseline 脚本 vs bot 框架）。

退出条件：同一 bot 定义在三端可运行；差异有文档；基准显示至少不劣于人工脚本，且在异常分类/恢复上有可测优势。

#### 阶段 5：L2 本地动作（可选）

交付：

- 每个动作独立任务卡、写范围、验收、Attempt；
- 分支/补丁/测试证据；
- 人审后由人合并/推送；
- 失败回滚。

退出条件：没有任务卡不能写；没有验收不能标完成；越界写入被拒绝；回滚可验证。

#### 阶段 6：交互式/外部通知（可选，重新评估）

- `agent_resident`、外部审批卡、IM 输出、多 bot 协作。
- 每项都需要新的威胁模型、权限设计和真实验收，不能借“框架”自动获得。

## 17. MVP 范围与验收

### 17.1 MVP 做什么

- 一个项目、一个飞书只读 bot；
- 定义、策略、批准、生命周期、锁、游标、事件、批次、分诊、人读报告；
- `process` + `agent_batch`；
- 面板 + 项目本地文件；
- Herdr 或 cron 二选一部署；
- 无模型测试 + 一次真实只读验收。

### 17.2 MVP 不做什么

- 多 bot 群聊、自由互调、子 bot 自动创建；
- 常驻 Agent、L2 自动修复、任何 L3 外部写；
- MCP、动态能力安装、多 IM、桌面 UI、浏览器/电脑操作、供应商管理；
- 独立 task graph 引擎或新的记忆数据库。

### 17.3 验收清单

- [ ] 没有项目策略、目标或批准时不能启动；
- [ ] 修改权限、指令、适配器、触发器或输出后旧批准失效；
- [ ] Bot 不能修改自己、创建子 bot 或调用未声明命令；
- [ ] 外部评论中的指令不会改变控制面行为；
- [ ] 路径越界、symlink 逃逸、绝对路径被拒绝；
- [ ] 重复事件不重复分诊；
- [ ] 部分失败不推进该项游标；
- [ ] 认证失效显示为“未登录”，不是空结果；
- [ ] 空轮询不生成空报告、不调用模型；
- [ ] Agent 输出非法时不写入结论；
- [ ] 锁冲突时跳过而不是叠加；
- [ ] 崩溃后能对账并恢复，不重放已完成外部动作；
- [ ] 进程/pane 可停止、可回收，且不误关他人资源；
- [ ] 三端安装副本的同一校验器行为一致，且不需要模型；
- [ ] 所有真实 ID、路径、游标、报告都在项目本地，公共仓只有占位符。

## 18. 实施前必须验证

- Claude Code、Pi、Codex 的非交互调用、会话恢复、权限模式和退出码是否足以形成统一契约；
- 飞书评论分页、排序、增量字段和 token 失效的真实行为；
- Herdr pane 的创建/回收/失联检测在不同版本上的一致性；
- cron/launchd/systemd 对文件锁和重启的处理差异；
- 单机文件锁在 NFS/共享目录上的可靠性（第一版应明确只支持本地盘）；
- 批准摘要在 JSON 规范化、跨平台换行和路径大小写上的稳定性；
- 批次大小、上下文长度、Agent 超时和成本的真实边界；
- 状态目录的保留期、磁盘占用和清理策略；
- 多项目安装时，三端包缓存、版本和配置引用的隔离；
- 运维人员是否真的会看面板和 health，而不是只看“进程还在”。

这些未验证前，不得把对应能力写成“已支持”。