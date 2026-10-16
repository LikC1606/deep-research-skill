# CT-91 September Rollout Acceptance Assessment

## Decision

The September rollout does **not** meet CT-91's acceptance gates. The requested object is the accepted ARC-6E handoff, deployed as `tq-2.4.1-linux-x64` with sealed digest `tq241-c17e-9b42`, persistent write barrier enabled, admission ceiling 32, and eight producer streams. The monthly cost comparison clears the required saving, but the corrected queue-residence p95 misses the latency limit. The corpus supports a hold on unconditional approval and a bounded follow-up experiment; it does not support a production guarantee.

## Identity and gates

“Needle-17” is an operations-calendar pilot alias, not a package name. CT-91 directs the reviewer to resolve it through the release archive [S1, line 5]. The archive maps `Needle-17` to `ARC-6E`; its “accepted pilot handoff” status means the artifact and reports were sealed for review, and explicitly does not mean a workload-specific deployment gate passed [S2, lines 8 and 12].

The ARC-6E register identifies product **Trivenn Queue 2.4.1**, package `tq-2.4.1-linux-x64`, digest `tq241-c17e-9b42`, persistent-barrier mode, admission ceiling 32, eight producer streams, reference build B-11, dossier PERF-246 and price schedule L-44 [S3, lines 4–10]. The no-barrier sibling ARC-6F (`tq241-x00a-1d13`) is a different artifact and cannot satisfy CT-91. Revision 1.1 corrected the report's accounting and latency label without changing ARC-6E's executable digest [S3, lines 12–14].

CT-91 has two necessary gates: monthly CPU-plus-queue-license spending must be at least 20% below B-11, and pooled steady-state queue-residence p95 must be no greater than 150 ms at the matching workload and configuration. Failure of either gate prevents unconditional approval [S1, line 11].

## Performance evidence and correction

The matching workload is 2,000,000 accepted jobs per day for 30 days, with an 80% clean and 20% retry mix. These are accepted logical jobs; a retry job has one failed attempt followed by one successful attempt [S1, line 7]. PERF-246 uses the same eight-producer, ceiling-32, persistent-barrier configuration and defines queue residence from first enqueue through final acknowledgment [S4, lines 16 and 40].

The early summary and a later digest repeated candidate CPU means of 18/40 CPU-ms (clean/retry) and a 118 ms p95, describing roughly 47% lower CPU. Although the digest was published on August 28, it reproduces the August 20 excerpt and says it did not rerun the benchmark, inspect traces or review appendices [S5, lines 2, 4 and 6]. Revision 1.1, signed August 24, supersedes those CPU values for cost and efficiency: the candidate's billable helper adds 6 and 20 CPU-ms, giving **24/60 CPU-ms per accepted clean/retry job**; B-11 remains **30/90**. Each corrected class mean already includes every attempt; B-11's means already include its persistence work [S4, lines 94–96]. The 118 ms value was pooled worker service-time p95, incorrectly labelled queue residence. The corrected pooled steady-state queue-residence p95 is **168 ms** for the candidate; B-11's **142 ms** queue-residence value is unchanged [S4, line 98].

Derived reconciliation: the old weighted candidate mean was `0.8 × 18 + 0.2 × 40 = 22.4 CPU-ms/job`, versus B-11's `0.8 × 30 + 0.2 × 90 = 42`, explaining the old `(42 − 22.4) / 42 = 46.67%` headline. The corrected candidate mean is `0.8 × 24 + 0.2 × 60 = 31.2 CPU-ms/job`, making the applicable CPU reduction `(42 − 31.2) / 42 = 25.71%`. These are CPU reductions; the monthly comparison below applies the same reduction [S1, line 7; S4, lines 7 and 96]. The digest supplies no independent corroboration [S5, lines 6–8].

## Monthly cost calculation

Documented inputs are evidence (E); arithmetic results below are derived estimates (I). For queue `i`, the general formulas are `mean_CPU_i = 0.8 × clean_CPU_i + 0.2 × retry_CPU_i` in CPU-ms/accepted job, `CPU_hours_i = monthly_accepted_jobs × mean_CPU_i / 3,600,000`, and `monthly_cost_i = CPU_hours_i × USD 0.40/CPU-hour`. All calculations use accepted jobs as the denominator. The two-attempt retry CPU is already contained in its class mean; it is neither discarded nor multiplied by two again [S1, line 7; S4, lines 22 and 96].

1. Monthly accepted jobs: `2,000,000 jobs/day × 30 days = 60,000,000 jobs` [S1, line 7].
2. Clean jobs: `60,000,000 × 0.80 = 48,000,000`; retry jobs: `60,000,000 × 0.20 = 12,000,000` [S1, line 7].
3. B-11 CPU: `(48,000,000 × 30 + 12,000,000 × 90) CPU-ms = 2,520,000,000 CPU-ms = 700 CPU-hours` [S4, line 96; S6, line 5].
4. Candidate CPU: `(48,000,000 × 24 + 12,000,000 × 60) CPU-ms = 1,872,000,000 CPU-ms = 520 CPU-hours` [S4, line 96; S6, line 5].
5. L-44 converts 1,000 CPU-ms to one CPU-second and 3,600 CPU-seconds to one CPU-hour, charges USD 0.40 per CPU-hour for either queue, and includes the persistence helper [S6, lines 5–7]. Therefore CPU charges are **USD 280.00** for B-11 (`700 × 0.40`) and **USD 208.00** for the candidate (`520 × 0.40`).
6. For CT-91, take these CPU charges as the complete monthly queue-specific totals: **USD 280.00** for B-11 and **USD 208.00** for the candidate [S6, lines 5–7].

