# 原文回读与证据记录

用于多轮研究、长文定位、断点续研和关键 claim 核验。简单事实查询只需保留最小证据链。

## 任务目录

开始时把 `DEEP_RESEARCH_RUN_DIR` 设置为当前任务的独立目录。下文的 `research.py` 使用 SKILL.md 中的 `uv run --with-requirements ...` 前缀执行。

```text
<run-dir>/
  .deep-research-refs.json       # Search 和直接 Read 登记的来源
  .deep-research-citations.json  # 此任务成功读取的网页 URL
  .deep-research/
    task.json                   # 可选：任务合同、Need 进度、失败记录和停止原因
    searches/<search_id>.json   # 查询参数、检索时间和候选结果；不等于正文证据
    sources/REF-.../
      metadata.json             # 最近快照的元数据
      <content_hash>.json       # 历史快照元数据
      <content_hash>.txt        # 保存的正文提取结果
    evidence.json               # 经原文摘录校验的证据卡
```

原文快照指提取后的文本，不是网页原始 HTML 或 PDF 二进制。元数据包括来源、读取时间、内容 hash、内容范围、是否截断、正文位置；PDF 还保存页码对应的字符区间。历史快照保留，刷新不会覆盖旧证据使用的正文。

## 任务进度与恢复

多轮研究先登记任务和完整 checklist。每个可独立核验的必答问题或字段使用一个 Need ID；数量不受主 SKILL 中 1–4 个研究方向的限制。简单查询可以跳过初始化，原有 search/read/evidence 用法继续有效。

```bash
research.py init --deliverable "比较两种方案并给出建议" --target "A 与 B 的当前版本" \
  --constraint "保留用户要求的全部比较字段"
research.py need N1 --question "A 的运行成本" --proof "当前版本的计价单位与费用" --priority 1
research.py need N2 --question "B 的运行成本" --proof "当前版本的计价单位与费用" --priority 1
research.py need N2 --state partial --note "已读计价规则，尚缺存储费"
research.py failure --need N2 --action "读取官方存储费页面" --reason "页面返回 403"
research.py status
```

- `init` 重复执行同一合同会保留进度；交付物、Target 或 constraints 不同则拒绝覆盖，提示使用新任务目录。
- `need` 首次登记需要 question 和 proof；已有问题不能被同 ID 的不同问题覆盖。优先级为 1–5，1 最高，默认 3；Need 状态为 `open | partial | blocked | answered`。更新状态必须写 note，说明结论或剩余缺口。
- 保存支持或反驳该 Need 的证据卡后，使用 `need N1 --state answered --note "具体结论及适用范围" --evidence EV_ID` 收尾。可重复 `--evidence` 关联多张卡。工具拒绝不存在、属于其他 Need 或仅为 context 的收尾证据；不会自动判定这些证据是否充分满足 proof。
- 读取来源、增加证据或记录一次失败，都不会自动关闭 Need。`failure` 保留实际尝试和失败原因，不代替无收益轮次判断。
- `status` 给出所有 Need、`remaining_needs` 和按优先级选择的 `next_need`；已 blocked 的项保留在缺口清单中。失败与搜索的总次数和各自最近 10 条记录随状态返回，完整记录留在 task.json。搜索次数包含缓存复用与失败，不等于联网次数。

```bash
research.py stop --reason "读取预算耗尽；N2 尚缺存储费"
research.py resume
# 全部已登记 Need 为 answered，且检查过完整用户要求和冲突后：
research.py stop --complete --reason "全部必答项和重要冲突已核对"
```

`stop` 默认标记 incomplete；`--complete` 要求至少一个 Need 且全部 answered。`resume` 重新激活任务并显示剩余缺口，保留已答项、失败历史和上次停止原因。停止后修改 Need 进度须先 resume。单个分支停滞不能让其他必答项消失。

`completion_ready` 只检查已登记清单和显式进度。工具无法发现漏登记的用户要求，也不验证结论的语义正确性；不要把它当作报告质量评分。已有证据先于 init 创建时仍保留，`unregistered_evidence_needs` 提示尚未登记的 Need；初始化后新增证据必须使用已登记 ID，避免拼写错误。

跨来源综合、推断前提及逐事实的 `E / I / ?` 仍可记在 `research.md`，格式见 [evidence-ledger.md](evidence-ledger.md)。使用 task.json 时，以其为任务进度依据，无需在笔记中重复维护一套状态。

