# Deep Research 升级调研与建议

调研日期：2026-10-15。对象：当前安装的 standalone skill，以及对应 GitHub 仓库的默认分支。交付物：有一手资料支撑的优化优先级、最小实现方案和验收标准。

结论：下一版应优先补齐 **可回读的原文、按研究缺口保存的任务状态、逐条结论与引用的支持关系**。现有 Need Map、Search → Read → Pivot、事实/推断/未知的区分值得保留。新增搜索渠道的优先级低于这三项。

## 研究问题与证据边界

| Need | 要解决的问题 | 证据与结果 |
| --- | --- | --- |
| N1 | 当前实现具体缺什么？ | 本地入口、阅读器、状态文件、分发目录；另用隔离 mock 检查两个行为。 |
| N2 | 同类 skill 和研究系统有哪些可迁移机制？ | 10 个项目的一手页面；其中 8 个项目进一步检查源码或流程文件。 |
| N3 | 如何保持轻量，并给出可实施的顺序？ | 将项目机制逐项映射到本地缺陷，给出分阶段合同。这里是设计推断。 |
| N4 | 如何判断升级确实有效？ | 读取 DeepResearch Bench 的 RACE/FACT 定义，提出固定语料和成对比较指标。 |

项目的能力描述和公开代码可以证明其设计，不能直接证明迁移到本 skill 后的收益。本轮未运行外部项目，也未进行本地新旧版本 A/B；没有用星数或作者自报成绩排列项目优劣。

## 当前实现：已确认的事实

