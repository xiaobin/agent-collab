---
name: bot-report
description: 查看已接入 Bot 采集到的飞书待办摘要：空间、工作项数、评论数、未完成待办（工作项 ID、标题、当前节点、节点负责人）。默认只读本地已采集数据，只有用户明确要求时才先同步一次（沿用批准门禁）。用户说「看下飞书待办」「看看 bot 的报告」「先同步一下再看看」时使用。
---

# Bot 报告（查看已采集数据）

三端各一条命令，语义相同：

- Claude Code：`/agent-collab:bot-report`
- Pi：`/skill:bot-report`
- Codex：`$agent-collab:bot-report`

用户用自然语言说「看看飞书待办」也走本 Skill。**用户不需要看到或复制任何 python 命令**。

记 `<plugin>` 为插件根（Claude Code 即 `${CLAUDE_PLUGIN_ROOT}`），`<project>` 为目标项目根，`<bot>` 默认 `feishu-sync`。

## 流程

1. **定位**：在项目根运行；`bot_id` 默认 `feishu-sync`，项目里有多个 bot 时先问用户看哪个。
2. **默认只读**：直接渲染本地已采集的数据。

   ```bash
   python3 "<plugin>/scripts/bot_report.py" report --project-root . --bot-id <bot>
   ```

3. **可选同步一次**：只有用户明确说「先同步一次」「要最新的」时才加 `--sync`；它会调用 `bot.py run --once`，因此同样受批准门禁约束。

   ```bash
   python3 "<plugin>/scripts/bot_report.py" report --project-root . --bot-id <bot> --sync
   ```

4. **转述**：把命令输出的人读摘要原样转述给用户（空间、工作项数、评论数、待办列表）。不修改数字，不补充命令里没有的内容。
5. **报告文件**：默认写到 `.herdr/bots/<bot>/reports/summary-<UTC 时间戳>.md`；用户要留存或分享时给出这个路径。

## 失败含义（原样回报，不要绕过）

| 现象 | 处理 |
| --- | --- |
| `--sync` 返回退出码 3（批准门禁） | 说明配置尚未批准或已失效；回到 bot-onboarding 重新出计划并请用户确认，**不自动重批** |
| 输出「还没有初始化」 | 还没完成接入；引导用户走 bot-onboarding |
| 输出「还没有采集到任何数据」 | 未跑过采集或上次运行失败；用户要求时可加 `--sync`，或先查 `bot.py status` |
| 输出「批准已失效」 | 配置/身份/策略在批准后变化；重新出计划并再次确认 |
| 输出「飞书登录已失效」 | 请用户自己在终端完成 meegle 登录，然后恢复运行；不要代跑登录 |
| 负责人或作者显示「未知（user_key）」 | 本地缓存里没有该用户姓名；照实展示，不猜名字 |

## 边界

- 只读：不评论、不改状态、不建单，不调用任何飞书写操作。
- 默认不触发采集；`--sync` 必须由用户明确要求，且不得绕过批准门禁。
- 不修改 definition、adapter-config 或批准快照；发现漂移只报告。
- 数据全部来自本地 `state/events.jsonl`、`state/state.json` 与 `state/user-cache.json`。