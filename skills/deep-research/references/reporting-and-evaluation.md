# 章节、引用审计与报告对比

长报告需要分章写作、论文需要沿引用链补缺口，或修改 skill 后需要比较真实产出时使用。简单事实查询无需启用全部流程。以下 `research.py` 使用主 SKILL 的 uv 前缀，任务目录和来源读取规则见 [workspace-and-evidence.md](workspace-and-evidence.md)。

## 提纲随证据修订

先登记完整 Needs，再把它们分配到章节。`perspective` 表示本章要解决的判断角度，例如部署成本或复现条件；只在能改变研究问题时填写。

```bash
research.py section costs --title "运行费用" --need N1 --need N2 \
  --perspective "相同负载下的实际月费" --order 1
research.py outline
# 正文揭示旧价格只适用于历史版本，调整章节但保留全部原始 Needs：
research.py section costs --title "当前费用与历史差异" --need N1 --need N2 \
  --reason "已读生效日期，需要解释历史报价差异"
```

`outline` 显示章节内未答项、没有证据的项、尚未分配章节的 Needs 和修订记录。`remaining_needs` 同样包含已答但证据变化后待复核的项，`needs_requiring_review` 单独列出这些项，与任务 status 一致。修改已有章节须写 reason；省略 perspective/order 会保留原值，显式空 perspective 可清空。移出章节的 Need 仍在任务清单和未分配列表中，不能通过改提纲掩盖缺口。章节归属不意味着问题已经回答。

## 原文证据与综合分开保存

使用 `evidence` 保存原文卡后，再写调用者自己的综合：

```bash
research.py synthesis costs --summary "当前报价按处理量和存储量分别计费；旧价格已失效。" \
  --evidence EV_ID_1 --evidence EV_ID_2 \
  --inference "按给定负载代入两项单价，计算总月费。" \
  --limitation "税费和超出给定负载的折扣未核验。"
research.py packet costs --limit 5
research.py packet costs --offset 5 --limit 5
```

例中的 `EV_ID_1/2` 需替换为实际返回的 `EV-...`。`synthesis` 不改写原文和证据卡，只接受本章节 Needs 的证据。综合中的推断须列出已读前提和计算，不能把调用者总结变成来源原话。

`packet` 是可离线使用的章节 JSON 证据包：章节、Need 问题/证明标准/状态、综合、摘录、snapshot_id、正文 hash、位置和范围。默认每页 5 张卡、最多 10 张，返回 JSON 的 UTF-8 大小限制为 48,000 字节；字节预算可能让实际条数小于 limit，此时标 `page_limited_by_bytes`，必须沿返回的 `next_offset` 继续，不能按请求 limit 自行跳页。

它遍历本章节全部证据，包含未被综合选中的反驳与 context。首页 `relation_counts` 统计全章各关系，即使反驳在后续页也可看见；`needs_without_evidence` 提示没有卡片的 Need。这些计数不能证明来源独立、语义冲突或证据充分。沿 `next_offset` 读完相关页后再定稿。

完整上下文和全部证据会另存到 `packet_path` 指向的 `.deep-research/packets/<hash>.json`，分页复用同一份内容快照。过长的 claim、来源地址或上下文预览会明确标 `preview_truncated`；`context_preview_truncated` 表示 Need/综合/列表也有缩略，此时按字段回读完整 JSON。`total_needs`、`remaining_need_count`、`needs_requiring_review_count`、`needs_without_evidence_count` 和 `new_evidence_count` 始终是全量计数，不能把缩略后的列表长度当作总数。原文摘录保持完整，缩略 claim/地址不会改写证据卡；最终引用地址仍从实际 Read 结果或完整证据取值，不使用缩短的预览地址。

章节定义、Need 状态或相关证据发生变化，`synthesis_stale` 会提示重审；`new_evidence_ids` 标出综合之后新增的卡。该标记不能判断结论是否仍成立，不自动重写摘要或改变 Need 状态。

`packet` 的 `needs_requiring_review` 独立反映任务结论的复核要求。刷新综合可以清除摘要过期提示，但不能替代 `need --state answered --note ...` 对证据范围的重新确认；确认 Need 后，基于旧 Need 记录的综合也需要重审。两种提示都不判断语义正确性。

