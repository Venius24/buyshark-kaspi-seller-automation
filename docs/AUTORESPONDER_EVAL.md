# LLM auto-responder: quality review on production chats

A manual quality review of the buyer-chat auto-responder (a DeepSeek chat model behind the
Kaspi.kz seller chat) on real conversations from one store, 2–19 October 2026.

Raw conversations are **not** published. This document contains aggregates and a handful of
paraphrased examples. Order numbers, SKUs, names, phone numbers, addresses and links were
removed, no customer message is quoted verbatim, and the store is referred to as [STORE].

## Summary

| Question | Result (95 % CI) |
|---|---|
| Model-written replies with no visible problem | **83.8 %** (77.5–88.6), 140 of 167 |
| Replies with a likely invented or unsupported fact | 4.2 % (2.0–8.4), 7 of 167 |
| Replies that should have been handed to a human | 3.6 % (1.7–7.6), 6 of 167 |
| Hand-offs initiated by the model that were unnecessary | 25.0 % (12.7–43.4), 7 of 28 |
| Share of all exchanges that ended in a hand-off to the store | 27.7 % (22.3–33.8), 64 of 231 |

Read these as "what one annotator saw in one store over 18 days", not as a general property of
the model. See [Limitations](#limitations) before quoting any number.

## Data

The chat journal (`chat_response_logs`) stores the customer's text, the bot's text and the
reason for every automatic answer and every hand-off since 2 October 2026. The review covers
**every logged exchange from 2 to 19 October** (18 calendar days, 17 of them with traffic). It is
a census of that window, not a random sample.

| Kind of exchange | Count | Scored |
|---|---|---|
| Model reply (`inbound_reply`) | 167 | yes |
| Hand-off initiated by the model (`inbound_escalated_ai`) | 28 | yes |
| Hand-off by a deterministic rule (`inbound_escalated_trigger`) | 36 | no, context only |
| Fixed logistics template (`inbound_logistics_template`) | 8 | no, context only |
| **Total** | **239** | **195** |

The 239 exchanges belong to 135 conversations; 56 conversations have more than one exchange.
All 167 replies came from the same model (`deepseek-chat`) and the same prompt tier. The 44
unscored rows are not model output: they are keyword hand-offs and a canned template, kept in the
sheet so the annotator could see the whole conversation.

The 36 rule-based hand-offs fired on a complaint keyword (18), on an attachment the bot cannot
read (12) or on a store-configured keyword (6). They are not scored here.

## Method

**Unit.** One exchange: a customer message and the bot's answer to it, judged in the context of
the earlier messages of the same conversation.

**Labels.** Every scored exchange got one verdict:

| Verdict | Applies to | Meaning |
|---|---|---|
| ok | reply | Correct and appropriate on the evidence available |
| inexact | reply | Not harmful, but weak: ignored part of the question, contradicted itself, wrong language, awkward |
| unsupported claim | reply | States a fact the bot could not know, or contradicts the data |
| unsupported claim (uncertain) | reply | Looks invented, but could not be ruled out without order or catalogue data |
| should have escalated | reply | The bot answered, a human was needed |
| escalation needed | hand-off | The model's own draft was inadequate or risky; a human was right |
| escalation unnecessary | hand-off | The model's draft was already a good answer |

**Two passes.**
1. A first pass by an LLM (Claude) reading the conversation text only.
2. A manual review of all 195 verdicts by the project owner, who changed what he disagreed with.
   The reviewed labels are the ones reported here.

The reviewer changed 26 of 195 first-pass verdicts (13.3 %). Agreement between the passes was
169 of 195 (86.7 %, CI 81.2–90.7), Cohen's kappa 0.73 over seven classes; collapsed to
"fine / not fine" it was 181 of 195 (92.8 %). In 17 of the 26 changes the reviewer lowered the
severity (mostly "inexact" to "ok"); in 9 he raised it or re-classified it. This is **not**
inter-annotator agreement: the second pass saw the first pass's verdicts, so anchoring will
inflate it.

**Intervals.** Proportions carry Wilson 95 % intervals, which assume independent exchanges.
Exchanges in one conversation are correlated, so for the reply metrics a conversation-level
bootstrap (20,000 resamples of conversations, fixed seed) is shown next to it. The two agree
closely here. Hand-offs come one per conversation, so Wilson alone is used for them.

## Results

### Model replies (n = 167, 94 conversations)

| Verdict | n | Share | Wilson 95 % CI | Conversation-bootstrap CI |
|---|---|---|---|---|
| ok | 140 | 83.8 % | 77.5–88.6 | 78.0–89.4 |
| inexact | 14 | 8.4 % | 5.1–13.6 | 4.7–12.2 |
| unsupported claim | 5 | 3.0 % | 1.3–6.8 | 0.6–6.1 |
| unsupported claim (uncertain) | 2 | 1.2 % | 0.3–4.3 | – |
| should have escalated | 6 | 3.6 % | 1.7–7.6 | 1.2–6.5 |
| *any unsupported claim* | 7 | 4.2 % | 2.0–8.4 | 1.2–7.7 |
| *unsupported claim or missed escalation* | 13 | 7.8 % | 4.6–12.9 | 3.8–12.1 |
| *any issue* | 27 | 16.2 % | 11.4–22.5 | 10.6–22.0 |

### Hand-offs initiated by the model (n = 28, one per conversation)

| Verdict | n | Share | Wilson 95 % CI |
|---|---|---|---|
| escalation needed | 21 | 75.0 % | 56.6–87.3 |
| escalation unnecessary | 7 | 25.0 % | 12.7–43.4 |

One of the seven has a conflicting annotation (the verdict says "unnecessary", the note says the
draft promised something the bot cannot promise). The verdict was kept as recorded; counting it
as "needed" would give 6 of 28 = 21.4 % (10.2–39.5).

How the 28 hand-offs happened matters for the cause (see below): 20 came from the model emitting
its explicit hand-off marker, 8 from a safety net that treats a reply promising a human ("I will
check", "I will pass this on") without the marker as a hand-off. Unnecessary hand-offs: 5 of 20
and 2 of 8.

### Slices

| Slice | n | ok | Wilson 95 % CI |
|---|---|---|---|
| Russian conversations | 139 | 85.6 % | 78.8–90.5 |
| Kazakh conversations | 28 | 75.0 % | 56.6–87.3 |
| Replies to a voice call or voice message | 24 | 100 % | – |
| Everything else | 143 | 81.1 % | 73.9–86.7 |

- Language was detected by Kazakh-specific letters in either the customer's or the bot's text, so
  it is a heuristic. The two intervals overlap; Kazakh is weaker on this sample, not provably so.
- 24 of the 167 replies answer a "voice call" event or an empty message (a voice note). The bot
  asks the customer to write instead; the reviewer accepted all of them. They are easy, so they lift
  the headline number: every one of the 27 problems is in the other 143 replies.

### Hand-off rate and cost

The 64 hand-offs (36 by rule, 28 by the model) over 231 exchanges (replies plus both kinds of
hand-off, the logistics template excluded) is **27.7 %** (22.3–33.8). That is in line with the
weekly shares in the [baseline snapshot of 16 October](AI_BASELINE_2026-10-16.md): 30.6 % for the
week of 5 October and 22.7 % for the partial week of 12 October (10.1 % for the week of 28
September, when hand-offs started to be logged). The baseline also has the cost side: roughly
58–114 model replies a week, about 2,050 prompt tokens and about 45 completion tokens per reply
in mid-October, up from about 1,550 prompt tokens in September when the order-facts block was
added. The baseline is a snapshot taken before the card-facts change described below.

### Before and after card facts

On the evening of 15 October (UTC) the verified product-card facts were switched on for the
auto-responder (`CHATBOT_CARD_FACTS_ENABLED`). The sample splits as:

| Period | Replies | ok | Wilson 95 % CI | Unnecessary hand-offs |
|---|---|---|---|---|
| 2–15 October | 138 | 83.3 % | 76.2–88.6 | 7 of 23 |
| 16–19 October | 29 | 86.2 % | 69.4–94.5 | 0 of 5 |

The intervals overlap almost entirely and the second period is four days. This sample cannot say
whether the flag helped.

## What went wrong, and why

Mechanisms marked "hypothesis" are inferred from the conversations and the code, not tested.

**1. Facts the bot could not know (7 replies).**
- Two replies told a customer on the way to pick up an order that it was "still being assembled"
  when the store had already packed it. The bot reports the marketplace's order state, which did
  not match what had physically happened. (Hypothesis: the state lags behind the store's
  workflow; the bot has no way to know.)
- One reply confirmed "yes, that is the exact item" for a product link the bot cannot open.
- One reply said the store sells a product category it had no data on, then contradicted itself in
  the next message ("not in stock").
- One reply guessed which number in "200x240" is the width, and a later reply in the same
  conversation read the format the other way. (A staff message that preceded the flip is not in the
  log, which is why the second reply is scored only as inexact.)
- Two replies are scored "uncertain": a stock count for a product the bot had no quantity for,
  and a claim about how a product works that conflicts with what other customers reported.

Hypothesis: when the order block and card facts do not contain the answer, the model tends to
answer anyway rather than say it does not know.

**2. A human was needed and the bot answered (6 replies).**
- Two complaints in Kazakh ("a different item was put in", "it arrived broken") matched no
  deterministic rule, so the model answered with a clarifying question. The complaint list in
  `backend/chatbot/prompt.py` (`COMPLAINT_STEMS`) has some Kazakh stems but not these forms.
- One reply invented a limitation ("a technical problem with images in the chat") instead of
  handing a photo request to staff.
- Two replies said they did not know and sent the customer to Kaspi (its support, or the listing)
  for facts only the store can supply: a product dimension and what a set contains.
- One customer was physically lost at the pickup point. The directions were right; the reviewer
  noted that a human should have been called in.

**3. Weak but harmless (14 replies).** Wrong language for a Kazakh greeting, a store-hours answer
that mixed up shipping hours and pickup hours, a repeated refusal four times to an urgent
customer, an invented detail about the Kaspi app's interface, a holiday wish with no basis in the
chat, a phrase it did not understand ("is there a ... stand", mixed Russian and Kazakh), and
similar.

**4. Unnecessary hand-offs (7 of 28).** The generic "a manager will look into it" message replaced
a draft that was already a fine answer, for example "I cannot change the delivery address, the
order is with the courier; ask Kaspi support". Two causes:
- The model emitted the hand-off marker although its draft was complete (5).
- The safety net matched a keyword in the draft (2). `_MANAGER_PROMISE_STEMS` in
  `backend/chatbot/prompt.py` contains the stem for "handed over", which also matches the plain
  report "we handed the order to the courier". The bot's correct answer was thrown away.

**Accepted by the reviewer but notable.** For voice-call events the bot invents a reason ("no
speakers", "technical fault"). The behaviour is useful (it asks the customer to write) but the
wording varies and is sometimes false. A fixed template would be better.

## Examples

All paraphrased; identifying details removed.

| # | Situation | Bot did | Verdict |
|---|---|---|---|
| 1 | Customer pastes a link to a product page and asks "is this exactly the one you sell?" | Confirmed it was the same item, in stock, matching the photos. It cannot open links. | unsupported claim |
| 2 | Customer on the way to collect an order asks if it is packed. | Said it was still being assembled and gave no time. The store had packed it already. | unsupported claim |
| 3 | Customer asks for a real photo of an item. | Said the chat has "a technical problem with images" and pointed to the listing. No such problem existed; staff could have sent the photo. | should have escalated |
| 4 | (kk) Customer: "Why didn't you include the item I ordered? You sent a different one." | Replied in Kazakh asking which item and for the order number, which was already attached. No complaint rule fired. | should have escalated |
| 5 | Customer asks whether "240" in a product's size is the height or the length. | Answered "length × height", while another conversation had been told "width × height" for the same format. | inexact |
| 6 | Customer: "so it has not shipped yet?" The model's draft correctly said the order went to the courier on a given date and tracking is on Kaspi's side. | The safety net matched a keyword in the draft and sent the generic "a manager will look into it". | escalation unnecessary |
| 7 | Customer filed a return and is unsure it was done correctly. The model's draft said "we will see your request and approve it". | Handed off to a human, which stopped the bot from promising an approval it cannot give. | escalation needed |
| 8 | Customer asks until what time pickup is open today. | Compared the current local time with the pickup hours and told them they would be too late. | ok |

## Limitations

- **One store, 18 days.** One catalogue, one customer base, one prompt version, one model. The
  census is small (167 replies, 28 hand-offs) and the intervals are wide.
- **One annotator, on top of LLM labels.** A single person (the project owner, who also wrote the
  system) reviewed the verdicts. There is no second annotator, so there is no real inter-annotator
  agreement and the verdict boundaries (for example "inexact" versus "ok") are subjective. The
  reviewer started from the LLM's labels, which biases the result toward them.
- **The annotator did not have the order data.** The bot saw order facts (status, dates, items);
  the sheet did not. "Unsupported claim" therefore means "looks unsupported", and the uncertain
  class was not resolved. Messages written by store staff in the same conversation are not in the
  log either, so some bot replies lack the context they responded to.
- **Manual review was done once.** There was no second pass over the reviewed labels.
- **"ok" is not "verified correct".** It means no problem was visible to the reviewer. Stock,
  size and price statements were not checked against the catalogue.
- **Exchange-level, not outcome-level.** The review does not measure whether the customer's
  problem was solved or how they felt about it.
- **Not a random sample.** The window is a census of logged exchanges; weekday, season and
  campaign effects are not controlled. Language detection is a heuristic.
- **Deterministic hand-offs are not scored**, so the quality of the keyword rules is only touched
  on, not measured.

This is not a benchmark of the model. The deterministic suite in [`evals/`](../evals/README.md)
covers the staff assistant and does not evaluate model quality at all.

## Follow-ups

The regression set, the calibrated judge, the fixes and the before/after measurement were done on
19–20 October 2026: see [AUTORESPONDER_IMPROVEMENT.md](AUTORESPONDER_IMPROVEMENT.md). The list
below is the original one, as written after this review.


- A second annotator, with order and catalogue facts in the sheet, on the same exchanges.
- Extend the Kazakh complaint stems and make the safety net ignore past-tense reports.
- A fixed template for voice-call events.
- Turn the reviewed exchanges into a regression set that replays the prompt (the private data
  would stay private; only aggregates would be published).
