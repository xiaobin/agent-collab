# Changelog

本项目使用 SemVer。公共核心契约的语义变化需要用户确认，详见 `docs/governance.md`。

## [0.4.10] - 2026-11-26

### Fixed

- `triage dispatch` 不再把批次派给派发方自身：优先排除 `HERDR_PANE_ID`，其次用本项目 `state/pane.pid` 与 `herdr pane process-info` 核对 pane，再叠加配置 `triage.dispatch.exclude_panes`。
- 新增 `triage.dispatch.allow_pane_labels`；非空时只允许匹配 label、terminal title 或 pane_id 的目标。
- 只选择 `claude`、`codex`、`pi` 的 idle Agent pane；shell、unknown 或缺失 agent 类型跳过，并记录“no actionable agent pane”。
- 只剩自身 pane 时保持 `pending`，原因写明“only the dispatching pane is available”，不自派自答。

### Compatibility

- 已有 `herdr_batch` 配置无需迁移；未配置 `exclude_panes`/`allow_pane_labels` 时默认不限制额外 pane，自身 pane 仍自动排除。
- 仍不执行飞书写操作，不改变批次、幂等、结果校验和报告契约。

## [0.4.9] - 2026-11-26

### Added

- 新增 `triage dispatch`：按 `triage.dispatch.workspace_label` + `cwd_marker` 解析 Herdr workspace 中的 idle Agent pane，不按 pane 编号配置目标。
- 派发前使用现有 `claim`/`attempt_id` 占批次；同一批次重复调用不会重复发送；`pane run` 退出 0 后仍等待 Agent 结果文件并通过 `apply` 校验，超时或非法输出不写“已分诊”。超时时记录缺失事件，只对缺失事件发一次补救提示，不重发整批。
- 忙碌 pane 保持待办并记录等待原因；Herdr 不可用、无配置、无匹配 pane 均保持 `pending`。
- `triage dispatch --watch` 可作为项目侧自动派发入口；`dispatch.jsonl` 记录批次、attempt、pane、状态、原因和缺失事件。
- 新增 `tests/test_bot_dispatch.py`，覆盖 idle 派发、忙碌等待、不可用、重复调用、超时补救、非法输出和 pane 编号拒绝。

### Compatibility

- `triage_trigger=manual` 行为不变；未配置 `triage.dispatch` 的 `herdr_batch` 批次仍保持 pending。
- 派发只传 batch_id、batch 文件路径、结果文件路径和分类口径，不传凭据或完整事件库；仍不执行飞书写操作。

## [0.4.8] - 2026-11-26

### Fixed

- `meegle auth status` 返回 `authenticated:false` 时不再一律判定为认证过期：只有 reason 含 401/token expired/unauthorized/not logged in/authentication expired 才退出 6；server unreachable/timed out/network/connection 等按 `upstream_error` 退出 5 并退避；无法归类按 `unknown_auth_state` 退出 5 并退避。
- 网络抖动不再写 `auth_required`、不再停止常驻循环、不再提示人工重新登录；连续上游错误按 1–30 秒退避持续重试，直到用户停止。
- 新增断网、真实 401、未知 reason 三条适配器回归，以及常驻连续 upstream_error 直到 SIGTERM 的回归。

### Compatibility

- 真实认证失效仍退出 6；命令本身返回 401/token expired 时行为不变。
- 仅只读采集与分诊链路，不改变配置字段或数据格式；仍需重新批准仅在定义/策略/适配器配置变化时发生。

## [0.4.7] - 2026-11-26

### Changed

