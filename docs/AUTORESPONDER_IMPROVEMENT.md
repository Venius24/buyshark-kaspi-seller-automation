# LLM auto-responder: measure, fix, measure again

This is the follow-up to [AUTORESPONDER_EVAL.md](AUTORESPONDER_EVAL.md). That review measured the
buyer-chat auto-responder on production chats and found specific causes of errors. This document
covers what happened next, 19–20 October 2026:

1. The review was turned into a regression set.
2. An LLM judge was calibrated against the manual labels.
3. The causes were fixed.
4. The bot was measured again with the same judge.
5. The result was deployed.

Every measurement is listed here, including the failed ones. The full chronological log, in
Russian, is [evals/autoresponder/METRICS_LOG.md](../evals/autoresponder/METRICS_LOG.md). Its rows
are append-only.

## Result

Same 102 cases, 3 repeats each, same judge (GPT-6 Luna, medium effort, rubric v3). "Before" is the
code that was in production during the manual review. "After" is the version deployed on
20 October.

| Metric | Before | After (deployed) |
|---|---|---|
| **Resolved by bot**: bot answered alone and the judge accepted the answer | 41.8 % | **49.3 %** |
| Success: the right outcome per case (95 % CI) | 71.9 % (63.7–79.7) | 83.3 % |
| Escalation recall: cases that need a human and got one | 81.8 % | 88.9 % |
| False hand-off: cases that did not need a human but got one | 11.3 % | **2.8 %** |
| Escalation precision | 83.5 % | 95.7 % |
| Autonomy: runs closed without a human | 64.1 % | 62.7 % |
| Sent answers judged acceptable | 65.3 % | 78.6 % |
| Unsupported claims among sent answers | 16.8 % | 10.4 % |
| Prompt tokens per call | 2014 | 3342 (+66 %) |

Paired comparison over the 102 cases: success **+11.4 points (95 % bootstrap CI +4.2…+19.0)**.
17 cases got better and 6 got worse (majority over repeats). McNemar p = 0.035.

The cost is a longer prompt: about +$0.0003 per answer at DeepSeek's prices.

**The set is deliberately loaded with failures** (see below), so these numbers are not comparable
with the 83.8 % "no visible problem" share from the production review. They are only comparable
with each other.

## 1. The regression set

`evals/autoresponder/cases.jsonl`, 102 cases:

- **94 cases from the labelled production chats.** All 55 problem cases and hand-offs, plus 40 cases
  labelled ok. The ok cases include every Kazakh-language case and every voice message; the rest
  were picked at random.
- **8 synthetic cases.**

Customer messages were **paraphrased by an LLM, never copied**. A guard check rebuilt every case
after paraphrasing and required the bot's deterministic behaviour to stay the same: keyword
triggers, complaint stems, the logistics template, and the "catalogue question" flag. One case
failed the check and was dropped.

The store is fictional (`store_profile.json`, "Domira"). Order codes, names and phones are
replaced deterministically.

One leak was found before the first commit:

- **What leaked.** The anonymiser replaced 10-digit order codes but missed real 9-digit ones, so 25
  real codes reached the public set.
- **Fix.** The pattern was widened, and the same replacement was applied to the set and to every
  results file.
- **What still matches the private data.** Product SKUs and the city code, both of which are public
  Kaspi catalogue identifiers.

**Fact fidelity.** The bot sees order facts in its prompt: status, dates and items. These could not
always be restored for old chats, so each case has a level:

| Level | Order facts |
|---|---|
| A, D | Restored as production saw them |
| B | Order stage inferred |
| C | No order |

For levels B and C the judge is told the facts are hidden. Without that note, the first judge called
correct production answers "invented", because the facts behind them were missing from the eval.

## 2. The judge, and how far to trust it

An LLM judge is only useful if it agrees with a human. It was calibrated against the owner's
**195 manual labels** with Cohen's κ. The dialogues were split into dev and test halves by a hash of
the conversation id. The rubric was tuned on dev only; test was looked at once at the end.

Two questions were asked of every judge:

- **4 classes:** ok / inexact / unsupported / should escalate.
- **Binary, "acceptable":** could this draft have been sent as it is?