文件分别保存在 `.deep-research/outline.json`（含修订前后内容和原因）、`syntheses/<section>.json` 与 `evidence.json`；刷新综合不会删除原始证据。章节证据包按内容 hash 保存，新证据或综合变化会产生新文件，旧文件可继续追溯；`basis_hash` 可用于辨认分页期间依据是否变化，分页期间有新增材料时应重新检查首尾页。

## 草稿逐句绑定与审计

在草稿关键事实和推断旁放证据 ID，保留对推断的明确表述：

```text
该研究在一个站点招募了 12 人。[EV-实际ID]
基于单站点、无对照设计，现有材料不足以证明多站点因果效果。[EV-实际ID1][EV-实际ID2]
```

```bash
research.py audit report-draft.md --limit 10
research.py audit report-draft.md --offset 10 --limit 10
```

审计保存完整 `.deep-research/audits/<report_hash>.json`，CLI 返回有界预览和分页。长句或大量引用会标 `preview_truncated`，完整内容在审计文件中。`next_offset` 以实际返回条数为准。

检查项包括：不存在的 EV、历史快照 hash 和逐字摘录定位失效、context/refutes 关系需要复核、未绑定文本、未 Read 的网页 URL，以及 URL 与所绑定证据来源不一致。网页引用支持 Markdown 内联、完整/折叠/简写参考式链接和裸 URL；仅检查实际使用的参考链接。链接按 Markdown 的 URL 编码规则比较，最终输出仍复制成功 Read 返回的原始引用地址。未绑定文本只是审阅候选，反驳卡也可能恰当地支持对冲突的讨论。

Markdown 块与链接由标准解析器处理，句子划分仍是启发式，**所有语义结论始终为 `not_assessed`**。同段换行保留为一个候选范围，句后跨行引用绑定到前句；表格按数据行合并单元格，不把表头作为事实。标题、代码块、未使用的参考定义不作候选；内联代码、转义示例或字符实体中的 EV 不会补齐正文证据。预览规范化 Markdown，`line/end_line` 指向原始段落或表格行范围，精确原文仍在草稿中。缩写、多句共同引用、HTML 和隐含论断仍需人工检查。审计无结构问题也不能证明报告正确；逐项核对摘录是否支持相邻事实、数字单位、因果和适用范围，不用词串或数字重合自动通过。

最终面向用户的引用仍使用成功 Read 返回的原始 `citation_url`，本地文件用 `citation_path` 与位置；不要输出虚构的证据链接。可在完成语义复核后移除内部 EV 标记，并保留草稿和审计作为追溯依据。

## 可选的逐论断语义审阅

需要保存可追溯的审阅判断时使用 `claims`；它不改变上述 `audit` 的 `not_assessed` 合同，也不调用评审模型。

```bash
research.py claims report-draft.md --schema-version 2
# 编辑返回的 template_path，保存为单独的 judgments.json：
research.py claims report-draft.md --judgments judgments.json
research.py claims report-draft.md --offset 5 --limit 5
```

v2 模板为 `judgments.v2.template.json`，显式含 `schema_version=2`，绑定报告和候选清单 hash。每条有 `id/candidate_id/claim/span`，默认整句一个跨度。复合句可拆成多条，使用不同 id、相同 candidate_id；`span=[start,end]` 是该候选 `sentence` 的 Unicode 字符索引，左闭右开，`claim` 必须与对应切片逐字相同。索引针对规范化 Markdown 的候选文本，不是原草稿位置、UTF-8 字节或屏幕字符宽度。中文与 emoji 各按代码点计数，组合字符可能占多个索引。

同一候选的跨度必须从 0 连续覆盖到句末，不能遗漏、重叠、重复或越界；连接词、空白、标点和引用标记也须归入某段。每个候选都需要完整分割，不能靠重复正确片段代替未审部分。`candidate_lengths` 保存原 inventory 的长度，不能通过修改它缩短覆盖范围。程序验证文本覆盖，审阅者仍须判断每段包含的全部事实。`kind` 选 `unclassified / factual / inference / structural / opinion`，分类需要理由。