## 搜索复用与下一步选择

```bash
research.py search "B storage pricing" --need N2
research.py search "B storage pricing" --need N2 --refresh
```

相同查询与检索参数默认复用当前任务保存的结果，返回 `cache_hit`、原始 `searched_at` 和 `search_id`，不再消耗联网搜索额度。`--refresh` 用于需要最新结果或明确重试 provider 的情况；失败不会覆盖旧的成功结果，失败本身不会缓存。成功但为空的结果会缓存，此时换查询，或在确需重新检索时 refresh。

缓存键区分大小写、limit、Target、artifact、proof 和实际选择的 provider 模式，只去掉查询首尾空白，不做语义去重。Need ID 不属于检索参数，同一批线索可复用于不同 Need，搜索历史分别归属。不同任务目录不共享缓存。

`--need` 可选；使用时须有已登记且处于 active 的任务。工具记录查询、缓存命中、首次登记的来源数与失败原因，但不自动改写查询或关闭 Need。结合 `status` 的 proof、缺口笔记和近期搜索，选择要补的缺口，避免换一种措辞重复搜同一事实。`search --need` 的失败会自动进入失败历史；工具外的尝试仍用 `failure` 登记。

`new_ref_count` 统计本次候选中此前未在任务内登记的 REF；各候选的 `seen_before` 标出是否重复出现。零新增只是换方向或回读的提示，不证明没有新证据；相同 URL 的更新内容可用 `read --refresh` 检查。缓存结果仍为 `unread_lead` 且不可引用，必须 Read 正文。

`local_only` 允许回看已有搜索缓存，仍拒绝新联网搜索和 `search --refresh`。缓存保存的是历史线索，不能用它声称检索结果为当前最新。

## 阅读与回查

```bash
research.py read REF_OR_URL_OR_LOCAL_PATH --term "目标术语"
research.py find REF --term "另一术语" --term "限制"
research.py open REF --start-line 20 --count 30
research.py read REF --refresh
research.py open REF --version CONTENT_HASH --start-line 20 --count 30
research.py status
```

- `read` 首次保存正文，后续默认用缓存；已知 URL 和用户文件可以直接读。
- `find` 和 `open` 只读当前任务已保存的快照，不联网。`find` 未匹配时返回空窗口；`open` 默认最多返回 40 行，可用 `--count` 调整到 120 行，输出另受字符上限约束。
- `read --refresh` 重新读取来源，适合需要当前版本或本地文件已更新的情况。在线来源刷新受 `source_policy` 和联网读取预算约束。
- `content_scope` 区分 `full_text`、`abstract`、`metadata`、`readme`、`discussion`、`official_pages` 和 `local_file`。这些是实际读取范围，不表示该来源所有内容已被核验。检查 `truncated`；`false` 也不证明网站未在上游省略内容。
- `windows` 与 `locators` 一一对应。记录结论时保留 `content_hash` 与行号/页码；换版本后用 `--version` 复查旧摘录。
- 本地路径返回 `citation_path`；网页返回 `citation_url`。二者都需要实际摘录支撑相邻 claim。

## 保存证据

```bash
research.py evidence REF --need N1 \
  --claim "实验只在一个站点完成" \
  --quote "The study was conducted at a single site." \
  --relation supports
```

`--quote` 必须逐字出现在已保存文本中，不能使用翻译、拼接片段或自己概括的文字。`--start-line` 可用于定位重复摘录；`--version CONTENT_HASH` 选择历史快照。成功后返回证据 ID、claim、原文摘录、来源、内容 hash、位置、范围和是否截断。

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

`DEEP_RESEARCH_MAX_SEARCH` 和 `DEEP_RESEARCH_MAX_READ` 为当前任务设置联网调用次数上限，默认 0 表示不限制。失败尝试也会消耗其相应调用额度，缓存回读不会。`DRBENCH_*` 变量为已有评测入口保留。

每轮只把会改变结论、缺口或下一查询的新增内容放入当前上下文。原文留在磁盘，按需回读；不要用不断追加全文替代证据综合。停止时核对全部必答项、重要冲突、剩余缺口与实际预算，不以来源数量或单个分支停滞判断整个任务完成。