- 分诊分类改为按评论相对“已交付内容”的意图判断：`correction_of_delivered_work`、`answer_to_my_question`、`clarification_request`、`handoff_or_notice`、`scope_or_contract_change`、`needs_more_context`；`triage_policy_version` 升为 `triage-v2`。
- 结果新增必填 `source`（`agent`/`heuristic`）；`correction_of_delivered_work` 必须为高优先级。报告默认只读 `source=agent`，启发式结果必须用 `--source heuristic` 单独渲染，不混在同一份报告。
- 报告条目增加判定依据和来源，避免只展示模板化建议。
- 真实 169 条验证中，独立复核抽检发现 2 条误判（空正文附件、混合契约意图），已用 `supersedes` 纠正；最终 169 条有效结论，`results.jsonl` 保留 171 行（169 有效 + 2 条纠正链）。

### Fixed

- `results.jsonl` 的 `author` 从批次事件快照回填；作者缺失写 `unknown`，不再留空。真实 171 行 author 覆盖率 171/171，其中 8 条为 `unknown`。
- `evidence_refs` 和 `uncertainties` 必须以 JSON 数组保存；报告加载时拒绝 list 的字符串表示，并给出字段级错误。
- 报告剥离 `<!-- mention:... -->` 元数据，只保留 `@姓名`。
- 面板不再把高优先级条目跨组重复列出：顶部只给总数和分组分布，事件只在所属分组出现一次，组头标“其中 N 项标 [高]”。

### Compatibility

- 0.4.6 的 `triage-v1` 结论和旧四分类不再被新报告采用；需要按新口径重新分诊。已提交的批次可保留原文，但 `results.jsonl` 中的旧结论不应混入 `triage-v2` 报告。
- 批次、幂等、原子写、并发锁、fail-closed 和人读分组/行数限制保持不变。
- 仍为只读、`suggest_only`，不执行飞书写操作，不调用模型命令行，不重启常驻进程。

## [0.4.6] - 2026-11-26

### Added

- `bot.py triage prepare|claim|apply|dispose|report`：从 `events.jsonl` 生成确定性批次，空事件不建批；达到 `triage.max_batch_events` 立即切批，否则等待 `triage.batch_interval_seconds`。
- 分诊结果契约：四类分类、独立 urgency、`triage_policy_version`、`supersedes` 纠正链、同 event+policy 唯一有效结论；`results.jsonl` 追加，`dispositions.jsonl` 记录后续处置。
- 人读输出：从同一份 `results.jsonl` 生成全量 Markdown 和受限面板摘要，按需要你决定/可自行处理候选/待补上下文/仅知会分组，每组默认 5 项、总计 40 行；高优先级全局置顶，面板截断时保留各分组计数和完整报告路径；空事件静默，pending 显示 `TRIAGE_PENDING`，认证失效/部分失败不建批且与无新增长得不一样。
- prepare/claim/apply/dispose 共用 `state/triage/lock` 单写锁；结果整批原子追加，失败后可按原输入幂等续跑；`ready.jsonl` 缺失时 prepare 自动补发；认证状态同时识别 `auth_required`、`authentication_expired`、`unauthorized`，health 缺失/损坏 fail-closed。
- 新增 `tests/test_bot_triage.py`，覆盖空批次、恰好/超量切分、重复事件、同批重复 `triage_id`、supersede、真实认证状态、health 缺失/损坏、并发 claim/apply、部分 apply 恢复、ready 修复、分组高优置顶与行数上限、pending 和处置去重。

### Compatibility

- `triage_trigger=manual` 优先落地；`herdr_batch` 仍只生成 pending 批次，不自动假装已分诊。
- 仍为只读、`suggest_only`，不执行飞书写操作、不调用模型、不改变常驻采集进程。
- 新增配置字段均有默认值：`triage.triage_trigger`、`triage.human_report_max_items_per_group`、`triage.human_report_max_lines`；旧定义无需迁移。

## [0.4.5] - 2026-11-25

### Added