旧调用在没有已保存审阅且未指定版本时继续创建 v1 模板 `judgments.template.json`。有 judgments 时按其声明版本处理，缺少版本按 v1；显式参数冲突会拒绝。无输入时默认沿用最新审阅的版本；显式 `--schema-version 2` 只选择新模板，不迁移已有审阅。返回的 `schema_version` 表示当前展示的审阅，`template_schema_version` 表示模板，两者可能不同。生成模板或回读都不更新 latest；旧模板、旧记录与旧 hash 保留，保存新审阅才更新 latest。

事实与推断的 `verdict` 选 `not_assessed / supported / partial_support / refuted / insufficient_evidence / read_error / execution_error`。`rationale` 写简短的可核验依据，`gap` 写具体缺口；不要求内部思维链。部分支持、反驳、证据不足和执行/读取错误必须有 gap；读取失败不能写成事实反驳。只有支持或部分支持时填写 `support_mode=direct|joint`，其余保持 `not_assessed`。

v2 同时填写 `support_origin=source_stated|derived`：前者表示来源直接陈述，后者表示据前提推导；其他 verdict 保持 `not_assessed`。它与 `support_mode` 独立：单来源也可以推导，多来源也可以共同陈述。`derived` 必须提供以下 `derivation`，前提均须为本行已确认、快照有效且贡献为 necessary/helpful 的证据；公式、单位非空，假设为显式列表（可空）。其他 origin 的 derivation 为 null。程序不执行公式，也不自动判断推导或假设是否成立。

```json
{
  "premise_evidence_ids": ["EV-实际ID1", "EV-实际ID2"],
  "formula": "2 * 3 = 6",
  "units": "dollars/unit * units = dollars",
  "assumptions": []
}
```

`bindings` 保留候选中的全部显式 EV。每项含 `evidence_id`、`implicit`、`confirmed`、`contribution`、`rationale`。未出现在候选中的绑定必须标 `implicit=true`，提议阶段保持 `confirmed=false, contribution=not_assessed`；审阅确认后才纳入。引用贡献为 `necessary / helpful / irrelevant / contradicts / not_assessed`，非未评项须有理由。`joint` 至少需两份有效且已确认的必要/有帮助证据；审阅者说明每份前提的作用，无需每份单独蕴含整句。脚本只校验合同和逐字摘录，不能核实这些判断。

原指标保留，标明 `unit=claim_row`：`support_coverage` 为完全支持的事实/推断行数除以所有事实/推断行数，部分支持、未评或读取错误不算完全支持。`semantic_assessment_coverage` 单列已完成语义判断的行比例；`unclassified_claims` 不能省略。`citation_contribution_rate` 为必要/有帮助的绑定数除以已评贡献的绑定数；无关和矛盾引用进入分母，未评另计。增加无关引用不会增加支持覆盖。

`candidate_metrics` 标明 `unit=candidate`，一个候选最多贡献一个分母。有 factual/inference 段即 eligible，全部此类段已评且无 unclassified 段才算 assessed；全部此类段 supported 且无 unclassified 段才算 fully supported。因此拆成更多正确行不会冲淡同一句未支持的后半段。`unclassified_candidates` 与 `fully_classified_candidates` 另列，不能用分类为结构/观点来证明语义正确。`source_stated_supported_candidates` 只计事实/推断段全部来源陈述的完整支持候选；有 derived 段的完整支持候选计入 `derived_supported_candidates`，混合候选只进后者。

v2 的 `coverage_verified=true` 只表示跨度分割已验证；v1 保持 false，候选支持与分类计数、比率及新增来源类型计数为 null，不据旧 kind/mode 猜测。`candidate_count` 仍可列出候选总数。所有比率在没有分母时为 null；这些都是审阅者判断的汇总，不能自动判定报告正确。

