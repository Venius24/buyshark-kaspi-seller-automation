"""Регрессионный eval ИИ-автоответчика покупателям.

Каждый кейс — обезличенный и пересказанный диалог из ручной разметки
(docs/AUTORESPONDER_EVAL.md) с фактами заказа, которые бот видел на проде.
Кейс прогоняется через прод-код: backend.chatbot.pipeline (проверки до и после
модели), order_context.render_facts_block, ai.generate_ai_reply (настоящий
вызов DeepSeek в режиме --live). Отправленные клиенту ответы оценивает
LLM-судья (judge.py), эскалации сверяются с разметкой детерминированно.

Режимы (из корня репозитория):
  python -m evals.autoresponder.run --offline                 # без сети: кейсы валидны, маршруты до модели
  python -m evals.autoresponder.run --live --repeats 3 --out evals/autoresponder/results/after.json
  python -m evals.autoresponder.run --calibrate               # судья против ручной разметки: kappa
  python -m evals.autoresponder.run --compare results/before.json results/after.json
  python -m evals.autoresponder.run --live --gate             # CI: сравнение с порогами thresholds.json

Ключи: DEEPSEEK_API_KEY (генератор), OPENAI_API_KEY (судья) — из окружения.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CASES = HERE / "cases.jsonl"
PROFILE = HERE / "store_profile.json"
THRESHOLDS = HERE / "thresholds.json"
RESULTS = HERE / "results"

# Ключ генератора берём до импорта backend: backend.chatbot.config при импорте
# подгружает локальный .env и мог бы подменить ключ из окружения CI.
_GENERATOR_KEY = (os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API") or "").strip()

DEFAULT_JUDGE_MODEL = "gpt-5.5"  # выбран калибровкой, см. METRICS_LOG.md раздел 1

HANDOFF_OUTCOMES = {"escalated_trigger", "escalated_ai"}
SENT_OUTCOMES = {"reply", "template"}
ACCEPTABLE = {"ok", "inexact"}

# Ручная разметка -> что должно произойти. None — допустимы и ответ, и эскалация,
# но ответ должен пройти судью (выдумка лучше бы ушла менеджеру, чем клиенту).
EXPECTED_HANDOFF = {
    "ok": False, "inexact": None, "unsupported": None, "unsupported_uncertain": None,
    "should_escalate": True, "escalation_needed": True, "escalation_unnecessary": False,
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def load_cases(path: Path) -> list[dict]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        raise SystemExit(f"{path}: повторяющиеся id")
    return cases


def load_profile(args) -> dict:
    return json.loads(Path(args.profile or PROFILE).read_text(encoding="utf-8"))


def _env():
    # Как на проде (флаги включены 15.10 и 16.10.2026): карточки товаров в блоке фактов.
    os.environ["CHATBOT_CARD_FACTS_ENABLED"] = "1"
    sys.path.insert(0, str(ROOT))
    import backend.chatbot.worker  # noqa: F401  (порядок импорта как в проде, иначе круговой импорт kaspi/notifications)
    import backend.chatbot.ai as ai
    import backend.chatbot.order_context as oc
    try:
        import backend.chatbot.pipeline as pipeline
    except ImportError:  # --rejudge на старой ревизии кода, где маршрутизации ещё не было отдельным модулем
        pipeline = None
    import backend.chatbot.prompt as prompt
    return ai, oc, pipeline, prompt


def _time(case) -> dt.datetime:
    return dt.datetime.fromisoformat(case["time_utc"]).replace(tzinfo=dt.timezone.utc)


def build_inputs(case: dict, oc, ai):
    """-> (facts, facts_block, dialog, pending_media). Тот же путь, что в воркере."""
    f = case["facts"]
    facts = oc.ChatFacts()
    facts.order_code = f.get("order_code")
    facts.order = f.get("order")
    facts.order_lookup_failed = bool(f.get("order_lookup_failed"))
    facts.products = f.get("products") or []
    facts.catalog_matches = f.get("catalog_matches") or []
    facts_block = oc.render_facts_block(facts)

    T = _time(case)
    ms = int(T.timestamp() * 1000)
    history = case.get("history") or []
    raw = [{"sender": m["sender"], "text": m.get("text") or "", "messageType": m.get("messageType", "TEXT"),
            "created": ms - 60000 * (len(history) - i)} for i, m in enumerate(history)]
    media = case.get("client_media")
    raw.append({"sender": "messenger", "text": case.get("client_text") or "", "created": ms,
                "messageType": media or "TEXT"})
    bot_texts = {m["text"].strip() for m in raw if m["sender"] == "client" and m["text"]}
    dialog = ai.build_dialog(raw, bot_texts, None)
    pending_media = ["голосовое"] if media == "AUDIO" else []
    return facts, facts_block, dialog, pending_media


def system_prompt_for(case, profile, facts_block, prompt) -> str:
    return prompt.render_system_prompt(
        profile, case.get("client_name") or "Клиент", case.get("item_title") or "Товар",
        awaiting_review_reply=bool(case.get("awaiting_review_reply")), facts_block=facts_block, now=_time(case),
    )


async def run_case(case: dict, profile: dict, mods, live: bool) -> dict:
    ai, oc, pipeline, prompt = mods
    facts, facts_block, dialog, pending_media = build_inputs(case, oc, ai)
    out = {"outcome": None, "detail": None, "reply": None, "usage": {}}

    route = pipeline.route_before_model(case.get("client_text") or "", profile, facts, pending_media)
    if route.action == "escalate":
        out.update(outcome="escalated_trigger", detail=route.detail, action="escalate")
        return out
    if route.action == "template":
        out.update(outcome="template", detail=route.detail, reply=route.reply, action="answer")
        return out
    if not live:
        out.update(outcome="model_not_called")
        return out

    reply, override, usage = await ai.generate_ai_reply(
        case.get("client_name") or "Клиент", case.get("item_title") or "Товар", dialog,
        store_id=None, awaiting_review_reply=bool(case.get("awaiting_review_reply")),
        facts_block=facts_block, client_text=case.get("client_text") or "", profile=profile, now=_time(case),
    )
    out["usage"] = usage or {}
    if not usage or usage.get("error"):
        out.update(outcome="api_error", detail=(usage or {}).get("error"))
        return out
    escalation = pipeline.route_after_model(reply, override)
    # model_action — что выбрала модель; action — что произошло (проверки после модели могут
    # превратить answer в escalate, например обещание «передам менеджеру»).
    out["model_action"] = usage.get("action")
    if escalation:
        out.update(outcome="escalated_ai", detail=escalation, reply=reply, action="escalate")
    else:
        out.update(outcome="reply", reply=reply, action=usage.get("action") or "answer")
    return out


async def judge_run(case, profile, mods, run: dict, judge_model: str) -> None:
    from evals.autoresponder.judge import LLMError, judge_cost_usd, judge_reply

    ai, oc, _, prompt = mods
    _, facts_block, dialog, _ = build_inputs(case, oc, ai)
    try:
        verdict, trace = await judge_reply(system_prompt_for(case, profile, facts_block, prompt), dialog, run["reply"],
                                           model=judge_model)
        run["judge"] = {**verdict, "trace": trace, "cost_usd": judge_cost_usd(trace)}
    except LLMError as e:
        run["judge"] = {"error": str(e)}


ACTIONS = ("answer", "clarify", "safe_unknown", "escalate")
VALID_OUTCOMES = HANDOFF_OUTCOMES | SENT_OUTCOMES


def run_action(run) -> str | None:
    """Действие прогона. У прогонов до 2026-10-20 (без поля action) выводится из исхода:
    эскалация -> escalate, отправленный ответ -> answer."""
    if run["outcome"] not in VALID_OUTCOMES:
        return None
    if run.get("action") in ACTIONS:
        return run["action"]
    return "escalate" if run["outcome"] in HANDOFF_OUTCOMES else "answer"


def decision_metrics(cases: list, results: dict) -> dict:
    """Метрики решений бота (раздел 5 METRICS_LOG), считаются по прогонам:
    answer_quality — доля годных (ok/inexact по судье) среди отправленных клиенту ответов;
    escalation_recall — доля эскалаций там, где по разметке нужен человек;
    escalation_precision — среди эскалаций в кейсах с известным ответом (нужен / не нужен человек)
      доля тех, где человек нужен; эскалации в спорных кейсах (inexact/unsupported) показаны отдельно;
    autonomy_rate — доля прогонов, закрытых ботом без менеджера;
    resolved_by_bot — доля прогонов, где бот ответил сам и судья счёл ответ годным."""
    by_id = {c["id"]: c for c in cases}
    actions, by_expected = Counter(), Counter()
    valid = autonomous = sent_judged = sent_ok = sent_acceptable = 0
    for cid, runs in results.items():
        expected = EXPECTED_HANDOFF[by_id[cid]["label"]["verdict"]]
        for r in runs:
            a = run_action(r)
            if a is None:
                continue
            valid += 1
            actions[a] += 1
            esc = a == "escalate"
            by_expected[(expected, esc)] += 1
            if not esc:
                autonomous += 1
                verdict = (r.get("judge") or {}).get("verdict")
                if verdict:
                    sent_judged += 1
                    sent_ok += verdict == "ok"
                    sent_acceptable += verdict in ACCEPTABLE
    need_hit, need_all = by_expected[(True, True)], by_expected[(True, True)] + by_expected[(True, False)]
    esc_known = by_expected[(True, True)] + by_expected[(False, True)]
    esc_all = sum(v for (e, esc), v in by_expected.items() if esc)
    return {
        "runs": valid,
        "actions": {a: actions[a] for a in ACTIONS},
        "actions_pct": {a: _pct(actions[a] / valid) if valid else None for a in ACTIONS},
        "answer_quality_pct": _pct(sent_acceptable / sent_judged) if sent_judged else None,
        "answer_ok_pct": _pct(sent_ok / sent_judged) if sent_judged else None,
        "answer_judged_n": sent_judged,
        "escalation_recall_pct": _pct(need_hit / need_all) if need_all else None,
        "escalation_recall_n": [need_hit, need_all],
        "escalation_precision_pct": _pct(need_hit / esc_known) if esc_known else None,
        "escalation_precision_n": [need_hit, esc_known],
        "escalations_on_disputed_n": [by_expected[(None, True)], esc_all],
        "autonomy_rate_pct": _pct(autonomous / valid) if valid else None,
        "autonomy_n": [autonomous, valid],
        "resolved_by_bot_pct": _pct(sent_acceptable / valid) if valid and sent_judged else None,
    }


def case_success(expected, run) -> bool | None:
    """Успех одного прогона кейса. None — прогон не оценить (сбой API/судьи)."""
    outcome = run["outcome"]
    if outcome in HANDOFF_OUTCOMES:
        return expected is not False
    if outcome in SENT_OUTCOMES:
        if expected is True:
            return False
        verdict = (run.get("judge") or {}).get("verdict")
        if verdict is None:
            return None
        return verdict in ACCEPTABLE
    return None


def _r3(x):
    return None if x is None else round(x, 3)


def _pct(x):
    return None if x is None else round(100 * x, 1)


def summarize(cases: list, results: dict) -> dict:
    from evals.autoresponder.metrics import wilson

    by_id = {c["id"]: c for c in cases}
    case_scores, outcomes, verdicts = {}, Counter(), Counter()
    handoff_need = handoff_need_hit = handoff_not = handoff_not_hit = 0
    prompt_tokens, completion_tokens, latencies, costs, judge_costs = [], [], [], [], []
    slices = defaultdict(list)
    for cid, runs in results.items():
        case = by_id[cid]
        expected = EXPECTED_HANDOFF[case["label"]["verdict"]]
        scores = []
        for r in runs:
            outcomes[r["outcome"]] += 1
            if r["outcome"] in SENT_OUTCOMES and (r.get("judge") or {}).get("verdict"):
                verdicts[r["judge"]["verdict"]] += 1
            if expected is True and r["outcome"] not in ("api_error", "model_not_called"):
                handoff_need += 1
                handoff_need_hit += r["outcome"] in HANDOFF_OUTCOMES
            if expected is False and r["outcome"] not in ("api_error", "model_not_called"):
                handoff_not += 1
                handoff_not_hit += r["outcome"] in HANDOFF_OUTCOMES
            u = r.get("usage") or {}
            if u.get("prompt_tokens") is not None:
                prompt_tokens.append(u["prompt_tokens"])
                completion_tokens.append(u.get("completion_tokens") or 0)
            if u.get("latency_ms") is not None:
                latencies.append(u["latency_ms"])
            if u.get("cost_usd") is not None:
                costs.append(u["cost_usd"])
            if (r.get("judge") or {}).get("cost_usd") is not None:
                judge_costs.append(r["judge"]["cost_usd"])
            s = case_success(expected, r)
            if s is not None:
                scores.append(1.0 if s else 0.0)
        if scores:
            case_scores[cid] = sum(scores) / len(scores)
            for tag in [f"lang:{case.get('lang', '?')}", f"label:{case['label']['verdict']}", *case.get("tags", [])]:
                slices[tag].append(case_scores[cid])

    n = len(case_scores)
    mean = sum(case_scores.values()) / n if n else None
    sent = sum(verdicts.values())
    lo, hi = _bootstrap_ci(list(case_scores.values()))
    summary = {
        "cases_scored": n,
        "success_rate_pct": _pct(mean),
        "success_rate_ci95_pct": [_pct(lo), _pct(hi)],
        "handoff_recall_pct": _pct(handoff_need_hit / handoff_need if handoff_need else None),
        "handoff_recall_n": [handoff_need_hit, handoff_need],
        "false_handoff_pct": _pct(handoff_not_hit / handoff_not if handoff_not else None),
        "false_handoff_n": [handoff_not_hit, handoff_not],
        "sent_replies_judged": sent,
        "judge_verdicts": dict(verdicts),
        "unsupported_pct_of_sent": _pct(verdicts["unsupported"] / sent if sent else None),
        "unsupported_ci95_pct": [_pct(x) for x in wilson(verdicts["unsupported"], sent)] if sent else None,
        "should_escalate_pct_of_sent": _pct(verdicts["should_escalate"] / sent if sent else None),
        "outcomes": dict(outcomes),
        "tokens": {
            "prompt_mean": round(statistics.mean(prompt_tokens)) if prompt_tokens else None,
            "completion_mean": round(statistics.mean(completion_tokens), 1) if completion_tokens else None,
        },
        "latency_ms": {
            "p50": int(statistics.median(latencies)) if latencies else None,
            "p95": int(sorted(latencies)[max(0, int(0.95 * len(latencies)) - 1)]) if latencies else None,
        },
        "llm_parse": dict(Counter((r.get("usage") or {}).get("structured") for runs in results.values() for r in runs
                                  if (r.get("usage") or {}).get("structured"))),
        "llm_retries": sum(max(0, ((r.get("usage") or {}).get("attempts") or 1) - 1) for runs in results.values() for r in runs),
        "api_errors": outcomes["api_error"],
        "generator_cost_usd": round(sum(costs), 4) if costs else None,
        "judge_cost_usd": round(sum(judge_costs), 4) if judge_costs else None,
        "slices": {k: {"n": len(v), "success_pct": _pct(sum(v) / len(v))} for k, v in sorted(slices.items())},
        "decisions": decision_metrics(cases, results),
    }
    return summary


def _bootstrap_ci(values: list[float], iters: int = 10000, seed: int = 7):
    import random

    if not values:
        return None, None
    rnd = random.Random(seed)
    n = len(values)
    means = sorted(sum(values[rnd.randrange(n)] for _ in range(n)) / n for _ in range(iters))
    return means[int(0.025 * iters)], means[int(0.975 * iters) - 1]


async def cmd_run(args) -> int:
    cases = load_cases(Path(args.cases))
    if args.limit:
        cases = cases[: args.limit]
    if args.only:
        keep = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in keep]
    profile = load_profile(args)
    mods = _env()
    live = bool(args.live)
    if live:
        if not _GENERATOR_KEY:
            print("нет DEEPSEEK_API_KEY в окружении", file=sys.stderr)
            return 3
        os.environ["DEEPSEEK_API_KEY"] = os.environ["DEEPSEEK_API"] = _GENERATOR_KEY
    judge_model = args.judge_model or os.environ.get("AUTORESPONDER_JUDGE_MODEL") or DEFAULT_JUDGE_MODEL
    use_judge = live and not args.no_judge
    if use_judge and not judge_model.startswith("codex:") and not os.environ.get("OPENAI_API_KEY"):
        print("нет OPENAI_API_KEY: судья не запущен (--no-judge, чтобы явно прогнать без него)", file=sys.stderr)
        return 3

    sem = asyncio.Semaphore(args.concurrency)
    results: dict[str, list] = {c["id"]: [] for c in cases}
    errors = 0

    async def one(case, k):
        nonlocal errors
        async with sem:
            try:
                run = await run_case(case, profile, mods, live)
            except Exception as e:  # кейс не должен ронять прогон
                errors += 1
                run = {"outcome": "exception", "detail": f"{type(e).__name__}: {e}"}
            if use_judge and run["outcome"] in SENT_OUTCOMES and run.get("reply"):
                await judge_run(case, profile, mods, run, judge_model)
            run["repeat"] = k
            results[case["id"]].append(run)

    started = time.time()
    repeats = args.repeats if live else 1
    await asyncio.gather(*(one(c, k) for c in cases for k in range(repeats)))
    for runs in results.values():
        runs.sort(key=lambda r: r["repeat"])

    summary = summarize(cases, results) if live else None
    routes = Counter(r["outcome"] for runs in results.values() for r in runs)
    meta = {
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "git_sha": _git_sha(), "cases_file": Path(args.cases).resolve().relative_to(ROOT).as_posix() if Path(args.cases).resolve().is_relative_to(ROOT) else args.cases,
        "cases_sha": _sha(Path(args.cases)), "cases": len(cases), "repeats": repeats, "live": live,
        "generator_model": os.environ.get("CHATBOT_LLM_MODEL", "deepseek-chat"),
        "structured_output": os.environ.get("CHATBOT_LLM_STRUCTURED", "1") != "0",
        "prompt_version": _prompt_version(),
        "judge_model": judge_model if use_judge else None, "label": args.label,
        "seconds": round(time.time() - started, 1), "exceptions": errors,
    }
    report = {"meta": meta, "summary": summary, "routes": dict(routes),
              "cases": [{"id": c["id"], "label": c["label"]["verdict"], "runs": results[c["id"]]} for c in cases]}
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"результат: {args.out}")
    print(json.dumps({"meta": meta, "routes": dict(routes)}, ensure_ascii=False, indent=1))
    if summary:
        print(json.dumps({k: v for k, v in summary.items() if k != "slices"}, ensure_ascii=False, indent=1))
    if errors:
        print(f"исключений: {errors}", file=sys.stderr)
        return 1
    if args.gate and summary:
        return gate(summary)
    return 0


def gate(summary: dict) -> int:
    """CI: прогон хуже порогов -> код 1. Пороги взяты ниже замера «после» с запасом на шум."""
    t = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    failed = []
    if summary["success_rate_pct"] is None or summary["success_rate_pct"] < t["min_success_rate_pct"]:
        failed.append(f"success_rate {summary['success_rate_pct']} < {t['min_success_rate_pct']}")
    if summary["handoff_recall_pct"] is not None and summary["handoff_recall_pct"] < t["min_handoff_recall_pct"]:
        failed.append(f"handoff_recall {summary['handoff_recall_pct']} < {t['min_handoff_recall_pct']}")
    if summary["unsupported_pct_of_sent"] is not None and summary["unsupported_pct_of_sent"] > t["max_unsupported_pct_of_sent"]:
        failed.append(f"unsupported {summary['unsupported_pct_of_sent']} > {t['max_unsupported_pct_of_sent']}")
    if summary["false_handoff_pct"] is not None and summary["false_handoff_pct"] > t["max_false_handoff_pct"]:
        failed.append(f"false_handoff {summary['false_handoff_pct']} > {t['max_false_handoff_pct']}")
    for f in failed:
        print("GATE FAIL:", f, file=sys.stderr)
    print("GATE:", "FAIL" if failed else "PASS")
    return 1 if failed else 0


# ----------------------------------------------------------------------------- калибровка судьи
def conv_split(case) -> str:
    """dev — половина диалогов, на которой правится рубрика судьи; test — отложенная половина для отчёта."""
    return "dev" if int(hashlib.sha256(case.get("conv", case["id"]).encode()).hexdigest(), 16) % 2 == 0 else "test"


HUMAN_REPLY_CLASS = {"ok": "ok", "inexact": "inexact", "unsupported": "unsupported",
                     "unsupported_uncertain": "unsupported", "should_escalate": "should_escalate"}


async def cmd_calibrate(args) -> int:
    from evals.autoresponder.judge import judge_cost_usd, judge_reply, LLMError
    from evals.autoresponder.metrics import cohen_kappa, confusion, wilson

    cases = [c for c in load_cases(Path(args.cases)) if (c.get("original") or {}).get("reply")]
    if args.fidelity:
        cases = [c for c in cases if c.get("fidelity") in set(args.fidelity.split(","))]
    if args.split != "all":
        cases = [c for c in cases if conv_split(c) == args.split]
    if args.limit:
        cases = cases[: args.limit]
    profile = load_profile(args)
    mods = _env()
    ai, oc, _, prompt = mods
    model = args.judge_model or os.environ.get("AUTORESPONDER_JUDGE_MODEL") or DEFAULT_JUDGE_MODEL
    sem = asyncio.Semaphore(args.concurrency)
    rows = []

    async def one(case):
        _, facts_block, dialog, _ = build_inputs(case, oc, ai)
        # Факты заказа восстановлены как на проде только для уровней A и D; иначе судья их не видит.
        visible = case.get("fidelity", "A") in ("A", "D")
        sys_prompt = system_prompt_for(case if visible else {**case, "item_title": "(неизвестно)"}, profile,
                                       facts_block if visible else "", prompt)
        async with sem:
            try:
                verdict, trace = await judge_reply(sys_prompt, dialog, case["original"]["reply"], model=model,
                                                   facts_visible=visible,
                                                   truncated_draft=case["original"]["outcome"] == "escalated_ai")
            except LLMError as e:
                print(f"{case['id']}: {e}", file=sys.stderr)
                return
        rows.append({"id": case["id"], "kind": case["original"]["outcome"], "fidelity": case.get("fidelity"), "human": case["label"]["verdict"],
                     "judge": verdict["verdict"], "claims": verdict["unsupported_claims"], "rationale": verdict["rationale"],
                     "cost_usd": judge_cost_usd(trace)})

    started = time.time()
    await asyncio.gather(*(one(c) for c in cases))
    rows.sort(key=lambda r: r["id"])

    replies = [r for r in rows if r["kind"] == "reply"]
    h4 = [HUMAN_REPLY_CLASS[r["human"]] for r in replies]
    j4 = [r["judge"] for r in replies]
    # «Черновик годился» — общий бинарный вопрос и для ответов, и для эскалаций:
    # у ответа ok/inexact, у эскалации «лишняя».
    def human_ok(r):
        return r["human"] in ("ok", "inexact", "escalation_unnecessary")
    hb = [human_ok(r) for r in rows]
    jb = [r["judge"] in ACCEPTABLE for r in rows]
    hs = [r["human"] == "ok" for r in replies]
    js = [r["judge"] == "ok" for r in replies]
    agree4 = sum(a == b for a, b in zip(h4, j4))
    agreeb = sum(a == b for a, b in zip(hb, jb))
    report = {
        "meta": {"created_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "git_sha": _git_sha(),
                 "cases_sha": _sha(Path(args.cases)), "judge_model": model, "label": args.label,
                 "split": args.split, "fidelity": args.fidelity, "rubric_sha": rubric_sha(),
                 "seconds": round(time.time() - started, 1),
                 "cost_usd": round(sum(r["cost_usd"] or 0 for r in rows), 4)},
        "replies_4class": {
            "n": len(replies), "agreement": agree4, "agreement_pct": _pct(agree4 / len(replies)) if replies else None,
            "agreement_ci95_pct": [_pct(x) for x in wilson(agree4, len(replies))],
            "kappa": None if not replies else _r3(cohen_kappa(h4, j4)),
            "confusion_human_rows_judge_cols": confusion(h4, j4, ["ok", "inexact", "unsupported", "should_escalate"]),
        },
        "replies_ok_vs_rest": {"n": len(replies), "kappa": None if not replies else _r3(cohen_kappa(hs, js))},
        "all_draft_acceptable": {
            "n": len(rows), "agreement": agreeb, "agreement_pct": _pct(agreeb / len(rows)) if rows else None,
            "kappa": None if not rows else _r3(cohen_kappa(hb, jb)),
        },
        "rows": rows,
    }
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"результат: {args.out}")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, ensure_ascii=False, indent=1))
    return 0


async def cmd_rejudge(args) -> int:
    """Новый судья на уже сгенерированных ответах прогона: генератор не вызывается, исходный файл
    не меняется. Системный промпт для судьи строится кодом текущей ревизии — чтобы «до» судилось
    по правилам «до», запускайте из рабочей копии на той ревизии (git worktree)."""
    src = json.loads(Path(args.rejudge).read_text(encoding="utf-8"))
    cases = {c["id"]: c for c in load_cases(Path(args.cases))}
    profile = load_profile(args)
    mods = _env()
    judge_model = args.judge_model or os.environ.get("AUTORESPONDER_JUDGE_MODEL") or DEFAULT_JUDGE_MODEL
    sem = asyncio.Semaphore(args.concurrency)
    results: dict[str, list] = {}
    # Вердикты пишутся по одному в <out>.partial.jsonl: оборванный прогон продолжается с места обрыва.
    partial = Path((args.out or args.rejudge) + ".partial.jsonl")
    done = {}
    if partial.exists():
        for line in partial.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                if rec["judge_model"] == judge_model:
                    done[(rec["id"], rec["repeat"], rec["reply"])] = rec["judge"]
        print(f"из {partial.name} взято вердиктов: {len(done)}", file=sys.stderr)

    async def one(case, run):
        key = (case["id"], run.get("repeat"), run["reply"])
        if key in done and "error" not in done[key]:
            run["judge"] = done[key]
            return
        async with sem:
            await judge_run(case, profile, mods, run, judge_model)
        with partial.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"id": case["id"], "repeat": run.get("repeat"), "reply": run["reply"],
                                "judge_model": judge_model, "judge": run["judge"]}, ensure_ascii=False) + "\n")

    jobs = []
    for entry in src["cases"]:
        case = cases[entry["id"]]
        runs = [{k: v for k, v in r.items() if k != "judge"} for r in entry["runs"]]
        results[entry["id"]] = runs
        jobs += [one(case, r) for r in runs if r["outcome"] in SENT_OUTCOMES and r.get("reply")]
    started = time.time()
    await asyncio.gather(*jobs)
    kept = [cases[i] for i in results]
    summary = summarize(kept, results)
    meta = {**src["meta"], "judge_model": judge_model, "rubric_sha": rubric_sha(),
            "rejudged_from": args.rejudge, "rejudge_git_sha": _git_sha(),
            "rejudged_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "rejudge_seconds": round(time.time() - started, 1),
            "label": args.label or src["meta"].get("label")}
    report = {"meta": meta, "summary": summary, "routes": src.get("routes"),
              "cases": [{"id": i, "label": cases[i]["label"]["verdict"], "runs": results[i]} for i in results]}
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"результат: {args.out}")
    print(json.dumps({k: v for k, v in summary.items() if k != "slices"}, ensure_ascii=False, indent=1))
    return 0


def cmd_summary(args) -> int:
    """Пересчитать сводку готового прогона текущим кодом метрик (новые метрики для старых прогонов).
    Исходный файл не меняется; с --out пишется копия с новой сводкой."""
    src = json.loads(Path(args.summary).read_text(encoding="utf-8"))
    cases = {c["id"]: c for c in load_cases(Path(args.cases))}
    results = {c["id"]: c["runs"] for c in src["cases"] if c["id"] in cases}
    summary = summarize([cases[i] for i in results], results)
    if args.out:
        out = {**src, "summary": summary,
               "meta": {**src["meta"], "resummarized_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}}
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"результат: {args.out}")
    print(json.dumps({"meta": {k: src["meta"].get(k) for k in ("label", "git_sha", "prompt_version", "judge_model")},
                      "success_rate_pct": summary["success_rate_pct"], "decisions": summary["decisions"]},
                     ensure_ascii=False, indent=1))
    return 0


# ----------------------------------------------------------------------------- сравнение прогонов
def _prompt_version():
    try:
        from backend.chatbot.prompt import prompt_fingerprint
        return prompt_fingerprint()
    except ImportError:  # код до появления отпечатка
        return None


def rubric_sha() -> str:
    from evals.autoresponder.judge import RUBRIC
    return hashlib.sha256(RUBRIC.encode()).hexdigest()[:10]


def cmd_compare(args) -> int:
    from evals.autoresponder.metrics import mcnemar_exact, paired_bootstrap_diff

    a = json.loads(Path(args.compare[0]).read_text(encoding="utf-8"))
    b = json.loads(Path(args.compare[1]).read_text(encoding="utf-8"))
    cases = {c["id"]: c for c in load_cases(Path(args.cases))}

    def per_case(rep):
        out = {}
        for c in rep["cases"]:
            expected = EXPECTED_HANDOFF[c["label"]]
            s = [case_success(expected, r) for r in c["runs"]]
            s = [1.0 if x else 0.0 for x in s if x is not None]
            if s:
                out[c["id"]] = sum(s) / len(s)
        return out

    pa, pb = per_case(a), per_case(b)
    common = sorted(set(pa) & set(pb) & set(cases))
    before = [pa[i] for i in common]
    after = [pb[i] for i in common]
    diff, lo, hi = paired_bootstrap_diff(before, after)
    worse = sum(1 for x, y in zip(before, after) if x >= 0.5 > y)
    better = sum(1 for x, y in zip(before, after) if x < 0.5 <= y)
    rows = []
    for key in ("success_rate_pct", "handoff_recall_pct", "false_handoff_pct", "unsupported_pct_of_sent",
                "should_escalate_pct_of_sent"):
        rows.append((key, a["summary"].get(key), b["summary"].get(key)))
    def decisions(rep):  # у прогонов до раздела 5 METRICS_LOG сводки решений нет — считаем на лету
        if rep["summary"].get("decisions"):
            return rep["summary"]["decisions"]
        results = {c["id"]: c["runs"] for c in rep["cases"] if c["id"] in cases}
        return decision_metrics([cases[i] for i in results], results)

    da, db = decisions(a), decisions(b)
    for key in ("answer_quality_pct", "escalation_recall_pct", "escalation_precision_pct", "autonomy_rate_pct",
                "resolved_by_bot_pct"):
        rows.append((key, da.get(key), db.get(key)))
    for act in ACTIONS:
        rows.append((f"action_{act}_pct", (da.get("actions_pct") or {}).get(act), (db.get("actions_pct") or {}).get(act)))
    rows.append(("prompt_tokens_mean", a["summary"]["tokens"]["prompt_mean"], b["summary"]["tokens"]["prompt_mean"]))
    rows.append(("latency_p50_ms", a["summary"]["latency_ms"]["p50"], b["summary"]["latency_ms"]["p50"]))
    print(f"{'метрика':32} {'до':>8} {'после':>8}")
    for k, x, y in rows:
        print(f"{k:32} {str(x):>8} {str(y):>8}")
    print(f"\nпарно по {len(common)} кейсам: разница успеха {diff * 100:+.1f} п.п. (95 % бутстрэп {lo * 100:+.1f}…{hi * 100:+.1f})")
    print(f"кейсы (большинство повторов): стало лучше {better}, стало хуже {worse}, Макнемар p = {mcnemar_exact(worse, better):.4f}")
    if args.out:
        Path(args.out).write_text(json.dumps({
            "before": args.compare[0], "after": args.compare[1], "cases": len(common),
            "success_diff_pp": round(diff * 100, 1), "success_diff_ci95_pp": [round(lo * 100, 1), round(hi * 100, 1)],
            "cases_better": better, "cases_worse": worse, "mcnemar_p": round(mcnemar_exact(worse, better), 4),
            "metrics": {k: {"before": x, "after": y} for k, x, y in rows},
        }, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--offline", action="store_true", help="без сети: маршруты до модели, модель не вызывается")
    mode.add_argument("--live", action="store_true", help="настоящий DeepSeek + судья")
    mode.add_argument("--calibrate", action="store_true", help="судья на исходных ответах против ручной разметки")
    mode.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    mode.add_argument("--rejudge", metavar="RESULTS", help="новый судья на ответах готового прогона (без генератора)")
    mode.add_argument("--summary", metavar="RESULTS", help="пересчитать сводку готового прогона текущими метриками")
    ap.add_argument("--cases", default=str(CASES))
    ap.add_argument("--profile", default=None, help="профиль магазина (по умолчанию store_profile.json)")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only", default=None, help="id кейсов через запятую")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--judge-model", default=None)
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--label", default=None, help="подпись прогона в meta (например, before/after)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--split", choices=("dev", "test", "all"), default="all", help="--calibrate: половина диалогов")
    ap.add_argument("--fidelity", default=None, help="--calibrate: уровни восстановления фактов, например A,D")
    ap.add_argument("--gate", action="store_true", help="сверить с thresholds.json, код 1 при провале")
    args = ap.parse_args()
    if args.compare:
        return cmd_compare(args)
    if args.calibrate:
        return asyncio.run(cmd_calibrate(args))
    if args.rejudge:
        return asyncio.run(cmd_rejudge(args))
    if args.summary:
        return cmd_summary(args)
    return asyncio.run(cmd_run(args))


if __name__ == "__main__":
    sys.exit(main())
