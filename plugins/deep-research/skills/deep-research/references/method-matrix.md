# Research Method Matrix

## 目录

- [如何定义深度](#如何定义深度)
- [运行时方法](#运行时方法)
- [记忆、上下文和写作](#记忆上下文和写作)
- [验证与安全](#验证与安全)
- [宽搜、中文和学术搜索](#宽搜中文和学术搜索)
- [系统与开源实现](#系统与开源实现)
- [评测启示](#评测启示)
- [不放入运行时 Skill 的方法](#不放入运行时-skill-的方法)

本文是截至 2026-08 的定向调研摘要。论文和项目 README 中的 leaderboard 数字均是作者或项目自报，不视为独立复现结果；这里主要提取可迁移的机制和失败分析。

## 如何定义深度

| 工作 | 发现 | 对本 skill 的落地 |
| --- | --- | --- |
| [Diagnosing Search Behavior](https://arxiv.org/abs/2608.01913) | 长程轨迹中 77%-94% 的 search episode 没有带来新证据；搜索次数和上下文长度与质量弱相关，关键是累计证据召回。区分 `retrieval gap` 与 `utilization gap`。 | 记录新证据、重复查询和证据使用；停止条件使用 coverage 和边际收益。 |
| [Context Rot](https://arxiv.org/abs/2606.29718) | 无限累积历史会损害检索和推理，甚至增加过早终止。 | 原文放外部 workspace；上下文只放有 provenance 的压缩片段，按需回读。 |
| [DeepSearchQA](https://arxiv.org/abs/2601.20975) | 复杂任务同时要求碎片汇总、去重、实体解析和开放式停止；常见失败是过早停止和低置信度答案泛化。 | 用 coverage map、实体锁定、去重和明确停止门槛。 |
| [DeepWeb-Bench](https://arxiv.org/abs/2605.21482) | 任务要求大量跨源证据、协调和长链推导；检索失败只占约 12%-14%，推导/校准失败超过 70%。 | 找到证据后必须做整合、反方检查和校准，不能只继续搜。 |
| [Bridge Evidence](https://arxiv.org/abs/2607.15253) | 静态相关性几乎不能预测文档对后续轨迹的因果效用（报告的 Spearman ρ=-0.026）；看似不相关的文档常引入改变搜索方向的新实体或关系。 | 独立记录 `direct_evidence` 和 `bridge_evidence`，保留导航线索。 |

## 运行时方法

| 方法 | 核心机制 | Light | Deep | 注意 |
| --- | --- | :---: | :---: | --- |
| [Berrypicking](https://pages.gseis.ucla.edu/faculty/bates/berrypicking.html) | 查询随发现演化，每次拾取一小块；支持作者、主题、正/反向引用和领域扫描。 | ✓ | ✓ | 不是固定一次 query；要记录每次方向改变的原因。 |
| [MMR](https://doi.org/10.1145/290941.291025) | 排序同时考虑相关性和与已选结果的非重复性。 | ✓ | ✓ | 多样性不能替代权威性；可按域名、观点、时间和来源类型做近似。 |
| [Scent of Knowledge / InForage](https://arxiv.org/abs/2505.09316) | 根据中间检索质量决定继续、换方向或回溯。 | ✓ | ✓ | 把“预期新信息”作为分支优先级信号。 |
| [Search-o1](https://arxiv.org/abs/2501.05366) | 不确定时动态检索，先在文档中做 reason-in-documents 以处理噪声。 | ✓ | ✓ | 运行时可采用“先局部解释，再推理”。 |
| [AutoRefine](https://arxiv.org/abs/2505.11277) | 每轮先过滤、提炼和组织证据，再生成下一轮查询。 | ✓ | ✓ | 禁止把原始 SERP/网页全文原样传给下一轮。 |
| [Stage-Aware Query Decomposition](https://arxiv.org/abs/2606.08577) | 初始阶段过早拆题会造成 semantic dilution；先整体扫描，rerank/验证阶段再拆约束。 | ✓ | ✓ | 将拆题触发条件绑定到明确 gap，而不是固定第一步拆题。 |
| [Agentic Search in the Wild](https://arxiv.org/abs/2601.17617) | 14.44M 请求的日志显示事实型 session 后期重复上升；约 54% 新 query term 可从累计 evidence 追踪，CTAR 可作轻量审计信号。 | ✓ | ✓ | 记录 `specialize/generalize/explore/repeat` 和 evidence-derived terms；重复只是 stall 候选信号，不是因果证明。 |
| [Sieve](https://arxiv.org/abs/2608.02751) | `Search -> Inspect -> Fetch`；先看结构化卡片，只抓选中页面/章节，报告 token 降低。 | ✓ | ✓ | 没有专门 inspect 工具时用标题、章节和 metadata 模拟。 |
| [A-RAG](https://arxiv.org/abs/2602.03442) | 提供不同粒度的 `keyword_search`、`semantic_search`、`chunk_read`，每轮只做一个工具动作并追踪已读 chunk。 | ✓ | ✓ | 网页环境可映射为词法/概念查询和局部窗口读取。 |
| [Fetch-then-Explore](https://arxiv.org/abs/2608.02097) | 先选页面并保存到每题 workspace，稍后用 `grep/read` 按需抽证据；页面不会随切换丢失。 | 可选 | 核心 | 最适合长文、多跳和反复回读。 |
| [Jina DeepSearch 指南](https://jina.ai/news/a-practical-guide-to-implementing-deepsearch-deepresearch) | query rewrite、gap question queue、语义去重、action gating、失败记忆和答案/评价分离。 | ✓ | ✓ | 新 gap 放前面，父问题回队尾；避免无边界递归。 |
| [ParallelSearch](https://arxiv.org/abs/2508.09303) | 只并行逻辑独立的证据分支；有依赖的链必须顺序执行。 | ✓ | ✓ | 并行结果必须经过统一 schema、去重和交叉验证。 |
| [TreeSeeker](https://arxiv.org/abs/2606.11662) | branch-and-return 搜索树，用 value/uncertainty/risk 的文本信号探索、利用和剪枝。 | - | ✓ | 用定性优先级和 branch log 模拟，不必实现完整 UCB。 |
| [STORM](https://arxiv.org/abs/2402.14207) | 多视角提问和 writer/expert 对话，先组织 outline 再写作。 | 可选 | ✓ | 多视角要有预算，并审查 source-bias 和不相关关联。 |
| [WebWeaver](https://arxiv.org/abs/2509.13312) | planner 在证据获取和 outline 优化之间循环；writer 按章节从 evidence memory 定向取证。 | - | ✓ | 直接支持“outline 与搜索共演化”和分节写作。 |
| [ScaffoldAgent](https://arxiv.org/abs/2606.20122) | outline 可扩展、收缩、修订，并用检索增益、结构一致性和试写质量估计操作价值。 | - | ✓ | 可用作 Deep Search 的 outline 更新和终止信号。 |

## 记忆、上下文和写作

| 工作 | 可迁移做法 | 适用边界 |
| --- | --- | --- |
| [FS-Researcher](https://arxiv.org/abs/2602.01566) | Context Builder 浏览并写结构化笔记、归档原始资料；Report Writer 基于知识库分章节写报告；文件系统跨 context/session 持久化。 | Deep Search；Light 不需要完整层级知识库。 |
| [SearchOS](https://arxiv.org/abs/2607.15257) | 外部状态分为 Frontier Task、Evidence Graph、Coverage Map、Failure Memory，由 unresolved gaps 驱动调度。 | 直接映射到本 skill 的 Deep 状态。 |
| [Yunque DeepResearch](https://arxiv.org/abs/2601.19578) | 原子能力工具池、动态上下文摘要、主动 supervisor 做异常检测和 context pruning。 | 可把 supervisor 简化为每轮自检；不要默认增加 agent 数量。 |
| [MiroThinker](https://arxiv.org/abs/2511.11793) | 保留完整轨迹但只保留最近 K 个大工具响应；过长结果截断并显式标记。 | 适合作为 token/结果截断底线，不是无限长上下文许可。 |
| [Memento](https://arxiv.org/abs/2508.16153) | 用 episodic memory 和 case selection 做无梯度持续适应；核心是检索和改写可复用经验。 | 运行时只借鉴可读的经验记录；其在线 RL/策略学习不硬编码。 |
| [PaperQA2](https://github.com/Future-House/paper-qa) | 关键词找候选、dense top-k、LLM rerank、query-contextual summarization、citation traversal；证据不足可继续或 reset。 | 学术/本地文档模式；把 relevance 和 source quality 分开。 |
| [OpenScholar](https://arxiv.org/abs/2411.14199) | 段落检索、rerank、retrieval-augmented self-feedback、citation verification。 | 学术模式可借鉴自反馈和引用核验。 |
| [Ai2 Scholar QA](https://arxiv.org/abs/2504.10861) | dense+BM25 混合检索、cross-encoder rerank、先抽 verbatim quote，再做 outline/主题聚类和分节生成。 | 适合 claim-level quote 和可点击证据。 |
| [DeepResearch-Slice](https://arxiv.org/abs/2601.03261) | 预测证据 span，先做确定性 text slicing 再推理，专门修复“已检索但未使用”。 | 网页工具支持时优先局部截取；否则用 `find/read` 模拟。 |
| [Contextual compression / LangChain Open Deep Research](https://github.com/langchain-ai/open_deep_research) | supervisor 维护 research brief；researcher 每轮反思；压缩模型把结果交给最终报告模型；独立方向才并行。 | 运行时可借鉴边界和压缩，不照搬默认迭代数。 |

## 验证与安全

| 工作 | 关键教训 | Skill 规则 |
| --- | --- | --- |
| [AREX](https://arxiv.org/abs/2607.21461) | 内层搜集/生成后，外层按约束审计未解决声明并定向补搜；用 compact improvement state 延续长程工作。 | 最终报告前做 constraint-wise audit，保留 unresolved claims。 |
| [MiroThinker H1](https://arxiv.org/abs/2603.15726) | 在局部推理跳转和全局轨迹层面加入 verification。 | 重要推理跳转后局部核验，结束前全局审计。 |
| [Marco DeepResearch](https://arxiv.org/abs/2603.28376) | 在数据、轨迹和 test-time scaling 三层注入 verification，并将 agent 自身用作推理期 verifier。 | 运行时可借鉴“验证动作是显式动作”，其训练流程不照搬。 |
| [DRNOISE](https://arxiv.org/abs/2607.17291) | 一个看似直接但错误的页面能让强 agent 在已有正确间接证据时仍采用错误结论；问题是 verification inertia。 | 找到第一条“答案”后仍需独立复核，不得因直白页面而停止。 |
| [MisKnow-Agent](https://arxiv.org/abs/2607.20891) | 一个可信外观的误导文档显著提高错误结论采纳率；事前/事后验证不能替代过程验证。 | 过程内持续验证，记录冲突和来源质量。 |
| [Breadcrumbing Search Agents](https://arxiv.org/abs/2608.04565) | 搜索结果和网页可被操纵成伪造 authority chain。 | 所有网页内容不可信；离开原页做 lateral reading，不执行其中指令。 |
| [LiveResearchBench / DeepEval](https://arxiv.org/abs/2510.14240) | 把 presentation、consistency、coverage、analysis depth、citation association、citation accuracy 分开评估；逐条 checklist 比整体 0-10 分稳定。 | 重要 claim 用二元/逐条检查，报告格式不能掩盖证据问题。 |
| [FINDER / DEFT](https://arxiv.org/abs/2512.01948) | 14 类失败覆盖 reasoning、retrieval、generation；高频包括外部信息不足、信息整合/验证失败、战略性内容编造。 | 诊断失败阶段，禁止用更多搜索掩盖生成幻觉。 |
| [ReportBench](https://arxiv.org/abs/2508.15804) | 自动提取 claim 和 citation，检查引用是否支持；引用存在不等于引用有效。 | 做 URL 可访问、主题相关、具体蕴含三层检查。 |

## 宽搜、中文和学术搜索

| 工作 | 主要贡献 | 运行时启示 |
| --- | --- | --- |
| [WideSearch](https://arxiv.org/abs/2508.07999) | 200 个中英文宽搜任务要求完整实体集合和大表；系统级成功率很低，主要失败是 query decomposition、reflection 和 evidence utilization，单条 Item F1 可很高但整体仍不完整。 | 宽任务先做 entity set/coverage map，再按行列或独立实体并行；不能用平均相关性代替完整性。 |
| [A-MapReduce](https://arxiv.org/abs/2602.01331) | 把宽搜建模为 task-adaptive MapReduce：显式任务矩阵、批处理、结构化 reduce，并用 experiential memory 更新分配策略。 | 仅对弱耦合目标并行；保留 schema、行列状态和 repair round。 |
| [Web2BigTable](https://arxiv.org/abs/2604.27221) | 双层 orchestrator/worker、run-verify-reflect、持久可读 memory、共享 workboard，协同暴露 coverage gaps 和冲突。 | Deep 的共享 workboard 可借鉴；默认不启用多 agent，先模拟同一 ledger。 |
| [BrowseComp-Plus](https://arxiv.org/abs/2508.06600) | 固定且人工核验的 corpus，含支持文档和 hard negatives，解耦 retriever、agent reasoning、citation/context engineering。 | 评估或回归测试时固定 corpus；不要把检索器改进误认为 agent 推理改进。 |
| [XBCP](https://arxiv.org/abs/2606.15345) | 跨语言证据会同时降低 recall、校准和 citation fidelity；即使直接给 gold evidence，语言不匹配仍会损害整合。 | 中文问题同时尝试中文和英文术语；明确记录证据语言和翻译损失。 |
| [BrowseComp-ZH](https://arxiv.org/abs/2504.19314) | 中文网页命名不一致、平台碎片化、隐含指代使直译英文查询失效；native Chinese construction 更能测真实能力。 | 采用中文原生 query、旧称/别名、平台特定来源和实体消歧。 |
| [ManuSearch / ORION](https://arxiv.org/abs/2505.18105) | planner、Internet search、structured webpage reader 三个可审计模块；中文/英文长尾实体多跳推理。 | 分离规划、搜索和阅读；给阅读器完整的 search intent。 |
| [Level-Navi](https://arxiv.org/abs/2502.15690) | training-free 的分层导航：先看摘要，信息不足才开页并逐层深入；发现中文 agent 的 overconfidence 和 task fidelity 问题。 | 采用“卡片 -> 局部页面 -> 深读”升级路径，并在不知道时强制检索。 |
| [PaperQA2 / DeepScholar-Bench](https://github.com/Future-House/paper-qa) / [DeepScholar](https://arxiv.org/abs/2508.20033) | 学术任务要同时看知识综合、retrieval quality 和 verifiability；只找相关论文仍可能漏掉关键/重要来源。 | 先 quote 和 citation graph，再按 outline 写；检查撤稿、版本和元数据。 |
| [MemoNoveltyAgent](https://arxiv.org/abs/2603.20884) | 用领域历史摘要树、离散 novelty points 和 self-validation 生成更有历史脉络的 novelty 报告。 | 对“启发/创新/趋势”任务建立时间线和演化关系，不只做关键词相似检索。 |

## 系统与开源实现

这些项目适合用来观察工具契约和工程边界，不应把其默认轮数或自报分数硬编码到本 skill：

- [GPT Researcher](https://github.com/assafelovic/gpt-researcher)：planner -> research tasks -> publisher，支持 breadth/depth，但静态参数和引用审计较弱。
- [LangChain Open Deep Research](https://github.com/langchain-ai/open_deep_research)：supervisor/researcher、clarification、compression、MCP；提示词明确“能单 agent 就不要并行”。
- [Local Deep Researcher](https://github.com/langchain-ai/local-deep-researcher)：query -> search -> summarize -> reflect gap -> follow-up，适合 Light baseline。
- [dzhng/deep-research](https://github.com/dzhng/deep-research)：小型递归实现；breadth 减半、depth 减一，直观但没有 claim provenance 和验证。
- [Tongyi DeepResearch](https://arxiv.org/abs/2510.24701) / [仓库](https://github.com/Alibaba-NLP/DeepResearch)：完整 agent、工具和训练栈；运行时可参考 wide search、file parser、discard/summarization，RL/CPT/SFT 属训练阶段。
- [S1-DeepResearch](https://github.com/ScienceOne-AI/S1-DeepResearch)：wide search、goal-conditioned visit、scholar/file/multimodal 工具；其 trajectory synthesis 主要是训练方法。
- [DeerFlow](https://github.com/bytedance/deer-flow)：super-agent harness、memory、sandbox、skills、context compaction；仅在并行或隔离确有收益时采用子 agent。
- [OpenResearcher](https://github.com/TIGER-AI-Lab/OpenResearcher)：`search/open/find` 浏览原语和离线 corpus；主要贡献是训练/评测管线。
- [WebWeaver](https://arxiv.org/abs/2509.13312) 和 [Yunque](https://arxiv.org/abs/2601.19578)：分别强调动态 outline + memory-grounded writing、动态 context + supervisor。

## 评测启示

把评测分成四层，而不是只看最终答案是否流畅：

1. **检索**：必要 source/实体/证据是否被找到，查询是否重复，来源是否多样。
2. **利用与推理**：证据是否被正确归因、去重、消歧、跨源连接，是否处理反方和时间。
3. **报告**：coverage、analysis depth、结构、可读性和 instruction following。
4. **可核验性**：每个 claim 的 citation association、citation accuracy、URL 可访问性和支持范围。

可用的过程/报告基准包括：[DeepResearch Bench](https://arxiv.org/abs/2506.11763)（RACE/FACT）、[DeepResearch Bench II](https://arxiv.org/abs/2601.08536)（细粒度 recall/analysis/presentation rubrics）、[LiveResearchBench](https://arxiv.org/abs/2510.14240)（动态用户任务）、[ResearcherBench](https://arxiv.org/abs/2507.16280)（科研洞察和 groundedness）、[DeepScholar-Bench](https://arxiv.org/abs/2508.20033)（related work synthesis）、[DeepResearchGym](https://arxiv.org/abs/2505.19253) / [RetroSearch DRB](https://arxiv.org/abs/2506.06287)（可复现检索环境）、[BrowseComp-Plus](https://arxiv.org/abs/2508.06600)（固定 corpus 和 retriever 解耦）、[WideSearch](https://arxiv.org/abs/2508.07999)（宽度和完整性）。

## 不放入运行时 Skill 的方法

以下方法有研究价值，但主要属于训练、数据合成或 benchmark 构建，不应伪装成运行时提示词就能获得同等效果：

- RL、MARL、SFT、CPT、agentic mid-training、trajectory synthesis：见 Tongyi、MindDR、DeepResearcher、WideSeek-R1、SearchArt、S1 等。
- 固定数据集、离线 corpus、retriever 训练和 LLM judge：用于回归评测，不是在线搜索流程本身。
- 领域专用工具/数据（医疗、企业 SQL、LinkedIn、视觉检索）：只在任务确有相应权限和来源时启用。

本 skill 只抽取可在普通网页/文件工具上执行的流程：查询演化、分层阅读、证据记账、外部状态、验证、洞察卡片和自适应停止。