1. **本地与发布版本存在差异。** 当前本地比 `origin/main` 多了 AnySearch 适配、依赖文件和 provider 测试等未提交内容。standalone 与 plugin 中的 `SKILL.md`、阅读脚本、安全参考文件不相同；plugin 缺少本地新增的依赖文件和 AnySearch 适配器。README 的“两份逐字一致”描述与当前工作区不符。发布升级时需要同时解决同步问题。
2. **阅读层没有落实 `local_only`。** [`_search_base`](../skills/deep-research/scripts/research.py#L402) 会检查来源策略；[`read_page`](../skills/deep-research/scripts/research.py#L1390) 和 [`_ref_read`](../skills/deep-research/scripts/research.py#L1581) 没有相同的检查。隔离 mock 设置 `local_only` 后，调用普通网页阅读入口仍进入了联网阅读器。诊断没有实际发送网络请求。
3. **白名单记录的是“成功读取过 URL”。** [`_ref_read`](../skills/deep-research/scripts/research.py#L1581) 在读取返回后加入 URL；没有记录该 URL 支持哪个 claim。用 `openalex_abstract` 模拟阅读返回，URL 同样进入白名单。摘要本身可以支持摘要明确陈述的内容，但目前缺少机制限制它被用来支持正文实现、消融或细粒度数字。
4. **原文没有形成可回读的资料层。** [`_pdf_text`](../skills/deep-research/scripts/search_candidate.py#L287) 合并页面文本，丢失页边界；[`_normalise_text`](../skills/deep-research/scripts/search_candidate.py#L283) 压平空白；[`_windows`](../skills/deep-research/scripts/search_candidate.py#L426) 返回若干字符串窗口。普通阅读路径没有保存完整提取文本、稳定行号或页码，也没有基于已保存原文的 `find/open`。重新查证通常需要重新下载。
5. **任务状态偏向候选与竞赛规则。** 已有 REF、引用列表和 Kaggle scope 状态，但 Need Map、通用覆盖状态、失败尝试和逐条证据没有统一的持久化合同。REF 映射只保留最近 256 项，引用白名单只保留最近 64 项；宽表或长研究可能丢失早先来源的可引用资格。
6. **普通使用缺少统一预算。** [`_reserve`](../skills/deep-research/scripts/search.py#L50) 的次数限制依赖 `DRBENCH_RUN_DIR` 与 `DRBENCH_MAX_*`；仅设置 `DEEP_RESEARCH_RUN_DIR` 不会启用这些预算。主流程的“连续两轮无新证据”主要靠提示词执行。
7. **现有测试覆盖面有限。** 当前两个测试文件覆盖 AnySearch 解析与 provider 路由，未覆盖来源读取范围、任务隔离、逐条引用支持、缓存回读和分发一致性。

## 相关项目：实际读到什么，如何取舍

以下链接均经过本轮成功读取。源码位置是本轮进一步检查的文件或函数；只阅读项目页面的条目另行注明。

| 项目 | 一手证据 / locator | 对本 skill 的可迁移做法 | 迁移边界 |
| --- | --- | --- | --- |
| [HyperResearch](https://github.com/jordan-gibbs/hyperresearch) | `core/fetcher.py::fetch_and_save`、`core/runs.py`、`core/citecheck.py`、bootstrap `SKILL.md` | 原文与提取文本保存到资料库；记录内容 hash；每次研究有 manifest、事件和恢复点；检查 sentence/citation 配对。 | 完整流程包含很多阶段与角色。其 citecheck 将数字同现或长词串重叠判为 `supported-mechanical`；这不能充分证明语义支持，本 skill 应将这类检查用于筛查。 |
| [Weizhena/Deep-Research-skills](https://github.com/Weizhena/Deep-Research-skills) | `research/SKILL.md` 与 `research-deep/SKILL.md`（Codex English 版本） | `outline.yaml` 保存条目，`fields.yaml` 保存字段；每个条目独立 JSON；续跑跳过已完成项；字段覆盖校验。 | 更适合宽表和批量对象。其确认步骤、逐项 agent 执行属于该项目的交互与调度选择；这里只采用条目/字段合同和恢复方式。 |
| [Jina node-DeepResearch](https://github.com/jina-ai/node-deepresearch) | `src/agent.ts` 的 `gaps`、`evaluateAnswer`、token tracker、动作开关 | 把待解问题作为可更新的缺口队列；答案不满足要求时回到搜索；限制预算与重复尝试。 | 自评不能替代正文证据。具体轮数、token 默认值和动作条件需要由任务预算决定。 |
| [LangChain Open Deep Research](https://github.com/langchain-ai/open_deep_research) | `deep_researcher.py::write_research_brief`、`compress_research`、`final_report_generation` | 用明确 brief 固定研究目标；原始记录和压缩结论分别保存；写报告时只取需要的研究结果。 | 可直接用文件和当前 agent 实现这些合同。采用 LangGraph 和独立压缩模型会增加运行依赖。 |
| [PaperQA2](https://github.com/future-house/paper-qa) | README；`src/paperqa/agents/tools.py::PaperSearch/GatherEvidence/GenerateAnswer/Reset` | 将找文档、从已有文档收集证据、生成答案拆成不同动作；证据按当前子问题筛选；保留页码引用与文档索引。 | 适合学术和本地语料；向量索引、嵌入模型和元数据服务是可选的后续扩展。本轮没有把 citation traversal 当作已核验的工具能力。 |
| [Local Deep Research](https://github.com/LearningCircuit/local-deep-research) | `focused_iteration_strategy.py`、`evidence/evaluator.py` | 策略与搜索器分离；为约束保存 claim、来源、quote 和支持/反驳关系；用上一轮结果生成后续问题。 | 当前检查的 strategy 中自适应提问和提前终止是可选开关；evaluator 会从搜索结果抽证据，本 skill 仍应坚持读取原文后才形成 finding。 |
| [GPT Researcher](https://github.com/assafelovic/gpt-researcher) | README；`gpt_researcher/skills/context_manager.py` | 按当前问题与章节选择相关上下文；研究和报告生成分开；压缩和复用已有研究内容。 | 适合借鉴上下文选择；“聚合更多来源”本身不能作为答案正确或完整的证据。 |
| [DeerFlow 2](https://github.com/bytedance/deer-flow) | README；`backend/docs/summarization.md` | 有界上下文摘要；恢复所需状态通过结构化 metadata 保存；原始工具文本与持久状态协议分开。 | 2.0 已是长任务 harness。这里只借鉴状态合同和上下文管理，完整应用框架超出此 skill 的需求。 |
| [STORM](https://github.com/stanford-oval/storm) | README 的 pre-writing / writing、multi-perspective question asking | 先研究与形成提纲，再围绕证据写作；用多视角补充遗漏。 | 适合长报告。多视角应来自会改变交付的缺口，避免为短事实查询增加固定阶段。 |
| [dzhng/deep-research](https://github.com/dzhng/deep-research) | README 的流程图与 depth/breadth 控制 | 用较少代码演示查询、learnings、后续方向之间的闭环。 | 固定深度和宽度是预算控制方式，不能单独作为研究完成标准。 |

## 跨项目综合

**持久资料与有界上下文可以同时实现。** HyperResearch 保存原文和资料索引，LangChain 分开保存 `raw_notes` 与 `compressed_research`，DeerFlow 用结构化 metadata 维持长期状态。这三种机制共同支持一个适合本 skill 的设计：资料留在磁盘，当前上下文只放与 open Need 有关的证据卡；需要核验时按 locator 回读。

**搜索、提证据、写答案需要各自的完成条件。** Jina 的 gap 队列和答案评价、PaperQA2 的不同工具动作、Weizhena 的字段覆盖校验，都把研究进度显式化。对应本地应分别记录：候选是否找到、原文是否读取、某个字段是否有证据、最终句子是否有支持，避免一个“读成功”状态承担所有含义。

**核验合同值得采用，自动通过规则需要更严格。** HyperResearch 的 citation 配对和 Local Deep Research 的结构化 Evidence 有参考价值；数字同现、snippet 抽取和模型自报 confidence 都可能缺少语义支持。当前白名单可保留为来源准入记录，另增加 quote、locator、适用范围与支持关系。

## 建议的最小实现合同

继续由当前 agent 负责理解任务、判断证据和综合答案。Python 工具负责可确定执行的读取、存储、索引、策略检查和结构校验。

```text
Need Map / checklist
    → 选择最高价值的 open gap
    → Search 或 Follow 已知链接
    → 保存 Source 原文与 metadata
    → 从本地原文 find/open
    → 记录 Evidence 与覆盖变化
    → 补缺口 / 查冲突 / 结束研究
    → 写答案并检查 claim/citation 配对
```

建议一开始使用 JSON/JSONL 和文本文件：

```text
runs/<run-id>/
  run.json              # 任务、Needs、覆盖、预算、停止原因
  sources.jsonl         # URL、版本、读取时间、正文范围、hash、body_path
  evidence.jsonl        # need_id、claim、source_id、quote、locator、relation
  events.jsonl          # 搜索、失败、读取、覆盖变化、冲突处理
  sources/<source-id>/body.txt
```

`Source` 至少区分 `full_text / abstract / metadata / unreadable`，记录提取是否截断；缺少日期、版本或页码时保留未知。`Evidence` 保存原文摘录和行号/页码，并区分支持、反驳、导航线索。推断继续使用 `I`，同时列出已读前提。

阅读入口支持 `REF`、用户给出的 URL、已读正文中的链接，以及本地路径，并记录发现方式。外部访问统一检查来源策略。对已缓存内容使用本地 `find/open`；需要刷新时显式记录新获取的版本。

## 实施顺序与验收

| 阶段 | 范围 | 完成条件 |
| --- | --- | --- |
| P0：来源和引用边界 | 全部联网入口执行策略检查；记录全文/摘要/错误壳/截断；证据卡关联 claim 与 locator；消除 64 项引用淘汰对既有证据的影响。 | `local_only` 下联网入口不可达；摘要仅支持其已读内容；错误壳不作为可用正文；65+ 来源仍可追溯。 |
| P1：原文保存与回读 | 保存提取文本和页面/行号映射；增加本地 `find/open` 与缓存命中；已知 URL 可直接读取并登记来源。 | 同一来源换关键词查证时可在断网下完成；证据摘录能返回稳定位置；无需重复搜索已知链接。 |
| P2：通用任务状态 | Need/checklist、失败记录、预算、覆盖变化和恢复点；为普通运行开放预算参数；将竞赛特定状态作为同一合同的扩展。 | 恢复后只处理剩余缺口；不同 run 不串来源或进度；单个分支低收益不导致其他必答项被跳过。 |
| P3：调度与上下文 | 按缺口选择查询；批量处理互相独立的来源；按章节提取证据；对决定性结论做定向冲突检查。 | 能解释每次追加检索要补哪个缺口；上下文不重复堆积全文；报告中关键推断列出证据前提。 |
| 发布门槛 | standalone/plugin 同步，安装依赖完整，统一校验两份分发内容。 | 从两种分发入口都能导入脚本并运行；内容一致；运行缓存和 lock 文件不进入发布包。 |

P0 到 P1 是最值得先做的一批：它们同时解决实际检查出的边界缺口，并为后续恢复、调度和审计提供共同基础。主 `SKILL.md` 只保留决策规则与工具入口，详细 schema 和场景流程按需读取。

## 如何衡量收益

[DeepResearch Bench](https://deepresearch-bench.github.io/) 将报告质量（RACE）与检索、有效引用和引用准确性（FACT）分开评估。这支持本项目分别衡量覆盖与证据质量的做法；本轮没有执行该 benchmark。

第一批采用小型固定语料，覆盖中文、英文、长文、摘要回退、来源冲突、转载去重、宽表和恢复。对相同任务、相同语料、相同预算比较当前版与升级版：

- 必答字段有直接证据的比例，以及仍为 `I` / `?` 的项。
- 关键 claim 的引用支持率；可准确回到原文位置的比例。
- 将转载误当独立证据、将摘要误当全文的次数。
- 每次搜索/读取新增的有效证据数、重复下载数、总时间与工具调用数。
- 断网回读和恢复成功率；两种分发入口的一致性。

确定性的路径、状态与结构检查可自动化；语义支持由明确 rubric 和人工/agent 审阅确认，单独记录判断依据。先证明覆盖和引用质量改善，再评估是否需要嵌入检索、更复杂的去重或额外执行角色。

## 调研状态

模式：Deep。成功读取 12 个不同 URL：10 个项目、1 个 benchmark 官方站点、1 个用于发现候选的 GitHub topic 页面。10 个项目均以一手项目页面为依据；其中 8 个项目另外读取了源码或流程文件。搜索使用 AnySearch；STORM 候选不足时工具同时使用 direct 回退。

本地行为诊断：2 项，均在隔离目录中使用 mock，未修改实现文件。停止原因：四项 Need 已有直接证据或明确局限；追加同类项目不会改变当前的第一批实施优先级。

以上为升级前的只读调研结果；后续实施记录如下。现有工作区改动未提交或推送。

## 首批实施记录

用户授权 clone 相关项目并借鉴源码后，10 个项目均已检出到工作区的 `deep-research-references/<owner>/<repo>`。使用浅克隆保存当前源码，各仓库 commit 和路径见同级参考目录中的 `manifest.json`。

落地机制与代码参考：

| 本地改进 | 借鉴的源码机制 | 当前实现 |
| --- | --- | --- |
| 正文保存与历史追溯 | HyperResearch `core/fetcher.py` 的保存、内容 hash，`core/runs.py` 的独立研究目录 | 新增 `scripts/workspace.py`，按任务和来源保存正文及 metadata；刷新保留历史 content hash。 |
| 按问题提证据 | PaperQA2 `GatherEvidence`，Local Deep Research 的 Evidence 对象 | 新增 `evidence` 命令，保存 Need、claim、逐字摘录、范围和 locator；工具验证摘录存在，支持/反驳关系由 agent 判断。 |
| 有界上下文与按需回读 | GPT Researcher 的上下文选择，LangChain 的原始/压缩笔记分离，DeerFlow 的有界持久状态 | 新增离线 `find/open/status`；正文留在磁盘，CLI 返回有限摘录和行号/PDF 页码。 |
| 任务进度与缺口意识 | Jina 的 gap 队列，Weizhena 的条目/字段覆盖合同 | 更新 skill：任务隔离、复用恢复目录、逐 Need 记录证据；一个分支停滞后仍核对全部 checklist。尚未实现自动通用 Need 调度器。 |

另修复了未缓存阅读绕过 `local_only`、REF/引用列表淘汰旧来源、普通运行预算不生效，以及 GitHub 根目录文件不能转 raw URL 的问题。摘要、元数据、README、讨论和正文分别标记范围；下载超过上限会拒绝并关闭响应，文本提取截断明确标记。

两份分发已同步，新增 `scripts/sync_skill.py`；`--check` 检查资源一致性，缓存、lock 和字节码不复制。主 SKILL 链接到按需读取的 `workspace-and-evidence.md`。

验证结果：31 项单元测试通过；11 个 CLI 场景覆盖 standalone/plugin、离线回读、历史版本、摘录拒绝与策略拦截；公开 GitHub README 的真实联网读取、转离线缓存回读和逐字摘录记录通过；两份 skill 的格式校验通过。

当前边界：已保存的是提取后的正文，不是原始 HTML/PDF 二进制；工具不自动判定语义蕴含，也不根据词串或数字同现判定 claim 正确。通用 Need 调度、转载独立性识别和成对质量评测仍是后续工作，尚未声称最终研究报告质量已有量化提升。

## 第二批实施：任务进度与恢复

沿用本地 HyperResearch `core/runs.py` 的幂等初始化与原子保存机制，以及 Jina `src/agent.ts` 的未解问题队列思路，为现有 workspace 增加可选 `task.json`，不引入它们的模型调用或完整执行框架。

- `init` 保存 Deliverable、Target 和约束，同合同重复初始化保留已有状态，异合同拒绝覆盖。
- `need` 登记可独立核验的完整 checklist，保存 proof、优先级、open/partial/blocked/answered、结论或缺口以及关联证据。问题与证明标准不能被同 ID 的不同要求静默替换。
- `status` 显示剩余项，按优先级建议下一项可继续的 Need；blocked 项仍保留。新增来源、证据和失败均不会自动结束 Need。
- `failure` 保存实际失败尝试；`stop` 默认 incomplete，显式 `--complete` 要求非空清单且所有已登记项 answered。`resume` 保留已答项、完整失败历史和上次停止原因。
- answered 需要结论笔记以及属于同一 Need 的 supports/refutes 证据；仅有 context、不存在或其他 Need 的证据不能用于收尾。初始化前的证据保持兼容，孤立 Need ID 在 status 提示；初始化后新增证据须使用已登记 ID。

新增 10 项定向测试，其中一项通过独立 CLI 进程完成登记、离线阅读、证据记录、部分完成、失败、拒绝提前完成、停止与恢复以及最终完成。全套 41 项测试通过；standalone/plugin 的 16 个资源文件一致，两份 skill 格式校验及 diff 空白检查通过。

当前进度仅由显式登记与调用者判断驱动。工具无法发现漏登记的用户要求，不自动执行研究、不验证语义支持，也没有根据证据数量证明报告质量提升。逐事实 E/I/? 和跨来源综合仍由研究者维护；自动检索决策、转载独立性及成对质量评测未在本轮扩展。改动仍保留在本地工作区，未提交或推送。

## 第三批实施：搜索复用与 Need 归属

检查 Jina `src/agent.ts` 的 allKeywords 历史和 dedupQueries 使用方式后，为现有检索入口增加任务内的精确请求缓存。保留 agent 根据正文和缺口决定查询的方式，不引入模型或嵌入服务进行语义去重。

- 相同查询、limit、Target、artifact、proof 和实际 provider 模式复用保存结果；仅忽略查询首尾空白，保留大小写及引号内容，避免误合并不同问题。
- `search --refresh` 显式更新候选，返回原始检索时间与缓存命中信息。失败不缓存，也不覆盖旧成功结果；已有缓存可在 local_only 下回看，刷新和未缓存检索仍被阻断。
- `search --need ID` 将查询、缓存命中、来源新增数与错误归入已登记 Need 的历史，失败自动加入既有失败记录。重复结果可跨 Need 复用，但历史分别归属；status 返回总次数及最近 10 条，完整历史留在任务文件。
- `seen_before` 和 `new_ref_count` 揭示是否反复发现同一批 REF，提示回读或转向剩余缺口。它们不表示语义新颖性、来源独立性或证据质量，也不会自动改变 Need 状态。
- 搜索缓存继续输出 unread_lead 和不可引用标记，必须 Read 后才能进入引用白名单；不同任务的缓存和历史保持隔离。

新增 10 项测试，全套 51 项通过。预算测试将额度设为 1，确认重复查询仅消耗一次搜索额度，强制刷新会触及额度限制而旧缓存仍可使用。另通过真实 CLI 进程验证离线复用、Need 归属与刷新拒绝；测试未发起外部搜索。两种分发的 17 个资源文件一致，两份格式校验和 diff 空白检查通过。

本轮只证明确定性复用、归属与边界行为。未运行报告质量 benchmark，未实现语义去重或自动判断无收益轮次；缓存是历史候选，需要时由调用者明确刷新。改动继续保存在本地工作区，未提交或推送。

## 第四批实施：按结论回查与重复正文提示

参考本地 PaperQA 的两处机制：`src/paperqa/docs.py` 以内容 hash 作为默认文档键，`src/paperqa/agents/tools.py` 分别统计 relevant paper 数与 evidence 数。本轮利用既有 SHA-256 快照和证据卡，将正文数量与卡片数量分开显示，不把任一计数当作独立支持数。

新增离线 `review`，支持按 Need、完全相同的 claim 文字筛选和分页。每张卡保留摘录、locator、内容范围、截断标记及历史 content_hash；同时标出整个任务中使用完全相同保存文本的 REF，以及同一 Need 和 claim 下支持/反驳并存的记录。默认返回 5 张卡，上限 10；重复 REF 列表另限 10 项并明确标记截断。原始证据不删除，不自动改写任务进度。

`status` 增加证据涉及的不同文本数、逐 Need/逐关系的文本数、跨 REF 重复正文组数和支持/反驳并存的 claim 数，原有卡片计数继续保留。重复分组使用证据绑定的历史版本，来源刷新后仍可识别旧证据中的相同文本。

新增 8 项测试，全套 59 项通过，覆盖镜像 URL、同源多摘录、部分或截断文本、不同 Need/claim 的关系隔离、历史版本、分页与边界、停止后的只读回查以及离线 CLI。standalone/plugin 的 18 个资源文件一致，两份格式校验及 diff 空白检查通过。

边界：内容 hash 仅证明保存的提取文本相同；改写、翻译、网页包装和共同上游仍需研究者核查。不同正文不能证明独立，支持/反驳并存也不能自动判为语义矛盾。本轮未运行报告质量 benchmark，改动未提交或推送。