完整记录按内容 hash 保存在 `claim-reviews/<report_hash>/<review_hash>.json`，内含证据卡、历史 snapshot、位置和理由，旧记录不覆盖。CLI 返回有界预览与 next_offset；长推导按实际 UTF-8 字节继续缩略并标 `preview_truncated`，公式、假设与精确摘录回读 review_path。回读发现贡献证据损坏时，保存的原始 verdict 不变，但 `effective_verdict=read_error`，不再计入有效支持。此命令不关闭或重开任何 Need。

## 从审阅缺口进行有界补查

```bash
research.py gap report-draft.md --claim C1 --need N1 --max-attempts 3
research.py search "精确对象 当前缺失字段 官方说明" --gap GAP_ID
# 读候选并保存新 evidence 后：
research.py gap-result GAP_ID --outcome progress --reason "新原文补充了当前版本的前提" --evidence EV_ID
# 没有新增有效证据时：
research.py gap-result GAP_ID --outcome no_progress --reason "仍只有旧版转载"
```

仅已审阅的不完整/错误事实或推断可建 gap，并明确关联已登记 Need。v2 gap 同时绑定 `candidate_id/span`，重复创建同一报告、claim、跨度和 Need 的 gap 不重置历史与上限。改变分段后应停止旧 gap，为剩余未支持段建新 gap；沿用行 ID 不能缩小原缺口。默认最多 3 次、可设 1–6 次；同一规范化 query 不重复执行，每次搜索后必须先记录结果，连续两轮 no_progress/error 或达到上限后停止。错误也耗费尝试；现有全局联网预算仍生效。

新 REF 不等于新证据：progress 要求同 Need、有效历史快照的新卡，并排除仅改写 claim 或相同正文/摘录/范围的转载；是否有助于缺口仍是调用者判断。`resolved` 还要求最新审阅对原报告、claim 文本及 v2 候选跨度显式支持并使用新增证据；它只结束 gap，不替代 Need 的人工确认。`stop` 加理由可结束分支，重写报告后应停用旧 gap。审阅记录变化后须重跑 `gap` 复核依据；已支持的 claim 不能继续生成补查。搜索和失败同时进入原有 Need 历史并带 gap_id，记录都留在任务目录。

## 沿论文前后引用补证据

当某个 Need 需要方法起源、后续复现或反例时，针对已知论文扩展一页：

```bash
research.py follow ARXIV:1706.03762 --direction references --limit 5 --need N1
research.py follow DOI:10.1038/nature14539 --direction citations --limit 5 --need N2
```

支持 DOI、arXiv ID/URL、Semantic Scholar ID/URL，以及能解析到上述地址的 REF。使用 Semantic Scholar 公共 graph API；每页默认 10、最多 20 条，`next_offset` 指向后续页。结果只登记为 `unread_lead`，不会进入引用白名单，也不会自动关闭 Need。先 Read 匹配目标与证明标准的候选；引用数量、年份或元数据不能证明复现成功。

每次未缓存的追踪调用消耗一次逻辑 search 预算；实际 HTTP 请求及跳转另计入 requests，失败也计入。相同请求可缓存回放，`--refresh` 强制重取。`local_only` 只允许缓存回放，拒绝新页和刷新。带 `--need` 的成功与失败写入任务历史。遇到限流或不可用记录缺口，改用现有公开检索入口，不循环重试同一页。

## 固定语料的盲法成对评估

重复比较与双评委运行先读 [预先冻结的配对运行](paired-evaluation.md)：冻结版本/配置与预定分母，独立保存两位评委、失败/缺评和分歧。以下 `compare/score` 适合单对临时比较。

`evals/pricing`、`trial`、`methods` 各有一个合成案例，分别检查版本/计价、摘要/转载/因果边界、基准可比性/缺失字段。它们用于小型回归评估，不能代表真实网页检索难度或完整 benchmark。

`evals/pricing-hard-zh` 与 `trial-hard-en` 增加中英文干扰语料、复合论断、计量/版本冲突及“未报告不等于没有”。它们是公开的开发案例；独立 held-out 案例应另行封存且不用于调试，不把公开案例改名为未见测试集。

