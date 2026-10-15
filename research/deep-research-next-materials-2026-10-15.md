# 下一轮 Deep Research 改进资料

调研日期：2026-10-15。对照版本：已推送的 `4be2a34`。本轮检索、读取一手资料并检查源码，新增六个本地参考仓库；没有修改 skill 实现、运行外部项目或执行付费模型评估。

建议顺序是：**先建立更难、可诊断的评测案例，再增加可追溯的语义审阅记录，最后按缺口反馈改善检索。** 当前三个固定语料案例的新旧版本均为平局且评分到顶，尚不能证明报告质量提升，见[实测记录](evaluation-2026-10-15/README.md)。继续扩大来源数量或重复这三个案例，不能解决这个验证缺口。

## 研究问题与证据边界

| 问题 | 本轮读取的直接证据 | 对本项目的意义 |
| --- | --- | --- |
| 怎样发现长报告的具体缺陷？ | DeepResearch Bench II、ResearchRubrics 的实际评分代码；DEER 论文及实现 | 将任务要求拆成可验证项目，并为每项保留报告中的证据位置，而非仅给整体分数。 |
| 怎样超出“引用存在”的结构审计？ | ALCE 的联合/逐引用支持检查；DEER 的论断分类、隐式引用与核验结果 | 区分是否需要证据、所给证据是否支持、是否存在未覆盖的子论断。 |
| 怎样区分检索与综合的问题？ | BrowseComp-Plus 的固定语料、相关性标注与检索接口；OpenScholar 的反馈补检索循环 | 分开衡量找没找到证据、读没读到证据、答案是否正确使用证据。 |

代码和论文证明这些项目采用了相应机制；下文迁移建议是设计推断，未证明能提高本 skill 的分数。论文中的性能提升也不是本地复现结果。arXiv 摘要页只按摘要处理；Bench II、DEER 及两篇补充论文另行读取了 PDF 正文。

## 六个可直接参考的项目

### 1. DeepResearch Bench II：细粒度诊断与评测隔离

