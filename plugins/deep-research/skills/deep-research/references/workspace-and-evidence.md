# 原文回读与证据记录

用于多轮研究、长文定位、断点续研和关键 claim 核验。简单事实查询只需保留最小证据链。

## 任务目录

开始时把 `DEEP_RESEARCH_RUN_DIR` 设置为当前任务的独立目录。下文的 `research.py` 使用 [retrieval-and-tools.md](retrieval-and-tools.md#运行与依赖) 中的 `uv run --with-requirements ...` 前缀执行。

```text
<run-dir>/
  .deep-research-refs.json       # Search 和直接 Read 登记的来源
  .deep-research-citations.json  # 此任务成功读取的网页 URL
  .deep-research/
    task.json                   # 可选：任务合同、Need 进度、失败记录和停止原因
    searches/<search_id>.json   # 查询参数、检索时间和候选结果；不等于正文证据
    sources/REF-.../
      metadata.json             # 最近快照的元数据
      <snapshot_id>.json        # 正文与元数据共同标识的历史快照
      <content_hash>.json       # 兼容旧版；保留该正文首次保存的元数据
      <content_hash>.txt        # 保存的正文提取结果
    evidence.json               # 经原文摘录校验的证据卡
    outline.json                # 可选：章节、Need 归属和提纲修订历史
    syntheses/<section>.json    # 调用者综合，独立于原文证据
    packets/<hash>.json         # 全量章节证据包；CLI 预览受字节预算限制
    audits/<report_hash>.json   # 草稿逐句证据绑定与结构检查
```

原文快照指提取后的文本，不是网页原始 HTML 或 PDF 二进制。元数据包括来源、读取时间、内容 hash、内容范围、是否截断、正文位置；PDF 还保存页码对应的字符区间。`content_hash` 只标识正文，用于相同文本去重；`snapshot_id` 同时区分正文及其来源、范围、截断标记和页码等元数据。历史快照保留，刷新不会覆盖旧证据使用的正文或元数据；正文与元数据均相同的重复读取复用快照标识，当前读取时间仍在 metadata.json 更新。

HTML 正文保留 HTTP(S) 链接的名称和地址；相对地址按跳转后的页面地址及 HTML 的 `base href` 解析。查找作者代码或附件时，可先用 `find REF --term github.com` 或正文中的链接名称定位，再对目标 URL 单独 `read`。链接只是线索，父页面的 Read 不会读取链接目标，也不会将其加入引用白名单。旧快照不会自动补回地址，确需重新提取时使用 `read REF --refresh`。

PDF 的 HTTP(S) 链接注释以 `[PDF link] URL` 保留在对应页的快照中，同页重复注释只记一次，并计入正文长度上限。这是可继续读取的地址，不是论文原句；`find` 返回其所在页码，目标内容仍须单独 Read。本地 PDF 也可离线提取这些地址。

按章节导出证据包、修订提纲、追踪论文前后引用、审计报告或进行固定语料成对评估时，读取 [reporting-and-evaluation.md](reporting-and-evaluation.md)。这些功能共用当前任务目录，不改变来源与语义判断的边界。

## 任务进度与恢复

多轮研究先登记任务和完整 checklist。每个可独立核验的必答问题或字段使用一个 Need ID；数量不受主 SKILL 中 1–4 个研究方向的限制。简单查询可以跳过初始化，原有 search/read/evidence 用法继续有效。

```bash
research.py init --deliverable "比较两种方案并给出建议" --target "A 与 B 的当前版本" \
  --constraint "保留用户要求的全部比较字段"
research.py need N1 --question "A 的运行成本" --proof "当前版本的计价单位与费用" --priority 1
research.py need N2 --question "B 的运行成本" --proof "当前版本的计价单位与费用" --priority 1
research.py need N2 --state partial --note "已读计价规则，尚缺存储费"
research.py failure --need N2 --action "读取官方存储费页面" --reason "页面返回 403"
research.py failure --action "运行共享导出工具" --reason "工具无法启动；已保留本地正文"
research.py status
```

- `init` 重复执行同一合同会保留进度；交付物、Target 或 constraints 不同则拒绝覆盖，提示使用新任务目录。
- `need` 首次登记需要 question 和 proof；已有问题不能被同 ID 的不同问题覆盖。优先级为 1–5，1 最高，默认 3；Need 状态为 `open | partial | blocked | answered`。更新状态必须写 note，说明结论或剩余缺口。
- 保存支持或反驳该 Need 的证据卡后，使用 `need N1 --state answered --note "具体结论及适用范围" --evidence EV_ID` 收尾。可重复 `--evidence` 关联多张卡。工具拒绝不存在、属于其他 Need 或仅为 context 的收尾证据；不会自动判定这些证据是否充分满足 proof。
- 每次显式确认 `--state answered` 时，`reviewed_evidence_ids` 记录该 Need 当时全部证据卡，包含未被选入结论的反驳和 context；`evidence_ids` 仍只表示所选结论依据。此后卡片增加或移除，`needs_requiring_review` 提示复核，保留原有 answered 状态。重复保存同一卡片、其他 Need 的活动和只刷新来源不触发该提示。
- 读取来源、增加证据或记录一次失败，都不会自动关闭 Need。`failure` 保留实际尝试和失败原因；针对某个 Need 的尝试传 `--need`，跨 Need 或任务级工具/流程错误可省略，保存为 `need: null`。显式空值或未登记 Need 仍拒绝；两种记录都要求已初始化且 active 的任务，不改变 Need、证据复核、完成条件或无收益轮次。
- `status` 给出所有 Need、`remaining_needs` 和按优先级选择的 `next_need`；已 blocked 和已答但待复核的项都保留在缺口清单中。`need_review_details` 给出新增/移除卡片总数及各最多 10 个 ID。旧任务的 answered 项若未保存复核范围，标 `review_basis_unknown`，变更计数为 null，需显式复核一次。失败与搜索的总次数和各自最近 10 条记录随状态返回，完整记录留在 task.json。`failure_count` 统计任务级及 Need 级失败记录条数，不等于失败搜索或 HTTP 次数；搜索次数包含缓存复用与失败，不等于联网次数。

出现待复核项时，用 `research.py review --need N1` 查看证据及范围；检查后用 `need N1 --state answered --note "已核对新增材料；结论及适用范围为……"` 重新确认，或将状态改为 partial/blocked 并记录缺口。若结论依据改变，同时更新 `--evidence`。修改备注、优先级、重复登记或刷新章节综合都不会代替这次显式确认。

```bash
research.py stop --reason "读取预算耗尽；N2 尚缺存储费"
research.py resume
# 全部已登记 Need 为 answered、没有待复核项，且检查过完整用户要求和冲突后：
research.py stop --complete --reason "全部必答项和重要冲突已核对"
```

`stop` 默认标记 incomplete；`--complete` 要求至少一个 Need、全部 answered 且没有待复核项。`resume` 重新激活任务并显示剩余缺口，保留已答项、复核范围、失败历史和上次停止原因。停止后修改 Need 进度须先 resume；停止后到达的证据仍可触发复核提示，工具不自动改写上次停止状态。单个分支停滞不能让其他必答项消失。

`completion_ready` 只检查已登记清单、显式进度和证据复核范围是否变化。待复核不等于原结论错误；工具无法发现漏登记的用户要求，也不验证结论的语义正确性或证据充分性，不要把它当作报告质量评分。已有证据先于 init 创建时仍保留，`unregistered_evidence_needs` 提示尚未登记的 Need；初始化后新增证据必须使用已登记 ID，避免拼写错误。

跨来源综合、推断前提及逐事实的 `E / I / ?` 仍可记在 `research.md`，格式见 [evidence-ledger.md](evidence-ledger.md)。使用 task.json 时，以其为任务进度依据，无需在笔记中重复维护一套状态。

## 搜索复用与下一步选择

```bash
research.py search "B storage pricing" --need N2
research.py search "B storage pricing" --need N2 --retry-failed
research.py search "B storage pricing" --need N2 --refresh
```

相同查询与检索参数默认复用当前任务保存的结果，返回 `cache_hit`、原始 `searched_at` 和 `search_id`，不再消耗搜索或 HTTP 请求额度。没有可用候选且任一渠道失败时返回错误，不缓存为空；所有已尝试渠道均成功但没有候选时返回 `empty`，可缓存。存在可用候选但部分渠道失败时返回并缓存 `partial`，保留 `provider_errors`；普通缓存回放仍标 `partial`。空结果、错误和部分成功都不能证明没有相关资料。

`--retry-failed` 只重试缓存中原先失败的有效请求；成功和有效空响应均复用，不重新查询。恢复扣一次逻辑 Search，HTTP 仍按实际尝试计量；再次失败或 HTTP 额度不足时保留旧线索和 `partial`，成功则清除对应的当前错误。返回 `recovery` 请求计数；这些计数包含被预算阻断的尝试，不等于实际 HTTP 次数。已有成功渠道的 `provider_results.completed_at` 保持原时间，恢复渠道更新自己的时间；顶层 `searched_at` 是本次组合保存时间，不能把全部线索称为刚刚检索。

恢复会重新组合原渠道输出和旧候选，最终仍受排序与结果数上限约束。若恢复后出现以前未执行的依赖请求（如 Kaggle 第二页），本次不发送，返回 `skipped`、`partial` 和 `recovery.deferred_new_requests`，提示显式 `--refresh`。没有失败请求的兼容缓存原样返回，不消耗预算。

`--refresh` 用于整次更新候选或扩展新依赖，与 `--retry-failed` 互斥；整次失败不会覆盖旧缓存。旧缓存仍可普通重放，但没有版本化渠道记录时无法定向恢复，需 refresh 一次。检索实现、学术/实战开关、URL 策略或有效 AnySearch 配置变化时，也须 refresh；普通缓存回看不受这些变化影响。各渠道的原始输出、有效参数、历史状态与时间只保存在任务缓存的私有 `provider_execution` 中，不随公开候选或 Search 事件返回。

缓存键区分大小写、limit、Target、artifact、proof 和实际选择的 provider 模式，只去掉查询首尾空白，不做语义去重。Need ID 不属于检索参数，同一批线索可复用于不同 Need，搜索历史分别归属。不同任务目录不共享缓存。

`--need` 可选；使用时须有已登记且处于 active 的任务。工具记录查询、缓存命中、首次登记的来源数与失败原因，但不自动改写查询或关闭 Need。结合 `status` 的 proof、缺口笔记和近期搜索，选择要补的缺口，避免换一种措辞重复搜同一事实。`search --need` 的失败会自动进入失败历史；工具外的尝试仍用 `failure` 登记。

`new_ref_count` 统计本次候选中此前未在任务内登记的 REF；各候选的 `seen_before` 标出是否重复出现。零新增只是换方向或回读的提示，不证明没有新证据；相同 URL 的更新内容可用 `read --refresh` 检查。缓存结果仍为 `unread_lead` 且不可引用，必须 Read 正文。

`local_only` 允许回看已有搜索缓存，仍拒绝新联网搜索、`search --refresh` 和 `search --retry-failed`。拒绝恢复时不消耗 Search、HTTP 或 Pivot，不改缓存。缓存保存的是历史线索，不能用它声称检索结果为当前最新。

## 批量发现

需要多条独立搜索时，先按待补缺口写一个 JSON 查询计划。`reason` 是简短的检索目的，不是推理过程。示例 `plan.json`：

```json
{
  "searches": [
    {"query": "citation evaluation entailment implementation", "reason": "找可回读的引用支持评估代码"},
    {"query": "citation evaluation human agreement error analysis", "reason": "找评估方法的人工对照与失败情况"}
  ]
}
```

```bash
research.py search-batch plan.json --workers 2 --limit 5
```

每条接受 `query`、`reason` 和可选的已登记 `need`。已知对象、需要特定材料时，可在该条同时填写 `target`、`artifact`、`proof`，沿用 `search` 的定向检索通道；这三个字段须一起出现。未知方法名时不预设 Target，找到候选后再定向搜索或直接 Read 核验。批次只执行计划，不调用另一个模型生成查询；简单查询仍用原来的 `search`。

已读正文可作为下一轮查询的输入：以顶层 `feedback` 代替 `searches`，填写原始查询、仍缺材料和带逐字摘录的相关/导航/误导段落，由工具核对术语出处并生成小批查询。格式与生成规则见 [search-feedback.md](search-feedback.md#执行反馈计划)。反馈及实际使用的历史快照保存在该批次搜索缓存中；原始查询仍保留，新候选仍需 Read。

计划限 1-6 条不同检索请求、64 KiB，最多 3 路并发，每条最多 12 个候选。查询文本相同但 Target、材料类型或 proof 不同的请求会分别保留、缓存；同一路由的重复查询仍拒绝。执行前校验整个计划；查询或 Target 涉及共享竞赛 scope 时按顺序执行，返回实际 `workers`。复用现有 provider、搜索预算、缓存和来源登记；缓存命中不额外消耗搜索额度。`--provider`、`--refresh` 或 `--retry-failed` 作用于整个批次；恢复时每条须已有兼容缓存，未缓存或旧格式项分别返回错误，完整项直接复用。两种更新选项互斥。

`results` 按计划顺序逐项保留 `reason`、REF、缓存信息或失败原因；`status` 为 `ok | partial | empty | error`。顶层 `completed` 表示返回了结果对象（含部分成功和空结果），`partial`、`empty`、`failed` 分别统计不完整渠道、成功空结果与错误；顶层状态为 `complete | partial | failed`，只要有渠道失败就标 partial，全部查询失败时 CLI 返回非零状态。`batch_id` 对应 `searches/<batch_id>.json`，保存查询目的及每次搜索的 ID，原始候选仍在各查询自己的缓存中。失败或空结果不会关闭 Need；成功项仍为 `unread_lead`，须 Read 后才能引用。先读取有价值的命中，再根据剩余缺口规划后续查询。

此处借鉴 OpenAI 官方 research_bot 的 [query/reason 计划](https://github.com/openai/openai-agents-python/blob/main/examples/research_bot/agents/planner_agent.py)和[并发执行及失败统计](https://github.com/openai/openai-agents-python/blob/main/examples/research_bot/manager.py)。本地实现继续保留原文与引用核验，查询数和报告长度由实际任务决定。

## 阅读与回查

```bash
research.py read REF_OR_URL_OR_LOCAL_PATH --term "目标术语"
research.py find REF --term "另一术语" --term "限制"
research.py find REF --term "另一术语" --term "限制" --offset 5 --limit 5
research.py open REF --start-line 20 --count 30
research.py read REF --refresh
research.py open REF --version SNAPSHOT_ID --start-line 20 --count 30
research.py status
```

- `read` 首次保存正文，后续默认用缓存；已知 URL 和用户文件可以直接读。
- `find` 和 `open` 只读当前任务已保存的快照，不联网。`find` 默认每页 5 个窗口，`--limit` 为 1–10；继续时保持相同术语、window 和 snapshot 版本，使用实际返回的 `next_offset`，示例中的 5 仅为示意。窗口按术语轮换，位置以 locator 为准。未匹配或 offset 已超过末页时返回空窗口；`open` 默认最多返回 40 行，可用 `--count` 调整到 120 行，输出另受字符上限约束。
- `read/find` 的 `match_counts` 是保存快照内各术语的逐字、不区分大小写匹配次数，`missing_terms` 列出零匹配术语；相邻命中共用窗口，`total_windows` 不等于匹配次数。`next_offset` 非空表示尚有窗口；`read` 无匹配时仍可返回开头预览，`find` 不返回无关开头。这些字段不证明网站全文覆盖、语义支持或未匹配信息不存在。
- `read --refresh` 重新读取来源，适合需要当前版本或本地文件已更新的情况。在线来源刷新受 `source_policy` 和联网读取预算约束。
- `content_scope` 区分 `full_text`、`abstract`、`metadata`、`readme`、`discussion`、`official_pages` 和 `local_file`。这些是实际读取范围，不表示该来源所有内容已被核验。检查 `truncated`；`false` 也不证明网站未在上游省略内容。
- `windows` 与 `locators` 一一对应。记录结论时保留 `snapshot_id`、`content_hash` 与行号/页码；换版本后用 `--version SNAPSHOT_ID` 复查旧摘录。旧版 `--version CONTENT_HASH` 继续可用，但相同正文对应多份元数据时只指向最早保留的那份，精确回读使用证据卡的 snapshot_id。
- 本地路径返回 `citation_path`；网页返回 `citation_url`。二者都需要实际摘录支撑相邻 claim。

固定语料的 `--corpus` 必须指向 `case-pack` 导出的 researcher `case.json`，其中只有 id/question/budget/sources，来源带内容 hash；不能直接传含 checklist/evaluator 的开发案例。由准备方先执行 `research.py case-pack /path/to/evals/CASE/case.json --output /path/to/researcher`，研究方使用 `/path/to/researcher/case.json`，不自行删除评审字段或跳过 hash 校验。

成功执行 `read S1 --corpus RESEARCHER_CASE_JSON` 后，`open S1`、`find S1` 和 `evidence S1` 可直接使用同一来源编号，无需重复传 `--corpus`。编号只解析当前 run 的成功 Read 记录，默认固定到最近一次成功 Read 的快照；Search 命中、失败或 pending 的 Read 不建立编号绑定。这些操作仅回查已保存快照，不再读原文件或消耗 Search/Read/联网预算。结果和证据卡仍使用 canonical REF、snapshot_id、原摘录与定位，缺失或损坏快照报错。

来源编号的 `--version SNAPSHOT_ID` 只能选择该编号此前成功 Read 的快照；使用旧 content_hash 时选择最近一次成功 Read 且正文 hash 匹配的快照。同文不同元数据需用 snapshot_id 精确区分。直接 `REF-…` 的最新快照与历史版本用法继续有效；普通 REF 刷新不会悄悄改变 `S1` 的语料快照。

新成功 Read 在 trace 中同时记录 content_hash，按正文 hash 选择历史版本时可跳过其他正文的记录。旧 trace 不自动补写；若缺少 content_hash 且扫描遇到缺失或损坏的快照，无法确认它是否为最新匹配版本，操作报错，可用已知 snapshot_id 精确回查。语料来源编号不得使用保留的 canonical REF 格式 `REF-` 加 16 位大写十六进制字符，case-pack 和语料读取会拒绝这种重名。

## 保存证据

```bash
research.py evidence REF --need N1 \
  --claim "实验只在一个站点完成" \
  --quote "The study was conducted at a single site." \
  --relation supports
```

`--quote` 必须逐字出现在已保存文本中，不能使用翻译、拼接片段或自己概括的文字。`--start-line` 可用于定位重复摘录；`--version SNAPSHOT_ID` 选择精确历史快照，也兼容旧版 CONTENT_HASH。成功后返回证据 ID、claim、原文摘录、来源、快照标识、内容 hash、位置、范围和是否截断。读取旧版快照时可能没有 snapshot_id；旧证据仍按 content_hash 回读，不自动改写证据 ID。

`--relation` 使用 `supports | refutes | context`，由调用者判断。`quote_verified=true` 只表示摘录能回到原文；不代表 claim 已通过语义核验。数字同现、词语重叠、模型自报 confidence 均不能单独证明蕴含。

对决定性结论，先确认实体、时间、单位和限定，再检查独立来源或反方。`status` 中的支持/反驳计数是证据卡数量，同源转载不会因此变成多份独立证据。

## 按结论回查与多来源核验

```bash
research.py review --need N1
research.py review --need N1 --claim "实验只在一个站点完成"
research.py review --need N1 --offset 5 --limit 5
```

`review` 离线读取已保存证据，不联网，也不修改任务状态。可按 Need、claim 或两者筛选，均为精确匹配。默认返回 5 张卡，`--limit` 为 1–10；按 `next_offset` 继续，`total` 和计数覆盖全部匹配结果，不只当前页。每张卡保留摘录、来源、范围、截断标记、历史 content_hash 和 locator，可用 `open --version` 回到原文。

- `same_text_ref_count` 和 `same_text_refs` 标出整个任务的证据中使用完全相同正文提取文本的 REF，最多列 10 个，另有截断标记。不同 URL、多个摘录和刷新前的历史证据都不会因此被删除。
- `distinct_content_count` 是匹配证据涉及的不同文本 hash 数；`source_ref_count` 是不同 REF 数。`status` 同时保留原始卡片数，并提供逐 Need、逐 relation 的 `evidence_content_counts_by_need`、正文重复组数 `duplicate_text_groups` 和支持/反驳并存的 claim 数 `mixed_relation_claims`。
- `claim_relations` 汇总同一 Need、完全相同 claim 文字下的调用者判断；`has_mixed_relations` 提示 supports 和 refutes 并存。它考虑整个任务中的该 claim，不受当前页大小影响；同一 Need 下不同 claim、不同 Need 下同名 claim 不会被直接当作一组。

相同 hash 仅证明保存文本相同，可能只是摘要、页面片段或共同截断前缀；不同 hash 也可能是改写、翻译、添加网页包装或同一来源的更新版本。因此这些计数不能当作独立支持数。支持/反驳并存也可能来自时间、条件、定义或调用者标注差异，不能自动宣判矛盾。先对照摘录和适用范围，再把裁决或未解决差异写入综合；工具不会自动关闭或重新打开 Need。

## 预算与停止

设置 `DEEP_RESEARCH_RUN_DIR` 后，即使不限额也会记录 `.adapter-counts.json`；CLI 的 `run_usage` 返回同一份累计计数。预算默认 0 表示不限制，负数无效；非零预算必须配置任务目录。旧 `DRBENCH_*` 同名变量优先，供评测入口兼容使用。

| 变量 | 统计与限制的单位 |
| --- | --- |
| `DEEP_RESEARCH_MAX_SEARCH` | 未缓存的逻辑 Search 次数；一次可访问多个渠道，不是 HTTP 请求数 |
| `DEEP_RESEARCH_MAX_READ` | 未缓存的在线 reader 尝试次数；同一命令触发备用 reader 时可增加多次 |
| `DEEP_RESEARCH_MAX_REQUESTS` | 实际 HTTP 请求尝试数；发送前原子占额，各重定向跳转也单独计数 |

失败尝试仍计入相应额度；预算阻止的操作只增加 `blocked`，不计为已发出的请求。`requests` 包括 AnySearch、搜索引擎、学术服务、GitHub、Kaggle 和在线读取；`request_errors` 记录请求、HTTP 或正文消费阶段失败；`request_seconds` 是各请求从发送到正文消费结束的累计秒数，含失败耗时与少量处理时间，多个并发请求可重叠，因此不是任务墙钟时间。`redirects` 记录重定向响应后的继续跳转决策，下一跳仍须通过 URL 与预算检查。缓存回读、本地文件、`open/find` 不增加 HTTP 额度。

直接读取本地文件也不消耗上述 `read` 额度；`status.sources_read` 统计已保存的不同来源，不能充当读取尝试计数。固定语料的 `search_calls/source_reads` 由 `--corpus` 独立计量并执行上限，证据在 `corpus-trace.json`；直接路径 Read 不受该语料预算约束。

这些计数仅覆盖脚本 HTTP 入口，不包括 DNS、依赖安装、agent 自带浏览器/搜索工具、远端聚合服务内部请求、模型 token 或费用。外部工具和墙钟/token/费用须从执行平台另行记录，未知项使用 null，不从请求数推算。

每轮只把会改变结论、缺口或下一查询的新增内容放入当前上下文。原文留在磁盘，按需回读；不要用不断追加全文替代证据综合。停止时核对全部必答项、重要冲突、剩余缺口与实际预算，不以来源数量或单个分支停滞判断整个任务完成。