| Judge | Rubric | Split | κ, 4 classes | κ, acceptable | Cost |
|---|---|---|---|---|---|
| gpt-5.4-mini | v1, facts always shown | all | 0.31 | 0.42 | $0.52 |
| gpt-5.4-mini | v2, facts hidden for B/C, norms | dev | 0.34 | 0.51 | $0.21 |
| gpt-5.4-mini | v3 | dev | 0.31 / 0.33 (two runs) | 0.51 / 0.60 | $0.21 each |
| gpt-5.4 | v3 | dev | 0.22 | 0.48 | $0.72 |
| gpt-5.5 | v3 | dev | 0.19 | 0.64 | $1.25 |
| gpt-5.4-mini | v3 | test | 0.30 | 0.52 | $0.34 |
| gpt-5.5 | v3 | test | 0.40 | **0.66** | $1.95 |
| GPT-6 Luna, low (Codex) | v3 | all 195 | — | 0.535 | subscription |
| **GPT-6 Luna, medium (Codex)** | v3 | all 195 | — | **0.574** | subscription |

What this showed:

- **Trust the binary verdict, not the 4 classes.** κ ≈ 0.6 on "acceptable" is moderate agreement.
  κ ≈ 0.3 on the 4 classes is not usable. So the main regression metric is binary, and the judge's
  "unsupported" share is reported only as a secondary signal.
- **The judge's own noise is about as large as the gap between models.** The same mini judge with
  the same rubric gave κ 0.51 and then 0.60 on the same dev half. A judge against itself reached
  κ 0.87 on "acceptable", which is the ceiling for any rubric change.
- **Bigger models did not help with the 4 classes.** gpt-5.5 was the best on the binary question.
- **The judge is stricter than the owner.**
  - Luna medium found 30 of the 34 bad answers (recall 88 %).
  - Of the 56 answers it flagged, the owner agreed on 30 (precision 54 %).
  - So the judge's absolute success rate runs lower than a human's would. That is fine for
    before/after, as long as both sides use the same judge.

### Running out of budget

The OpenAI judges ran on API credits. Calibration, the first "before" run and one failed run used
up the whole balance, about $9.2. Every judge call of the next run came back
`429: no credits remaining`.

The mistake was ours: nobody estimated the total cost or agreed on a cap before the paid runs.
Since then, a paid run starts only after a cost estimate and an agreed limit.

The judge was moved to **GPT-6 Luna through the Codex CLI**, which runs on a ChatGPT subscription.
Usage is counted as a share of the 5-hour limit:

- calibrating both effort levels and re-judging "before" and "after" took about 6 %;
- one judge call costs about 0.1 %.

Luna is a new judge, so it was **calibrated again on the same 195 labels**. Then it was checked
against gpt-5.5 on the same 196 "before" answers:

| Check | Value |
|---|---|
| Agreement | 180 of 196 |
| κ, acceptable | 0.815 |
| Success "before", gpt-5.5 | 73.9 % |
| Success "before", Luna | 71.9 % |

Changing the judge moves the number by about 2 points. Even so, before and after are only compared
under the same judge.

## 3. The LLM layer

The review asked for production engineering around the model call, not only a better prompt.

- **Config.** `backend/chatbot/llm.py` reads `CHATBOT_LLM_*` settings: URL, model, temperature,
  timeout and prices. They are no longer hard-coded.
- **Retries.** Retries on 429, 5xx and network errors, with exponential backoff and one shared
  deadline.
- **Tracing.** Every call records latency, attempts, tokens, cost, parse mode and `prompt_version`
  (a hash of the rendered template). The data goes to `chat_response_logs` (migration 053) and to
  Prometheus counters.
- **Shared checks.** The checks before and after the model moved into `backend/chatbot/pipeline.py`.
  The production worker, the eval runner and a replay script now call the same functions, so the
  eval tests the code that actually runs.

**Structured output: JSON mode failed, function calling worked.** The first attempt used
`response_format=json_object`. 81 of 306 calls failed:

- 59 came back as whitespace with `finish_reason=stop`, more often on short Kazakh messages.
- Re-sending the same request returned the same blanks.