`evals/multihop-long-en` 通过六份材料串联旧别名、归档编号、确切制品、长报告后段更正及现行计费。它检查是否把正文旧摘要、晚发转载、CPU 节省、完整费用与延迟边界混为一谈，并要求可复算派生值和保留缺失保障字段。长文约 17 KB，总语料约 26 KB；仍是公开开发案例，不能代表真实长报告、网络召回或未见任务。

新增案例用 `case.json`：

```json
{
  "id": "unique-case",
  "question": "完整用户任务",
  "budget": {"network_calls": 0, "source_reads": 4},
  "checklist": [{"id": "N1", "requirement": "必须回答的字段和证据标准"}],
  "sources": [{"id": "S1", "path": "source.txt"}]
}
```

对两个版本各开独立 run，用相同任务、语料、模型与预算实际生成报告；不要手写一个较弱报告充当旧版效果。写作端只看任务及语料，不接收预期答案或评审结论。报告用中性标题和相同引用约定，避免在正文暴露版本。调用者负责执行共同预算；`compare` 只记录预算，不负责运行模型或拦截超额操作。

```bash
research.py compare evals/pricing/case.json --a baseline.md --b candidate.md \
  --a-run runs/baseline --b-run runs/candidate --seed 42 --output comparisons/pricing
# 把 blind.json 与 judgments.template.json 交给尚未看过版本身份的审阅者。
research.py score comparisons/pricing --judgments comparisons/pricing/judgments.json
```

输出目录必须是新目录，避免覆盖已有评审。`blind.json` 包含问题、完整 checklist、固定语料及 hash、随机 A/B 报告、共同预算和 rubric；`key.json` 单独保存身份与成本，不给盲评者。随机顺序不保证文风无法透露身份。

审阅者对 completeness、citation_support、source_quality、synthesis 分别给 0–4 分和基于正文的理由；每项 checklist 标 `direct / inference / missing / incorrect` 并写理由，最后选择 A/B/tie。`direct` 指来源正文直接给出答案，`inference` 包含基于已读前提的透明计算和推导，不按报告是否写出答案区分；对要求说明证据缺口的项目，准确披露缺口可满足要求。缺字段、空理由、越界分数、case/report hash 不匹配均拒绝。工具只校验合同并汇总显式判断，不自动判语义分数，也不验证审阅者身份。

可选 run 中的 `measurements.json` 保存平台实际成本，例如墙钟时间、模型/配置、token 与费用；`.adapter-counts.json` 另保留逻辑 Search/Read、HTTP 请求及请求累计耗时。两者同时存在时，平台测量作为主记录，脚本计数保留在 `adapter_usage`，不互相覆盖。没有测量就保留 unknown，不能记作 0；HTTP 请求累计耗时也不能当作端到端时间。最后报告成对结果、引用支持判断依据、直接覆盖与推断的区别、成本是否可比和样本限制；平局或退步同样记录，不从少量合成任务推断普遍提升。

### 研究者与评估者材料隔离

案例可增加 `"evaluator": "evaluator.json"`，其中 `criteria` 的每项包含唯一 `id`、`requirement`、正整数 `weight`、参考判断 `reference` 和 `source_ids`。可选 `evidence_groups` 定义每组至少读到一份的可替代证据，例如 `[["S1"], ["S2", "S3"]]` 要求 S1 加 S2/S3 之一。参考判断与这些分组只供评估者读取。

```bash
research.py case-pack evals/pricing-hard-zh/case.json --output /tmp/researcher-pricing
```

只向研究者暴露导出目录：含问题、预算与改名后的固定语料，不含 checklist、参考判断、evaluator 路径和评分细则。不要让研究者在原仓库中运行宽泛检索；导出功能不提供操作系统隔离，实际运行者须限制其可读目录。评估者仍使用原 case，`compare` 将参考材料置于 blind.json，key.json 不给评估者，二者都不给研究者。

新增 `criteria` 逐项填写 `met / partial / unmet / not_assessable`、`reason`、报告中的逐字 `report_quote` 和实际引用的 `source_ids`。met/partial 需要非空摘录及来源；缺失项可用空摘录并解释缺失，不强迫编造引用。判断映射必须与 rubric 精确对应，重复 JSON 字段、缺项、额外项、伪造报告摘录均拒绝。`source_ids` 是评估者从报告确认的引用，不能为未引用内容填入理想答案所需来源。

