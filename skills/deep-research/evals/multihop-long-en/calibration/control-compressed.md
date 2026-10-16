# CT-91 September Rollout Acceptance Assessment

## Decision and exact object

Hold unconditional September approval: CT-91's cost and queue-residence gates both fail. “Needle-17” is the calendar alias that S1 sends to the S2 archive; it maps to ARC-6E, whose S3 register identifies **Trivenn Queue 2.4.1**, package `tq-2.4.1-linux-x64`, digest `tq241-c17e-9b42`, persistent barrier enabled, ceiling 32, eight producers and reference B-11. The linked dossier and applicable schedule are PERF-246 and L-44. “Accepted pilot handoff” means sealed for review, not gate passage. ARC-6F (`tq241-x00a-1d13`) is the no-barrier sibling and cannot substitute; revision 1.1 changes report accounting and labels, not ARC-6E's digest [S1, line 5; S2, lines 8 and 12; S3, lines 4–14].

CT-91 requires monthly CPU-plus-license spending at least 20% below B-11 and matching pooled steady-state queue-residence p95 ≤ 150 ms. Either failure blocks unconditional approval [S1, line 11].

## Corrected evidence and provenance

The workload is 2,000,000 accepted logical jobs/day for 30 days, 80% clean and 20% retry. Each retry includes one failed then one successful attempt; do not drop it or count it as two accepted jobs. The matching PERF-246 configuration is eight producers, ceiling 32 and persistent barrier; queue residence runs from first enqueue to final acknowledgment [S1, line 7; S4, lines 16, 22 and 40].

The retained summary's candidate 18/40 CPU-ms clean/retry means yield `0.8 × 18 + 0.2 × 40 = 22.4 CPU-ms/job` versus B-11's `0.8 × 30 + 0.2 × 90 = 42`, explaining `(42 − 22.4)/42 = 46.67%` (roughly 47%). S5's August 28 digest copies the August 20 excerpt: it did not rerun, inspect traces or read appendices, so it is neither a newer measurement nor independent corroboration [S4, line 7; S5, lines 2–8].

The August 24 revision 1.1 supersedes the candidate CPU means: billable helper work adds 6/20 CPU-ms, producing **24/60 CPU-ms per accepted clean/retry job**. Each mean already includes every attempt; B-11 remains **30/90** and already includes persistence work. The corrected candidate weighted mean is `0.8 × 24 + 0.2 × 60 = 31.2 CPU-ms/job`, a CPU reduction of `(42 − 31.2)/42 = 25.71%` before the fixed fee [S4, lines 94–96].

The old **118 ms** candidate value is pooled worker **service-time p95**, excluding requeue wait. Corrected pooled steady-state **queue-residence p95 is 168 ms**; B-11's **142 ms** queue residence is unchanged [S4, line 98]. CPU means and throughput do not establish a latency percentile; percentiles cannot be averaged across classes or runs as CPU means can [S4, lines 18, 40, 42 and 70–72].

## Monthly forecast and gates

Evidence supplies inputs (E); the following arithmetic is derived (I). Use `mean_CPU_i = 0.8 × clean_CPU_i + 0.2 × retry_CPU_i` in CPU-ms/accepted job, `CPU_hours_i = monthly_accepted_jobs × mean_CPU_i / 3,600,000`, and `monthly_cost_i = CPU_hours_i × USD 0.40/CPU-hour + fixed_license_i`. Retry CPU is already in the corrected means [S1, line 7; S4, lines 22 and 96].

- Accepted jobs: `2,000,000 × 30 = 60,000,000`; clean `48,000,000`, retry `12,000,000`.
- B-11 CPU: `48,000,000 × 30 + 12,000,000 × 90 = 2,520,000,000 CPU-ms = 700 CPU-hours`.
- Candidate CPU: `48,000,000 × 24 + 12,000,000 × 60 = 1,872,000,000 CPU-ms = 520 CPU-hours` [S1, line 7; S4, line 96; S6, line 5].
- L-44 uses 1,000 CPU-ms/CPU-second and 3,600 CPU-seconds/CPU-hour, bills both worker roles at USD 0.40/CPU-hour, and charges fixed September module fees of **USD 0** for B-11 and **USD 60** for persistent Trivenn [S6, lines 5–7].
- CPU charges are `700 × 0.40 = USD 280.00` and `520 × 0.40 = USD 208.00`; CPU-plus-license totals are **USD 280.00** and **USD 268.00**. CPU saves USD 72.00; after the license, monthly savings are USD 12.00.
- Relative total savings: `(280 − 268)/280 × 100 = 4.2857%`, or **4.29%**. The 20% cost gate requires `280 × 0.80 = USD 224.00`; USD 268.00 exceeds it by **USD 44.00**: **Fail** [S1, line 11].
- Matching queue-residence p95 is **168 ms > 150 ms** (B-11: 142 ms): **Fail** [S1, line 11; S4, line 98].