- `run --resident` 只读常驻采集：按 `--interval-seconds` 或 interval 触发器重复执行现有拉取，追加 `events.jsonl`、推进游标、更新状态和健康；`--max-rounds` 仅用于受控验证。
- 单实例：常驻循环持有 `state/resident.lock` 整个生命周期，重复启动退出 4；`stop`/`resume` 通过短 state 锁工作，不需要等待常驻睡眠。
- 健康与恢复：`resident.json` 记录 PID、启动/心跳时间、租约和停止原因；`health.json` 同步守护状态；SIGTERM/SIGINT 在当前轮结束后收尾；重启从游标继续，已消费事件不重放。
- 认证失效立即写 `paused`/`auth_required` 并退出 6，不自动登录、不继续轮询；普通失败指数退避（1 秒起、30 秒封顶）。
- 每轮开始前重算批准摘要、定义/策略/适配器哈希和身份 `binding`；过期或任一文件漂移写 `approval_invalidated`、退出 3，不产生新一轮动作。
- 收尾写状态时持短 state 锁，避免并发 `once`/`stop`/`resume` 的新游标被旧状态覆盖；`status` 核对 PID 与租约，死进程或过期租约显示 `stale`，不假报 `running`。
- `resident.json` 中存活 PID 作为第二道单实例检查：`resident.lock` 被手动删除后，重复启动仍退出 4。
- `--max-rounds` 内全部轮次失败时返回最后一次失败码并写 `degraded`/`failed`，不再以 0/stopped 伪装成功。
- `state/run.log` 记录每轮开始/结束、事件增量、游标摘要哈希和错误码，不写评论正文、作者或附件 URL。

### Compatibility

- 现有 `run --once` 行为不变；新增 `--resident`、`--interval-seconds`、`--max-rounds`，三者与 `--once` 的组合由 CLI 明确校验。
- `status` 输出在有 `resident.json` 时附加 `resident` 字段；既有字段保留。
- 常驻模式仍只读、不调用模型、不做分诊或人读报告；Herdr/cron 定时部署仍未实现。

## [0.4.4] - 2026-11-25

### Added

- 飞书适配器通过 `user search` 批量解析评论作者姓名，单次最多 20 个 key；只使用 `name_cn`/`name_en`，不拿登录用户姓名兜底。姓名缓存在工作区 `state/user-cache.json`（`0600`），命中与未命中分开 TTL；未解析事件写 `creator_name=null`、`creator_name_status=unknown` 和原因。
- 附件事件增加 `name_status=unavailable` 与 `dedupe_key`（`file_url` 的 SHA-256 前缀）；真实接口不返回附件名，`file_url` 可稳定判重但不能当文件名。
- 批准快照增加 `binding`：`feishu` 绑定 `project_key + user_key`；运行前从当前配置和环境变量重算，任一值变化退出 3，不进入适配器。
- 真实只读验证：161 条评论姓名解析 161 条、缓存 4 个用户；32 个附件 URL 稳定且判重键唯一；二次运行零新增。

### Compatibility

- 0.4.3 的 `feishu` 批准快照没有 `binding`，升级后需重新批准；`fake` 适配器绑定为空对象，不受影响。
- 评论事件新增 `creator_name_status`、`creator_name_source`、`creator_name_reason`；附件对象新增 `name_status`、`dedupe_key`。原有字段保留。
- 未实现常驻循环、Herdr 定时、Agent 调用和分诊批次；公共包仍不包含真实 ID、凭据、游标或事件。

## [0.4.3] - 2026-11-25

### Added

- `feishu` 只读适配器：通过 `meegle` 的 `auth status`、`user me`、`mywork todo`、`comment list`、`workitem get` 枚举待办、读取工作项详情与评论，产出 `work_item.discovered`、`work_item.updated`、`comment.created` 事件。
- 身份项走配置：`project_key`、`work_item_id`、`expected_user_key` 可由项目配置或 `MEEGLE_*` 环境变量提供，代码中没有真实项目或人员 ID 默认值。
- 评论按 `(created_at, comment_id)` 排序、按 `comment_id` 去重；分页超过上限视为不完整，退出 5 且不推进游标。
- 工作项快照递归规范化数组顺序，避免真实 API 返回顺序变化造成假更新；游标记录每项快照摘要和已见评论 ID。
- 认证失效或 token 过期退出 6，不自动登录、不重试；真实运行只接受 `--once`，不调用模型。
- 合成 meegle CLI 测试覆盖首轮事件、二次零新增、分页终止、分页超限、认证失效、token 过期、配置覆盖、重复待办行和数组顺序不稳定。