工具按权重汇总 met=1、partial=0.5、unmet=0，not_assessable 不进入已评权重；同时保留 total_weight/assessed_weight，不能靠跳过难项掩盖覆盖。`packet_hash` 把任务、rubric、报告和语料作为一份比较依据绑定到判断。逐字匹配仅证明摘录存在，不能证明满足 rubric。

### 先校准事实评委

判断报告质量改进前，可用冻结的已知错误与等义控制检验评委。现有 `evals/multihop-long-en/calibration/suite.json` 保存一份已归档报告的原文、四类错误（许可证遗漏、延迟指标混淆、转载冒充独立测量、保障字段凭空确定）及压缩、重排、重复三个控制，共八份报告。每份报告 SHA-256 和十二项原子判断预先声明；这些是公开开发材料，不能作为未见样本。原文的旧临时引用路径保留以维持字节 hash，核查依据是盲包中的 S1–S6 全文。

```bash
research.py calibrate evals/multihop-long-en/calibration/suite.json \
  --seed 42 --output /tmp/judge-calibration
# 将 blind-1.json + judgments-1.template.json 交给评委的一个新上下文；
# 将 blind-2.json + judgments-2.template.json 交给同一评委配置的另一个新上下文。
research.py calibration-score /tmp/judge-calibration \
  --judgments /tmp/judge-calibration/judgments-1.json \
  --judgments /tmp/judge-calibration/judgments-2.json
```

每轮每份报告只出现一次，随机配对与排序；第二轮对同一组报告交换 A/B，使用不同匿名 frame ID。双报告 frame 两轮均按 A、B 输出，实际展示顺序随标签反转；报告总数为奇数时，最后 frame 只含 A，第二轮只含 B。盲包包含完整语料、原子 criterion 和统一判断说明，隐藏原文/变体身份、错误清单、expected 和私有路径；原文与变体可能仍有文风差异，盲化不保证身份无法推测。`key.json`、原 suite 和另一轮输出都不给评委。两轮应使用相同模型与配置的独立上下文，不能复制上一轮判断后冒充换标验证；`reviewer` 必须相同，可记录相同的 `reviewer_config`，比较配置时保留 JSON 类型，工具不会验证身份和实际模型设置。

对每个 frame 中实际存在的标签逐 criterion 判断：`verdict=supported|refuted|insufficient_evidence|missing|not_assessable`。supported 的 `support_origin=source_stated|derived`，其余为 not_assessed。直接来源字段或明确披露的证据边界用 source_stated；算术、门槛判断和分析建议用 derived。引用数量不决定 origin。缺失证书/地域资料不能推成不存在或已确定；透明、正确的计算无需来源逐字给出结果。每项提供具体 reason，supported/refuted/insufficient_evidence 需要逐字 report_quote，supported/refuted 另需实际核验的 source_ids。判断该项的全部相关陈述，不能只摘正确子串忽略相矛盾的表述。

新增 suite 采用 `schema_version=1`，含 `id/case/criteria/anchor/variants`；每份报告声明 `path/sha256/expected`，expected 完整覆盖 criterion ID，值为 verdict 和 support_origin。variant 另含 `id/kind/description/targets`：error 只改变 targets 的预期，control 的 targets 为空且预期与原文相同。同一内容 hash 不能声明矛盾预期。先人工确认单因素范围与控制等义，再冻结文件与期望；脚本只校验合同，不能证明这些期望正确。改动后另建新套件和运行目录，不在看过评委结果后悄悄改答案。

`calibration.json` 报告逐项匹配、错误目标识别、控制稳定性和按报告内容 hash 对齐的标签一致性，保存原判断的内容 hash 与 `reviews/` 副本。所有预期项仍进入分母，not_assessable 不会抬分；重复文字也不增加事实项。只有完整匹配且一致才 `passed=true`。失败时保留具体分歧，不能使用该次评委的偏好宣布版本改善；通过也只说明该配置在这批开发错误与控制上的表现，不代表一般评委准确率或报告质量提升。命令不调用模型、不生成语义答案，也不测量 token、费用或端到端耗时。

