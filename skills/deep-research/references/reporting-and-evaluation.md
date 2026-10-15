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

## 沿论文前后引用补证据

当某个 Need 需要方法起源、后续复现或反例时，针对已知论文扩展一页：

```bash
research.py follow ARXIV:1706.03762 --direction references --limit 5 --need N1
research.py follow DOI:10.1038/nature14539 --direction citations --limit 5 --need N2
```

支持 DOI、arXiv ID/URL、Semantic Scholar ID/URL，以及能解析到上述地址的 REF。使用 Semantic Scholar 公共 graph API；每页默认 10、最多 20 条，`next_offset` 指向后续页。结果只登记为 `unread_lead`，不会进入引用白名单，也不会自动关闭 Need。先 Read 匹配目标与证明标准的候选；引用数量、年份或元数据不能证明复现成功。

每次实际 API 请求消耗一次 search 预算，失败也计入；相同请求可缓存回放，`--refresh` 强制重取。`local_only` 只允许缓存回放，拒绝新页和刷新。带 `--need` 的成功与失败写入任务历史。遇到限流或不可用记录缺口，改用现有公开检索入口，不循环重试同一页。

## 固定语料的盲法成对评估

`evals/pricing`、`trial`、`methods` 各有一个合成案例，分别检查版本/计价、摘要/转载/因果边界、基准可比性/缺失字段。它们用于小型回归评估，不能代表真实网页检索难度或完整 benchmark。

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

审阅者对 completeness、citation_support、source_quality、synthesis 分别给 0–4 分和基于正文的理由；每项 checklist 标 `direct / inference / missing / incorrect` 并写理由，最后选择 A/B/tie。缺字段、空理由、越界分数、case/report hash 不匹配均拒绝。工具只校验合同并汇总显式判断，不自动判语义分数，也不验证审阅者身份。

可选 run 中的 `measurements.json` 保存实际成本，例如 `source_reads`、`network_calls`、`elapsed_seconds`；也兼容现有 `.adapter-counts.json`。没有测量就保留 unknown，不能记作 0。最后报告成对结果、引用支持判断依据、直接覆盖与推断的区别、成本是否可比和样本限制；平局或退步同样记录，不从少量合成任务推断普遍提升。