### Compatibility

- 现有 Bot 定义、批准快照、状态和退出码保持不变；`feishu` 需要项目策略允许该适配器，且定义中 `network_read=true`、`network_write=false`。
- 未实现常驻循环、Herdr 定时、Agent 调用和分诊批次；公共包仍不包含真实 ID、凭据、游标或事件。

## [0.4.2] - 2026-11-25

### Added

- 通用 Bot 框架阶段 1：`bot.py` 提供 `validate`、`explain`、`approve`、`run --once`、`status`、`stop`、`resume`；定义与项目策略校验覆盖目标、适配器、运行时、工作区、权限、触发器、输出、记忆、健康和升级路径。
- 批准快照写入 `.herdr/bots/approved/<bot-id>/<revision>.json`，包含定义、项目策略、适配器配置三份 SHA-256，并绑定项目根、运行用户、批准人、时间、权限范围和可选过期时间；同 revision 采用原子 no-replace 写入，跨项目或跨用户重放会拒绝。
- 单次进程运行时：非阻塞文件锁、游标、事件去重、`state.json`、`health.json`、`events.jsonl` 和 append-only `lifecycle.jsonl`；`stop`/`resume` 与运行共用锁，stopped/paused 不会隐式恢复。
- 固定状态与批准写入使用 dirfd、`O_NOFOLLOW` 和原子提交，拒绝 symlink 越界；私有状态文件为 `0600`，新建目录为 `0700`。
- `max_bots`、`max_active_bots` 和 interval 最小间隔在批准/运行路径执行；非法 `max_autonomy` 与非法策略列表 fail closed。
- `fake` 本地适配器，支持 `ok`、`auth_expired`、`partial_failure`，不访问网络、不读凭据、不调用模型。
- `skills/project-collaboration/scripts/bot.sh` 三端包装器、`references/bot-framework.md`、`templates/bot-definition.json` 与 `templates/bot-policy.json`。
- 阶段 1 无模型测试：Schema、批准、运行时、锁、认证失败、部分失败、摘要篡改、symlink 越界、stop/resume、策略门禁、跨项目重放、并发批准、非法事件、写失败、read_scope、退出码与包装器。

### Compatibility

- 现有任务卡、评估记录、ledger 和项目 `.herdr/` 状态格式不变。
- Bot 状态只写入项目 `.herdr/bots/`；公共包不包含真实项目 ID、人员 ID、凭据、游标或报告。
- 常驻运行时、真实适配器、Agent 调用和网络访问未实现；`run` 不接受常驻模式，`agent_batch` 会明确拒绝。

## [0.4.1] - 2026-11-24

### Changed

- 进度格式扩展为七段结构：总目标必须给出目标来源（`goal_ref`）、目标描述和阶段骨架；新增任务边界（范围内、写范围、非目标、授权边界）和逐项改动清单（位置、改动、状态）。首次汇报、里程碑和交付必须列全；日常短同步可省略表格，但不得省略目标来源和当前写范围。
- 缺任务边界的汇报视为目标未对齐，不能据此开工或验收；纯调查任务在改动清单写“无（只读调查）”。
- `PROGRESS.md` 模板与最小内容同步增加任务边界和改动清单。

### Compatibility

- 任务卡 schema、评估记录、ledger 和项目 `.herdr/` 状态格式不变，无需迁移。
- 项目入口文件不复制本汇报格式；已复制旧格式的入口需改为指向 `references/communication.md`。

## [0.4.0] - 2026-11-24

### Added

