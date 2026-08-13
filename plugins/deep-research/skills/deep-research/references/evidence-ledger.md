# Research Note

这份笔记把“看过网页”变成可复查的 `claim -> excerpt -> source` 链。Light Search 可以只在当前上下文维护表格；Deep Search 只需维护一份 `research.md`，不必拆成多个状态文件。

## 最小模板

```markdown
# Research Note

## Contract
- Goal:
- Target:
- Constraints:
- Source policy: local_only | web_allowed | mixed
- Mode and budget:

## Need Map
| ID | Need | Why it changes the result | Best artifact | Proof | Status |
| --- | --- | --- | --- | --- | --- |
| N1 | 一个必须回答的未知项 | 会改变哪个决策 | 原始数据/规范/复盘/代码/复现 | 什么证据才算关闭 | open / partial / closed / blocked |

## Sources
| ID | Locator | Role | Useful excerpt/location | Supports | Next lead |
| --- | --- | --- | --- | --- | --- |
| S1 | URL or path | direct / bridge / mixed | "原文" (章节/页码) | N1 / C1 | 新实体或引用 |

## Claims
| ID | Claim | Support | Opposes/limits | Status |
| --- | --- | --- | --- | --- |
| C1 | 一个最小可验证说法 | S1 | S3 | supported / partial / disputed / open |

## Gaps and next queries
- G1 [high]: 缺什么；下一 query；状态 open/blocked/done

## Insights
- Insight: ...
  Evidence: S1 + 摘录/位置
  Why it matters: ...
  Next check: ...

## Stop
- Reason:
- Sources read:
- Remaining gaps:
```

一个 claim 尽量只表达一个事实或判断。把“数据 + 因果 + 建议”拆开，避免一个引用被迫支撑整段推断。

## 每轮更新

1. 给新来源去重并分配 ID；本地文件用 path，不伪造 URL。
2. 保存与 `reading_goal` 直接相关的最小摘录和位置。
3. 把摘录拆成 claim、限定、反方和后续线索。
4. 更新 Need 状态和 gaps，再决定下一次 query；不要把原始网页全文复制进长期上下文。

只有能改变 claim、gap 或搜索方向的结果才算新证据。同一事实的改写、同源转载和未核验 snippet 记为重复/线索，不算独立支持。

## 快速审计

对每条重要 claim 检查：

- 来源的实体、时间、版本和范围正确；
- 摘录直接蕴含 claim，而不是只共享关键词；
- 至少有一个真正独立、反方或更新来源，或说明为什么没有；
- 数字、单位、比较基准和因果措辞没有超过原文；
- `bridge` 来源只用于导航，没有被当作直接证据。

来源质量相对 claim 判断：官方结果证明名次/版本，作者复盘或仓库证明其方法，独立复现证明可迁移性；snippet 只能发现线索。

## 停止记录

停止时只写三件事：

1. 已关闭的高影响 Needs 和关键 claim；
2. 停止原因（已满足、连续两轮无高价值新证据、预算/访问/安全阻塞）；
3. 仍未解决的 gaps 和置信度。

“找到一个相关 URL”不等于 `supported`；无法定位原文时标为 `open` 或 `blocked`。