The response's `model` field showed that `deepseek-chat` is now an alias of `deepseek-flash`. So
production had already moved to a different model than the one in the baseline, without any change
on our side.

On 8 of the failing cases:

| Option | Valid answers |
|---|---|
| Asking for JSON in the prompt text | 0 of 8 |
| Forced function call (`send_reply` tool with `tool_choice`) | 8 of 8 |

On the full set the function call gave 297 of 297 parsed answers, 0 API failures and 0 retries.
p95 latency fell from 8.1 s to 2.7 s, because the slow tail had been retries of blank answers. That
run is marked **invalid** in the log and was never used for comparison.

## 4. The fixes, one iteration at a time

### v1: fix the causes from the review

- **The `передал` stem was too broad.** It caught "передали курьеру" ("handed to the courier") and
  threw away correct answers. It was narrowed to phrases like "передал ваш вопрос" ("I've passed your
  question on").
- **Kazakh complaint forms were added.** Examples: "сынып келді" ("arrived broken"),
  "басқасын салды" ("sent the wrong one"). The bare "сынып" was left out because it also means
  "class".
- **A "reliability" block was added to the prompt:**
  - characteristics and stock come only from the data;
  - never confirm links or photos the bot cannot open;
  - a request for a real photo goes to a human;
  - no "I'll check", since nobody will.
- **The pickup fact was corrected.** It no longer says "still being assembled" for orders that are
  already assembled.

Result, judged:

| | Before | v1 |
|---|---|---|
| Success | 71.9 % | 82.0 % |
| Escalation recall | 81.8 % | 100 % |
| False hand-off | 11.3 % | **25.5 %** |
| Unsupported among sent answers | 16.8 % | 9.4 % |

The paired difference was +10.1 points (+3.3…+17.0).

v1 looked like a clear win, and the success metric alone would have shipped it. But a quarter of
the chats that did not need a human now went to the manager. That is a real cost to the store.

### New metrics expose the trade-off

Success mixes two things: the quality of the answers that were sent, and how often the bot gave up.
The owner proposed four separate metrics. A fifth was added as the main one:

| Metric | Definition |
|---|---|
| Answer quality | Share of sent answers the judge accepted |
| Escalation recall | Share of cases needing a human that got one |
| Escalation precision | Share of hand-offs, in cases with a known right answer, that were needed |
| Autonomy | Share of runs closed without a human |
| **Resolved by bot** | The bot answered alone *and* the judge accepted it, out of all runs |

`run.py --summary` recomputes them for any old results file, so the earlier runs were re-scored
without generating anything new:

| | Before | v1 |
|---|---|---|
| Answer quality | 65.3 % | 85.2 % |
| Autonomy | 64.1 % | 41.8 % |
| **Resolved by bot** | **41.8 %** | **35.6 %** |

**v1 made the bot worse at its actual job.** Its answers got better, but most of the success gain
came from handing chats off. The bot closed fewer chats on its own.

### An explicit decision instead of "answer or escalate"

The model now returns an `action` through the function call:

| Action | Meaning |
|---|---|
| `answer` | I know, I answer |
| `clarify` | I didn't understand, I ask |
| `safe_unknown` | I understood, but neither I nor the store can know this (for example, Kaspi's own logistics), so I say honestly where to ask |
| `escalate` | A human is really needed: the only action that hands off |

The decision is stored in `chat_response_logs.llm_action` and counted in Prometheus.

The owner also fixed a business rule: **a pickup order is always ready**. It is assembled on the
spot when the customer arrives, and the only limit is opening hours. Before, the bot sent "is my
order ready?" to the manager.

| Run | Change | Recall | False hand-off | Precision | Autonomy |
|---|---|---|---|---|---|
| after2 | actions + pickup rule + "clarify before escalating" | 82.8 % | 7.1 % | 89.1 % | 64.7 % |
| after3 | clarify only for ordinary questions, never instead of a hand-off | 94.9 % | 3.5 % | 94.9 % | 59.8 % |
| after4 | clarify asks for the product *name*, not a link, and promises nothing | 88.9 % | 2.8 % | 95.7 % | 62.7 % |

**after2 shows how one prompt rule can override another.** "Clarify first" turned into a way to dodge
hand-offs:

- "the item didn't reach me" (in Kazakh) got "which order do you mean?";
- "replace it today" got "replace what?";
- "like in the photo" got "please describe it in words".

after3 limited clarify to ordinary questions: stock, characteristics, price and delivery.
Complaints, returns, replacements, non-delivery and photos are handed off straight away.

### Judged comparison

| Pair | Success difference (95 % CI) | Better / worse | McNemar p |
|---|---|---|---|
| Before → after3 | +12.4 points (+4.6…+20.3) | 17 / 5 | 0.017 |
| Before → after4 | +11.4 points (+4.2…+19.0) | 17 / 6 | 0.035 |
| after3 → after4 | −1.0 points (−5.6…+3.3) | 5 / 6 | 1.0 |

after3 and after4 cannot be told apart statistically. after4 was deployed because it closes more
chats on its own with an accepted answer (49.3 % against 46.4 %) and sends less to the manager
needlessly. The cost is lower recall: about 6 runs out of 99, where a request about a photo or a
price offer turned into a clarifying question.

**The prompt was not tuned further on these 102 cases.** The next change would be fitting the set,
not improving the bot.

### Why clarify scores low

Accepted share of sent answers, by action:

| Action | after3 | after4 |
|---|---|---|
| answer | 86.7 % | 90.2 % |
| clarify | 52.2 % | 57.9 % |
| safe_unknown | 82.4 % | 61.5 % |

All 12 clarifications the judge rejected in after3 were read by hand. They had three causes:

1. **A real defect.** The question came with a promise ("tell me which one and I'll check the stock")
   or asked for a link the bot cannot open. after4 fixed this.
2. **An artefact of the set.** All 12 were level C/D cases with no product title. In production the
   bot sees the product in the chat title and rarely needs to ask. The set penalises the bot for
   data the eval doesn't have.
3. **The rubric doesn't know about clarify.** Rubric v3 expects an immediate hand-off when a
   characteristic is missing from the data. Changing that norm means a rubric v4 and another
   calibration on the 195 labels.

## 5. Deployment

after4 went to production on 20 October 2026 with migration 053. CI failed twice on the way, for
reasons unrelated to the bot:

- the tests still expected the previous migration head;
- PyMuPDF 2.0 shifted the waybill stamp, so it is now pinned below 2.

## Limitations

- **One store, one annotator.** The 195 labels come from the project owner, who reviewed an LLM's
  first pass. There is no second annotator.
- **The set was used for development.** Every prompt change was measured on the same 102 cases, so
  the after numbers are optimistic. The honest test is new production chats the prompt has never
  seen. That check has not been done yet.
- **The judge is moderately reliable.** κ is 0.57 on the binary question and unusable on the four
  classes. Luna's agreement with itself was not measured.
- **Levels C/D have no product title.** This depresses clarify scores.
- **Not in CI yet.** The judged runs are done locally through Codex, and their results are
  committed to `evals/autoresponder/results/`. Two pieces are still open:
  - a deterministic part (routes, stems, hand-off recall without the model) in pytest;
  - a `--max-cost` guard in the runner.
- **The generator model can change underneath.** `deepseek-chat` silently became `deepseek-flash`.

## Next

1. Restore product titles for level C/D cases.
2. Write rubric v4 with norms for clarify and safe_unknown, and recalibrate it on the same 195
   labels.
3. Measure on a **new** dataset of post-deployment chats, with the same metrics and the same judge,
   so the numbers sit next to the ones above.
4. Check production: compare the `llm_action` and hand-off shares in `chat_response_logs` against
   the original review.

## Reproduce

```bash
# re-score any committed run with the current metrics (no network)
python -m evals.autoresponder.run --summary evals/autoresponder/results/after4_luna-medium.json

# paired before/after comparison (no network)
python -m evals.autoresponder.run --compare evals/autoresponder/results/before_luna-medium.json evals/autoresponder/results/after4_luna-medium.json

# new generation + judge (needs a DeepSeek key and the Codex CLI)
python -m evals.autoresponder.run --live --repeats 3 --judge-model codex:gpt-6-luna:medium --out results.json
```

The calibration itself (`--calibrate`) needs the private manual labels, which are not published.