- 可选的 Herdr 编排适配参考：lane 生命周期、派单前置、pane/agent 命令形态、状态处理、结构化回执和资源收尾。
- 完整 dispatch ledger 事件契约：dispatch、state、result、verify、review、decision、verdict、integration，保持 append-only 和 Attempt 递增。
- 验收顺序与四态结论：缺少证据时只能记 `UNKNOWN` 或 `BLOCKED`，不能记 `SUCCEEDED`。
- 修复轮次纪律：小批量、可执行完成定义、反证、回归锁定、同一缺陷两轮未过即升级。
- 统一的进度符号 `✅`/`◐`/`▶`/`○`/`⛔` 及总目标骨架报告规则。

### Compatibility

- Herdr 是可选适配，不是安装依赖；不采用 Herdr 的项目无需迁移。
- 任务卡 schema、评估记录和项目 `.herdr/` 状态格式不变。
- 具体仓库、agent、pane、命令、验收环境和项目特例仍由项目维护，不进入公共包。

## [0.3.0] - 2026-11-24

### Added

- 同一仓库支持 Claude Code Plugin、Pi package 与 Codex Plugin 三种分发载体，公共 Skill、脚本和项目状态格式共用一份正本。
- Pi `package.json` manifest，显式导出 `project-collaboration` Skill。
- Codex `.codex-plugin/plugin.json` 与 `.agents/plugins/marketplace.json`，通过同一 Skill 目录提供插件能力。
- Skill 目录内的任务卡与评估脚本包装器，统一 Claude Code、Pi、Codex 和普通终端的路径解析。
- 三端安装、自安装、项目接入、升级和恢复文档，以及 manifest、frontmatter、包装器和隔离实装回归测试。
- Agent 自安装提示必须先向用户确认安装范围与是否固定版本，不得预设 `user` 或目标版本。

### Compatibility

- Claude Code 入口保持 `/agent-collab:project-collaboration`；Pi 入口为 `/skill:project-collaboration`；Codex 入口为 `$agent-collab:project-collaboration`。
- 任务卡、评估记录、ledger 和项目 `.herdr/` 状态不变，无需迁移。
- 核心目标、权限、验收和评估契约不变；三种工具都不获得自动提交、推送、部署或业务验收权限。

## [0.2.0] - 2026-11-24

### Added

- Agent 评估记录模板，以任务、Attempt 和 Agent 为最小归因单元。
- `agent_eval.py check` 质量门禁与 `rollup` 同类任务能力档案。
- 独立复审、业务验收、硬失败、证据不足和责任归因规则。
- 项目历史与同版本 benchmark 分池聚合，小样本和未知指标显式标记。
- 追加式 correction 链：新记录通过 `correction_of` 更正旧判断，汇总只统计链最新记录。
- 评估流程、派单选型、防刷分原则、测试 fixtures 与自动化验证。

### Compatibility

- 任务卡 schema v1 不变，既有项目无需迁移任务卡。
- 评估历史保存在项目本地 `.herdr/agent-evaluations.jsonl`，Plugin 升级不覆盖项目状态。
- 不自动上传评估数据、淘汰 Agent、部署或发布；人的审批和业务验收边界不变。

## [0.1.0] - 2026-11-24

### Added

- 团队共享的 Claude Code Plugin 与内部 marketplace 清单。
- `project-collaboration` Skill，覆盖目标对齐、过程沟通、派单、验证、交付和恢复。
- 生命周期、决策边界、过程沟通、证据验收和状态交接 references。
- 薄 AGENTS、CLAUDE、当前任务卡和 PROGRESS 模板。
- 无第三方依赖的 `validate_task.py`，支持 draft 结构检查与 dispatch 开工就绪检查。
- 单元测试、结构测试、有效任务卡样例和一键 `verify.sh`。
- 本地安装、项目接入、架构和团队版本治理文档。

### Boundaries

- 不修改或覆盖接入项目的运行状态和证据。
- 不自动提交、推送、发布或启用组织强制安装。
- 技术验证不替代独立复审和人的业务验收。