The candidate's saving is `(280.00 − 208.00) / 280.00 × 100 = 25.7143%`, or **25.71%** rounded to two decimals. The 20% gate requires candidate cost of at most `280.00 × 0.80 = USD 224.00`; the candidate is below that limit by **USD 16.00** and passes the cost gate [gate premise: S1, line 11]. Monthly savings are **USD 72.00**.

The forecast assumes unchanged reported class means at the requested scale, the specified mixture/configuration, a deployment active throughout September, and CT-91's perfectly right-sized capacity. CT-91 excludes idle capacity, minimum reservations, extra warm replicas, tax, discounts, credits and unchanged shared costs; the model has no other queue-specific fee [S1, line 9; S6, lines 7–9]. L-44 is effective September 1–30. The older L-43 price of USD 0.35/CPU-hour and proposed introductory waiver expired August 31 and cannot be used here [S6, lines 2 and 9]. Billing uses aggregated CPU without rounding by job, attempt, day or whole hour; final monthly dollar totals are rounded to cents [S6, line 5]. These totals cover the requested CPU-plus-license boundary, not a wider production bill.

## Gate result

| Gate | Requirement | ARC-6E result | Outcome |
| --- | --- | --- | --- |
| Monthly cost | At least 20% below B-11 (≤ USD 224.00) | USD 208.00 vs USD 280.00; 25.71% lower | **Pass** |
| Queue residence | Pooled steady-state p95 ≤ 150 ms | 168 ms (B-11: 142 ms) | **Fail** |

The cost calculation passes, while the queue-residence result fails against the corrected report boundary. The candidate's lower service-time statistic cannot substitute for queue residence because requeue wait is included in queue residence but excluded from service time [S4, lines 40 and 98]. CPU means and throughput ratios also cannot establish this percentile, and percentiles cannot be averaged across classes or runs as CPU means can [S4, lines 18, 42 and 70–72].

## Assurance limits and recommendation

The corpus can establish the alias-to-digest identity, the tested configuration, the accepted-job denominators, corrected descriptive CPU means, corrected pooled p95, and the September cost arithmetic. It documents completion-marker-before-acknowledgment configuration and that every retained accepted item reached a final acknowledgment; these are bounded test observations [S4, lines 16 and 24]. It cannot establish an independent security-certification identifier or a contractual queue-record storage region, both of which CT-91 asks procurement to identify [S1, line 13]. The benchmark's “Test Room East” is only an experiment location, not a hosting or residency commitment [S4, line 60]. L-44 likewise supplies no certification, contractual region or p95 service-level guarantee. The missing fields do not establish that certification or regional options do not exist [S6, line 11].

The eight retained runs each contain 100,000 measured accepted jobs after 20,000 warm-up jobs, hence `8 × 100,000 = 800,000` pooled observations per build. Run IDs are B11-P01–P08 and T241-P01–P08. These repeat one generated workload and host configuration; they do not represent eight independent customer deployments. The report supplies no production-population confidence interval, September predictive interval or statistical-significance claim [S4, lines 46–52]. It also does not establish behavior under unlimited arrival bursts, persistent failures, storage faults, operator cancellations, different payloads, other retry mixtures or another persistence setting [S4, lines 16, 28 and 64–66]. Raw individual-event traces are absent, so the reported p95 cannot be independently recomputed from this corpus; no run-level p95 or error bars can be supplied from it [S4, line 54].

Recommendation (I): **do not grant unconditional September rollout approval under CT-91**; retain B-11 for the requested workload pending a successful replacement test. A bounded follow-up can investigate candidate requeue ordering and barrier scheduling, with the persistence setting retained and the digest/configuration explicitly recorded. Those mechanisms can concentrate delays despite lower mean CPU [S4, line 64]. Capture all worker-role CPU and complete first-enqueue-to-final-ack observations, including failed attempts; test the specified workload first, then separately assess relevant burst and failure patterns. Do not claim that a changed traffic mixture or ceiling clears the original gates. Obtain the missing certification and contractual-region evidence separately. Reconsider CT-91 approval only after the matching candidate retains the modeled cost at or below USD 224.00 and demonstrates pooled queue-residence p95 at or below 150 ms. These follow-up conditions do not establish that both gates presently pass or guarantee future production behavior [S1, line 11; S4, lines 82 and 100].

## Sources

- **[S1]** `/tmp/deep-research-eval-20261016-0Dy5D0/researcher/corpus/S1.txt` (lines 5, 7, 9–13): CT-91 request, workload, gates and assurance questions.
- **[S2]** `/tmp/deep-research-eval-20261016-0Dy5D0/researcher/corpus/S2.txt` (lines 8, 12–14): alias archive and meaning of “accepted.”
- **[S3]** `/tmp/deep-research-eval-20261016-0Dy5D0/researcher/corpus/S3.txt` (lines 4–10, 12–14): ARC-6E package, digest and mode.
- **[S4]** `/tmp/deep-research-eval-20261016-0Dy5D0/researcher/corpus/S4.txt` (lines 1–100): PERF-246 methods, limitations and revision 1.1 corrections.
- **[S5]** `/tmp/deep-research-eval-20261016-0Dy5D0/researcher/corpus/S5.txt` (lines 1–8): non-independent pre-correction digest.
- **[S6]** `/tmp/deep-research-eval-20261016-0Dy5D0/researcher/corpus/S6.txt` (lines 1–11): September L-44 rates, fees and exclusions.