### 有记录的固定语料检索与归因

新案例预算包含 `network_calls=0`、`search_calls`、`source_reads`，后两者 0–100。旧案例仍可走原有 compare；使用下面的离线 runner 前须明确补上预算。每个版本使用独立 run，任务、语料、模型及上限相同。

```bash
export DEEP_RESEARCH_RUN_DIR=/tmp/pricing-run-a
export DEEP_RESEARCH_SOURCE_POLICY=local_only
research.py init --deliverable "费用比较" --target "青梧 v3"
research.py need N1 --question "当前费用" --proof "当前价目及负载"
research.py search "请求 存储 计费" --corpus /tmp/researcher-pricing/case.json --need N1
research.py read REF_ID --corpus /tmp/researcher-pricing/case.json --term 请求费
# 也可直接读取 S1，再用同一编号保存证据和回查：
research.py read S1 --corpus /tmp/researcher-pricing/case.json
research.py evidence S1 --need N1 --claim "请求按每千次计费" \
  --quote "每 1,000 次请求 0.12 美元" --relation supports
research.py open S1 --start-line 1 --count 4
```

检索使用本地 SQLite FTS5/BM25，中文按字分词，是可复算的开发用检索器。返回的标题/snippet 仍是线索，不能引用；read 保存原有 snapshot 和白名单。成功 Read 的来源编号可用于离线 `evidence/open/find`，解析到该次已读快照；未读来源仍须 Read，详见[阅读与回查](workspace-and-evidence.md#阅读与回查)。`--corpus` 也可与 `--gap` 组合补查。模型对工具的显式 read 与检索器内部扫描区分计数，相同 search 缓存回放不多花检索预算，显式 read 每次计一次，读取失败也计。source hash、目录边界与任务/语料/预算指纹都检查；换任务或预算须换 run。

调用及耗时保存在 `.deep-research/corpus-trace.json`。compare 提供 `--a-run/--b-run` 后，score 将 trace 与案例指纹核对，并按 evidence_groups 输出 retrieval_gap、reading_gap、citation_gap、evidence_use_gap、meets_rubric 或 review_trace_mismatch。直接 read 的来源单独列出，不强迫必须先 search。met 仅是审阅者判断；没有分组、没有 trace 或无法评判时为 unknown。对应 run 若有同一报告 hash 的 `claims` 审阅，compare 会核验历史快照，另列 `correctly_used_source_ids`：只有完全支持论断的已确认贡献引用，且 REF/snapshot 与实际 read 记录一致时才计入。无审阅为 null，不能由 rubric 的 met 或引用数推定每份来源用对。诊断不能覆盖绕开这些命令的文件读取。

缺平台 measurements 时优先回读 trace 的真实 search/read 次数和工具耗时，scope 标明 fixed_corpus_tools_only；即使存在全为零的 HTTP 计数，也不覆盖离线检索成本。存在平台测量时，trace 成本另放 `fixed_corpus_usage`。总费用、模型调用数和美元成本没有记录就保持 null。`measurements.json` 可另填实际 `model` 和测量数据；score 的 comparability 单独展示语料/上限/模型是否一致，未知项不推定一致。这套回归只能证明接口行为，报告质量提升仍需实际同模型、同预算的盲评和独立 held-out 样本。

### 真网页与故障回归

固定语料 A/B 之外，分别验证真实 HTTP 通道和故障语义：超时/限流、验证码/未知页面、真实空响应、部分渠道成功、缓存复用与请求额度。记录查询、provider、失败原因、逻辑调用、真实 HTTP 尝试、重定向与耗时；只用公开查询，并按同一上限测试。在线结果会随时间和服务状态变化，保存运行时间及路径，不把验证码当作零命中，也不把直接 Read 成功当作搜索召回成功。

对确定性的实现故障可用相同 mock 在旧版/新版重放，标为功能回归，不能把它计作研究质量分数。报告质量比较必须让每个版本实际生成报告、用相同输入和预算，并由未看版本身份的审阅者核对原文；提供参考答案的作者不能同时冒充独立研究者。