- [项目](https://github.com/imlrz/DeepResearch-Bench-II)；[论文正文](https://arxiv.org/html/2601.08536v2)，第 1 页说明 132 个任务、9,430 项细粒度 rubric，分为信息召回、分析和表达三个维度。
- 已查源码：`run_evaluation.py:70` 的逐项评分合同、`validate_batch_result:256`、`aggregate_scores.py::compute_dimension_averages:32`。
- 当前代码要求逐 rubric 给 `score/reason/evidence`，并使用三种分值区分满足、未提及、使用禁止引用的来源。后者可以用于发现答案引用被隔离的参考材料；它不等于自动核验来源中的事实。
- 适合迁移：在 `evaluation.py` 的既有四维评分之外，为案例登记可核验的任务专属项目，要求对应报告摘录或明确的缺失理由；将 evaluator 的参考答案与 researcher 可读语料分开。评分器返回缺项或重复项时应拒绝结果。
- 边界：报告包含匹配数字不等于引用支持成立。代码为 Apache-2.0；数据逐任务继承源文许可，`DATA_LICENSE` 列有 CC BY、CC BY-NC 和 CC0，不能把代码许可视作全部任务的许可。

### 2. ResearchRubrics：按标准收集证据，再合并长文判断

- [项目](https://github.com/scaleapi/researchrubrics)。
- 已查源码：`src/evaluate_rubrics/evaluate_single_report.py::EvaluationResult:51`、`chunk_document:157`、`src/prompts/chunk_prompt_template.txt`、`synthesis_prompt_template.txt`、`src/calculate_metrics/calculate_compliance_score.py:31`。
- 实现为每项标准收集分块证据，再形成整篇报告的判断；结果结构保留理由、模型自报置信度、token、费用、耗时与成功状态，汇总时按标准权重计算。
- 适合迁移：对超出上下文的报告按 rubric 汇集相关段落，并保留原始 `line/end_line`、报告 hash 和未覆盖区间。记录实际评审成本，失败或未评项目单列，不能默认为通过。
- 边界：其分块使用字符数估算 token，不能直接替换本项目已有的 UTF-8 字节预算；分块证据合并也可能漏掉跨章节冲突。模型自报 confidence 不作为正确性证据。代码为 MIT。

### 3. DEER：分类处理未引用论断，保留核验解释

- [项目](https://github.com/hanjanghoon/DEER)；[论文全文](https://arxiv.org/pdf/2512.17776v5)，第 1–2 页介绍报告维度与报告级论断核验。
- 已查源码：`info_verification/claim_processor.py::Claim:14`、`claim_verifier.py::verify_claim_from_url_async:51`、`eval_metrics.py::compute_claim_metrics:7`、`compute_citation_metrics:54`。
- 论断结构包含报告位置、分类理由、是否涉及数字、显式/隐式引用和内部交叉引用。指标代码区分直接引用、回溯同段/前文引用、结构性总结、常识/观点及来源未知的事实；核验输出保留逐论断解释，并把读取/执行错误单列。
- 适合迁移：给 `reporting.py` 的 `unbound_text` 候选增加可选的分类与语义审阅记录；记录原报告位置、证据卡、支持范围和理由。读取失败应标为未能核验，不能当作事实被反驳。一个句子含多个事实时，应分别核对覆盖情况。
- 边界：隐式引用只能提出待确认的绑定，不应自动把相邻引用传播到整段。其 verifier 会将 `conflict` 合并为 `not_supported`；本项目若需保留矛盾诊断，应分别保存“反驳”“证据不足”和“执行错误”。代码为 MIT；数据另有非商用及公开分发限制，不复制其任务/参考答案到公开 evals。

### 4. ALCE：联合支持与逐引用贡献

- [项目](https://github.com/princeton-nlp/ALCE)。
- 已查源码：`eval.py::compute_autoais:301`，尤其是 `joint_entail`、逐引用及去掉某份引用后的支持检查，以及 `citation_rec/citation_prec` 输出。
- 它分别检查引用集合能否支持一个句子，以及每份引用是否独立支持句子或对联合支持有必要贡献，能识别部分多余引用。没有引用或引用越界的句子不能通过相应支持检查。
- 适合迁移：对现有证据绑定增加可选的审阅合同，分别记录“应当有证据的论断得到多少覆盖”和“实际引用有多少提供支持”；保留多证据联合推断的前提，不能要求每份来源都独立证明完整结论。
- 边界：NLI 模型输出仍是近似判断，需用人工标注的反例校准，不能直接升级为确定性通过条件。该实现以英文句子和数字引用为主，本项目继续使用现有 Markdown 解析与 EV 标识，不照搬旧正则。代码为 MIT。

### 5. BrowseComp-Plus：把检索评估与答案评估拆开

- [项目](https://github.com/texttron/BrowseComp-Plus)。
- 已查材料：`README.md:5` 的固定语料设计、检索评估说明、`topics-qrels/qrel_evidence.txt` 与 `qrel_golds.txt`、`search_agent/openai_client.py::_search:92` 和 `_get_document:125`。
- 项目以约 10 万篇固定文档控制检索环境，分别提供证据文档与含最终答案的 gold 文档标注；README 提供 recall/nDCG 的检索评估入口。没有下载完整语料、索引或运行排行榜评测。
- 适合迁移：先自建小型但有干扰项的固定语料，要求研究流程实际执行搜索、阅读和综合。对同一模型分别记录证据文档召回、成功阅读、实际引用、报告正确性及调用成本，区分检索失败和使用证据失败。
- 边界：这种困难事实检索任务不能单独证明长报告的综合质量；应与任务专属 rubric 搭配。代码为 MIT，外部语料的具体授权另行核对。

### 6. OpenScholar：由缺口反馈触发有界补检索

- [项目](https://github.com/AkariAsai/OpenScholar)。
- 已查源码：`src/open_scholar.py::get_feedback:262`、`edit_with_feedback_retrieval:329`、`run:606`，尤其是 639–696 行的反馈、追加检索、去重、重排与编辑流程。
- 实现区分可直接编辑的反馈与需要额外检索的问题；后者在 Semantic Scholar 路径开启时查询补充论文，再用新增段落编辑答案。该循环只遍历前三条反馈，并保存修改及部分费用信息。
- 适合迁移：将审阅发现的缺口回写到已有 Need，由具体缺口产生新 query；按新增有效证据和剩余预算决定是否继续，修改后重查引用。反馈和执行记录持久化，使恢复时知道哪些问题已尝试。
- 边界：自我反馈不等于已证实错误；其去重使用正文前缀与标题、部分编辑采用长度条件，不照搬为本项目的正确性标准。其大规模论文索引及训练依赖也不是迁移所必需。代码为 Apache-2.0。

## 两篇补充论文

1. [Deep Research, Shallow Evaluation（2026）](https://arxiv.org/pdf/2603.06942v1)。已读 PDF 的摘要、研究设计和逐指标评价发现：整体 A/B 偏好适合系统级比较，但不足以证明每个质量维度都可靠；逐指标标注与评审者的领域经验会影响结论。对本项目的建议是保留盲法偏好，同时报告每项事实支持、完整性及综合判断的理由与分歧，不将一个总体胜负当作各模块改进的证据。
2. [ReSum: Unlocking Long-Horizon Search Intelligence via Context Summarization](https://arxiv.org/pdf/2509.13313)。本次读取的 PDF 为 v3，第 1–2 页说明将交互历史压缩为可继续研究的状态，并指出通用摘要工具的局限。可研究为长任务保存“已确认结论、未答 Need、失败尝试、来源/快照指针”的恢复摘要，但原始证据继续留在磁盘；摘要不变成新的来源证据。本项目已有证据包和恢复状态，此方向优先级低于评测与语义审阅；论文的长程探索目标也不取代既有预算和停止条件。

## 下一轮可实施范围与验收建议

以下为 2026-10-15 检索时的建议。2026-10-16 已按顺序落实为困难开发案例、研究者材料导出、逐论断审阅与双指标、有界补查、固定语料调用记录和失败阶段诊断；命令合同见 [reporting-and-evaluation.md](../skills/deep-research/references/reporting-and-evaluation.md)。这些实现和接口回归不等于已证明报告质量提升，仍需实际盲评和独立 held-out 样本。

| 优先级 | 最小改动 | 需要证明的行为 |
| --- | --- | --- |
| P0 | 增加有干扰项、参考答案隔离的困难案例，并设置细粒度 rubric | 可区分“有引用但不支持”“一句中只有部分事实有支持”“版本/计价单位不一致”“证据不足却写成否定结论”；公开答案不会被 researcher 检索到。 |
| P1 | 在结构 audit 之外保存可选的逐论断语义审阅记录 | 可回到报告、证据卡与 snapshot；区分未评、支持、部分支持、反驳、证据不足和读取错误；不能借此自动关闭 Need。 |
| P1 | 分开统计支持覆盖与引用贡献 | 增加无关引用不会改善支持得分；两份必要证据共同支持的推断不因单份证据不充分而误判。 |
| P2 | 将审阅缺口关联到搜索历史和有界重查 | 下一次 query 对应明确缺口；重复结果或无新增有效证据不会无限消耗预算。 |
| P2 | 对检索和写作分别归因 | 同语料、同模型、同预算下，能说明失败是未检索到、未读到、读后漏用还是误用，保留实际调用与成本记录。 |

新的评测仍应包含中文和英文、冲突与数值边界，并保留 held-out 案例；不能只用开发时已知反例宣称总体质量提升。先运行可复算的小规模比较，再决定是否引入外部 NLI/评审模型依赖。

## 本地参考版本

路径均相对于工作区的 `deep-research-references/`，采用浅克隆与稀疏检出，仅取本轮需要的源码目录；完整树可从 Git 获取。没有执行仓库中的安装、运行或环境初始化指令。

| 仓库路径 | 固定 commit | 本轮主要检出范围 |
| --- | --- | --- |
| `princeton-nlp/ALCE` | `246c476a4edfc564266b7346b6e29ef4861ae937` | 根目录的评估代码、README、LICENSE |
| `AkariAsai/OpenScholar` | `0e9b8fb912273d3dae39e593da86e4f6d3bf8de1` | 根目录、`src/` |
| `scaleapi/researchrubrics` | `2dc80e2d4c38ddd80439517c259d93c6954b193f` | 根目录、`src/` |
| `imlrz/DeepResearch-Bench-II` | `b38f360603db9531b102aef8c166cedb8509b6f6` | 根目录的评估代码、许可和任务文件；未摘录任务答案 |
| `hanjanghoon/DEER` | `88580fc4dc502974a6748ca58edddf129b8a2044` | 根目录、`info_verification/`、`prompts/`、`script/` |
| `texttron/BrowseComp-Plus` | `046949032b0328319cc9a02663a759ec601d9402` | 根目录、`search_agent/`、`scripts_evaluation/`、`topics-qrels/` |

工作区的 `deep-research-references/manifest.json` 已登记上述版本。调研中的一次 `allenai/OpenScholar` 直读返回 404，随后通过检索确认并读取了作者仓库 `AkariAsai/OpenScholar`；失败地址没有作为资料依据。