This is a CPU-plus-license forecast, not the whole production bill. It assumes the corrected class means persist at the requested scale, the stated mix/configuration, September-long activity and perfectly right-sized capacity. CT-91/L-44 exclude idle capacity, minimum reservations, warm replicas, tax, discounts, credits and unchanged shared costs; there is no other queue-specific fee. L-44 applies September 1–30; expired L-43's USD 0.35 rate and proposed introductory waiver do not apply. Aggregate CPU has no per-job, attempt, day or whole-hour rounding; round final monthly dollars to cents [S1, line 9; S6, lines 2 and 5–9].

## Assurance limits and bounded follow-up

The corpus establishes identity, tested configuration, accepted-job denominators, corrected reported means/p95 and the arithmetic. Completion markers precede acknowledgment and every retained accepted item reached final acknowledgment; these are test observations [S4, lines 16 and 24]. It establishes **neither an independent security-certification identifier nor a contractual queue-record storage region**. “Test Room East” is only a benchmark location. L-44 adds no such assurances or p95 service-level guarantee; missing evidence does not prove certification or region options do not exist [S1, line 13; S4, line 60; S6, line 11].

Each of eight retained runs has 100,000 measured accepted jobs after 20,000 warm-up jobs, so `8 × 100,000 = 800,000` pooled observations/build; run IDs are B11-P01–P08 and T241-P01–P08. They repeat one generated workload/host, not eight independent customer deployments. There is no production-population confidence interval, September predictive interval or significance claim. Unlimited bursts, persistent failures, storage faults, cancellations, different payloads, other retry mixes or persistence modes remain unestablished. Raw individual-event traces are absent: p95 cannot be independently recomputed, and run-level p95/error bars cannot be invented [S4, lines 16, 28, 46–54 and 64–66].

Recommendation (I): **withhold unconditional September approval and retain B-11** pending a successful matching test. Investigate requeue ordering/barrier scheduling while retaining persistence and recording digest/configuration; these can concentrate delay despite lower CPU [S4, line 64]. Capture all worker-role CPU and full first-enqueue-to-final-ack observations including failed attempts; test the specified workload before separate burst/failure patterns. A changed mix or ceiling cannot clear the original gates. Obtain certification and contractual-region evidence separately. Reconsider only after matching cost ≤ USD 224.00 and pooled queue-residence p95 ≤ 150 ms. These follow-up conditions and the current observations provide no future production guarantee [S1, line 11; S4, lines 82 and 100].

## Sources

- **[S1]** `/tmp/deep-research-eval-20261016-0Dy5D0/researcher/corpus/S1.txt` (lines 5, 7, 9–13): CT-91 request, workload, gates and assurance questions.
- **[S2]** `/tmp/deep-research-eval-20261016-0Dy5D0/researcher/corpus/S2.txt` (lines 8, 12–14): alias archive and meaning of “accepted.”
- **[S3]** `/tmp/deep-research-eval-20261016-0Dy5D0/researcher/corpus/S3.txt` (lines 4–10, 12–14): ARC-6E package, digest and mode.
- **[S4]** `/tmp/deep-research-eval-20261016-0Dy5D0/researcher/corpus/S4.txt` (lines 1–100): PERF-246 methods, limitations and revision 1.1 corrections.
- **[S5]** `/tmp/deep-research-eval-20261016-0Dy5D0/researcher/corpus/S5.txt` (lines 1–8): non-independent pre-correction digest.
- **[S6]** `/tmp/deep-research-eval-20261016-0Dy5D0/researcher/corpus/S6.txt` (lines 1–11): September L-44 rates, fees and exclusions.
