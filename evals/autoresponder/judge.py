"""LLM-судья для ответов автоответчика.

Судья — модель другого семейства, чем генератор (DeepSeek) и первый проход
ручной разметки (Claude): так он меньше подыгрывает и тому, и другому.
Насколько ему можно верить, считает `run.py --calibrate`: каппа против
ручной разметки владельца.

Рубрика повторяет легенду разметки (.tmp/labeling/LEGEND.md) и поправки,
которые владелец внёс при ручном пересмотре (docs/AUTORESPONDER_EVAL.md).
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path

import httpx

VERDICTS = ["ok", "inexact", "unsupported", "should_escalate"]
DEFAULT_JUDGE_MODEL = "gpt-5.5"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

# Цены за 1M токенов, USD (для отчёта о стоимости прогона; без тарифа — None).
JUDGE_PRICES = {
    "gpt-5.4-mini": (0.75, 4.5),
    "gpt-5.4": (2.5, 15.0),
    "gpt-5.5": (5.0, 30.0),
    "gpt-4.1-mini": (0.4, 1.6),
    "gpt-4.1": (2.0, 8.0),
}

RUBRIC = """Ты проверяешь ответ чат-бота магазина на Kaspi.kz покупателю. Бот отвечает от имени магазина.
Ниже: инструкции и данные, которые были у бота (системный промпт), переписка и проверяемый ответ бота.
Оцени ТОЛЬКО проверяемый ответ, учитывая всю переписку до него.

Вердикты (выбери один):
- ok — ответ верный и уместный по имеющимся у бота данным и правилам магазина.
- inexact — не вредно, но слабо: ответил не на весь вопрос, противоречит своему же прошлому ответу, не тот язык
  (клиент пишет по-казахски, а бот по-русски, или наоборот), неуклюже, повторяет одно и то же,
  безобидный домысел без опоры в чате (например, поздравил с праздником, о котором речи не было).
- unsupported — бот утверждает факт, которого нет в его данных, правилах или переписке, либо противоречит им:
  наличие или количество товара без данных об остатке, характеристики, которых нет в карточке,
  «это тот самый товар» по ссылке или фото (бот не открывает ссылки и не видит фото), статус заказа,
  которого нет в данных, путь по меню приложения Kaspi, выдуманная техническая причина
  («проблема с изображениями в чате»), обещание отправить N штук или вернуть деньги.
- should_escalate — бот ответил сам, а нужен был сотрудник магазина: жалоба (пришёл не тот товар,
  сломан, не хватает), клиент просит прислать реальное фото или видео товара, клиент спрашивает то,
  что знает только магазин (размер, комплектация), и в данных этого нет, а бот отправляет его в Kaspi Гид
  или в карточку; клиент не может найти пункт выдачи на месте; срочная просьба, на которую бот
  несколько раз отвечает одно и то же; бот обещает «уточню», «проверю», «посмотрю» — проверить он не может,
  значит, нужен человек.

Что считается нормой (не снижай за это оценку):
- На голосовое сообщение или звонок бот просит написать текстом и ссылается на неполадку со звуком —
  это правило магазина, ответ ok.
- Вопросы о курьере, сроках и адресе доставки Kaspi бот переадресует в Kaspi Гид — это верно, менеджер тут
  не нужен: магазин не может поменять ни адрес, ни дату курьера.
- Возвраты и изменения заказа оформляются через Kaspi.kz — так сказано в правилах магазина.
- Бот не называет точное число штук на складе, только «есть / нет в наличии» — так и должно быть.
- Факты из блока «ДАННЫЕ ИЗ СИСТЕМЫ» в системном промпте бота достоверны: ответ, опирающийся на них, не выдумка.

- Общеизвестные правила Kaspi не выдумка: уведомления о заказе приходят в приложении Kaspi, логистикой занимается
  Kaspi Гид, возврат оформляется в Kaspi.kz, заказ может забрать другой человек по коду.
- Название товара из строки «по поводу товара» в начале системного промпта бота — это название чата, бот его знает.
- Мелкие отступления от стиля, которые не мешают покупателю (повторное «Здравствуйте» или «Сәлеметсіз бе»,
  чуть длиннее нужного), — это ok, не inexact.

Если ошибок несколько, выбирай самую тяжёлую: should_escalate и unsupported тяжелее inexact.
В unsupported_claims перечисли утверждения без опоры (пусто, если таких нет). Пиши кратко, по-русски."""

SCHEMA = {
    "name": "reply_verdict",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "verdict": {"type": "string", "enum": VERDICTS},
            "unsupported_claims": {"type": "array", "items": {"type": "string"}},
            "rationale": {"type": "string"},
        },
        "required": ["verdict", "unsupported_claims", "rationale"],
    },
}


class LLMError(RuntimeError):
    pass


# ----------------------------------------------------------------------------- судья через Codex CLI
# model="codex:<модель>[:<effort>]", например codex:gpt-6-luna:low — вызов `codex exec` по подписке
# ChatGPT, без API-кредитов. Стоимость в долларах не считается; расход — доля 5-часового лимита Codex,
# её видно в rollout-файле сессии (rate_limits.primary.used_percent). Каждый CODEX_PROBE_EVERY-й вызов
# идёт без --ephemeral, и из его rollout читается текущий процент (остальные сессии не сохраняются).
CODEX_PROBE_EVERY = 15
CODEX_RATE: dict = {"calls": 0, "primary_used_percent": None, "secondary_used_percent": None, "resets_at": None}
_codex_workdir = Path(tempfile.gettempdir()) / "autoresponder_codex_judge"


def codex_bin() -> str:
    explicit = os.environ.get("CODEX_BIN")
    if explicit:
        return explicit
    found = sorted(glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "OpenAI", "Codex", "bin", "*", "codex.exe")),
                   key=os.path.getmtime)
    if not found:
        raise LLMError("codex.exe не найден (задайте CODEX_BIN)")
    return found[-1]


def _read_rate_limits(thread_id: str) -> None:
    sessions = Path.home() / ".codex" / "sessions"
    for f in sorted(sessions.rglob(f"*{thread_id}*.jsonl")):
        for line in reversed(f.read_text(encoding="utf-8", errors="replace").splitlines()):
            if '"rate_limits"' not in line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            rl = (obj.get("payload") or {}).get("rate_limits") or obj.get("rate_limits")
            if isinstance(rl, dict) and rl.get("primary"):
                CODEX_RATE.update(primary_used_percent=rl["primary"].get("used_percent"),
                                  resets_at=rl["primary"].get("resets_at"),
                                  secondary_used_percent=(rl.get("secondary") or {}).get("used_percent"))
                print(f"[codex] вызовов {CODEX_RATE['calls']}, 5ч-лимит {CODEX_RATE['primary_used_percent']}%, "
                      f"недельный {CODEX_RATE['secondary_used_percent']}%", file=sys.stderr, flush=True)
                return


async def codex_json(spec: str, messages: list, schema: dict, *, timeout: float = 240.0,
                     retries: int = 2) -> tuple[dict, dict]:
    _, model, *rest = spec.split(":")
    effort = rest[0] if rest else "low"
    _codex_workdir.mkdir(parents=True, exist_ok=True)
    schema_path = _codex_workdir / "schema.json"
    schema_path.write_text(json.dumps(schema["schema"], ensure_ascii=False), encoding="utf-8")
    prompt = ("Ты работаешь как оценщик, а не как агент: не запускай команды и не читай файлы. "
              "Прочитай инструкции и данные ниже и верни только JSON по схеме.\n\n"
              + "\n\n".join(m["content"] for m in messages))
    started = time.monotonic()
    last_error = None
    for attempt in range(1, retries + 2):
        CODEX_RATE["calls"] += 1
        probe = CODEX_RATE["calls"] % CODEX_PROBE_EVERY == 1
        out_path = _codex_workdir / f"out_{os.getpid()}_{CODEX_RATE['calls']}.txt"
        cmd = [codex_bin(), "exec", "--json", "--skip-git-repo-check", "--ignore-user-config", "-s", "read-only",
               "-C", str(_codex_workdir), "-m", model, "-c", f"model_reasoning_effort={effort}",
               "--output-schema", str(schema_path), "-o", str(out_path), "-"]
        if not probe:
            cmd.insert(3, "--ephemeral")
        try:
            proc = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.PIPE,
                                                        stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(proc.communicate(prompt.encode("utf-8")), timeout)
            events = [json.loads(l) for l in stdout.decode("utf-8", "replace").splitlines() if l.startswith("{")]
            usage = next((e.get("usage") for e in events if e.get("type") == "turn.completed"), None) or {}
            thread_id = next((e.get("thread_id") for e in events if e.get("type") == "thread.started"), None)
            failed = [e for e in events if e.get("type") in ("turn.failed", "error")]
            text = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
            out_path.unlink(missing_ok=True)
            if probe and thread_id:
                _read_rate_limits(thread_id)
            if text.strip():
                trace = {"model": spec, "attempts": attempt, "latency_ms": int((time.monotonic() - started) * 1000),
                         "prompt_tokens": usage.get("input_tokens"), "cached_tokens": usage.get("cached_input_tokens"),
                         "completion_tokens": usage.get("output_tokens"),
                         "reasoning_tokens": usage.get("reasoning_output_tokens"),
                         "codex_5h_used_percent": CODEX_RATE["primary_used_percent"]}
                return json.loads(text), trace
            last_error = (json.dumps(failed[-1], ensure_ascii=False)[:300] if failed
                          else stderr.decode("utf-8", "replace")[-300:] or f"exit {proc.returncode}")
            if "usage limit" in last_error.lower() or "rate limit" in last_error.lower():
                break
        except asyncio.TimeoutError:
            last_error = "таймаут codex exec"
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        except json.JSONDecodeError as e:
            last_error = f"JSONDecodeError: {e}"
        if attempt <= retries:
            await asyncio.sleep(3 * attempt + random.random())
    raise LLMError(last_error or "неизвестная ошибка codex")


async def openai_json(model: str, messages: list, schema: dict, api_key: str, *, timeout: float = 60.0,
                      retries: int = 3) -> tuple[dict, dict]:
    """Вызов chat/completions со строгой JSON-схемой. Ретраи с экспоненциальной паузой
    на таймаутах, 429 и 5xx; на остальных 4xx — сразу ошибка. Возвращает (объект, трасса)."""
    payload = {"model": model, "messages": messages,
               "response_format": {"type": "json_schema", "json_schema": schema}}
    if model.startswith("gpt-5"):
        payload["reasoning_effort"] = "low"
    else:
        payload["temperature"] = 0
    headers = {"Authorization": f"Bearer {api_key}"}
    started = time.monotonic()
    last_error = None
    for attempt in range(1, retries + 2):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(OPENAI_URL, headers=headers, json=payload)
            if resp.status_code == 200:
                body = resp.json()
                content = body["choices"][0]["message"]["content"]
                usage = body.get("usage") or {}
                trace = {"model": model, "attempts": attempt, "latency_ms": int((time.monotonic() - started) * 1000),
                         "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens")}
                return json.loads(content), trace
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            if resp.status_code != 429 and resp.status_code < 500:
                break
        except (httpx.TimeoutException, httpx.TransportError, json.JSONDecodeError) as e:
            last_error = f"{type(e).__name__}: {e}"
        if attempt <= retries:
            await asyncio.sleep(min(20.0, 1.5 * 2 ** (attempt - 1)) + random.random())
    raise LLMError(last_error or "неизвестная ошибка")


def render_transcript(dialog: list, reply: str) -> str:
    lines = []
    for m in dialog:
        who = "Покупатель" if m["role"] == "user" else "Бот/магазин"
        lines.append(f"{who}: {m['content']}")
    lines.append("")
    lines.append(f"ПРОВЕРЯЕМЫЙ ОТВЕТ БОТА: {reply}")
    return "\n".join(lines)


FACTS_HIDDEN_NOTE = (
    "ВНИМАНИЕ: данные заказа и товара, которые были у бота (блок «ДАННЫЕ ИЗ СИСТЕМЫ» и название товара чата), "
    "тебе НЕ показаны — их не удалось восстановить. Утверждения бота о статусе, сроках, составе заказа, наличии "
    "названии и характеристиках товара (у бота могла быть карточка товара) не считай unsupported, если они "
    "не противоречат переписке и правилам. "
    "Ссылки, фото, интерфейс приложения и технические причины бот не мог знать ни из каких данных — к ним это не относится.\n\n"
)


TRUNCATED_DRAFT_NOTE = (
    "ВНИМАНИЕ: проверяемый ответ — черновик, сохранённый в журнале обрезанным (около 150 символов). "
    "Обрыв на полуслове не ошибка, оценивай то, что успел сказать черновик.\n\n"
)


async def judge_reply(system_prompt: str, dialog: list, reply: str, *, model: str | None = None,
                      api_key: str | None = None, facts_visible: bool = True,
                      truncated_draft: bool = False) -> tuple[dict, dict]:
    """dialog — сообщения до ответа (как их видела модель), reply — ответ бота.
    facts_visible=False — системный промпт собран без фактов заказа (их не восстановить): судья
    в тех же условиях, что ручной разметчик, который order_context тоже не видел."""
    model = model or os.environ.get("AUTORESPONDER_JUDGE_MODEL") or DEFAULT_JUDGE_MODEL
    api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
    if not api_key and not model.startswith("codex:"):
        raise LLMError("нет OPENAI_API_KEY")
    messages = [
        {"role": "system", "content": RUBRIC},
        {"role": "user", "content": ("" if facts_visible else FACTS_HIDDEN_NOTE)
                                    + (TRUNCATED_DRAFT_NOTE if truncated_draft else "") + "СИСТЕМНЫЙ ПРОМПТ БОТА (данные и правила, которые у него были):\n<<<\n"
                                    + system_prompt + "\n>>>\n\nПЕРЕПИСКА:\n" + render_transcript(dialog, reply)},
    ]
    if model.startswith("codex:"):
        return await codex_json(model, messages, SCHEMA)
    return await openai_json(model, messages, SCHEMA, api_key)


def judge_cost_usd(trace: dict) -> float | None:
    price = JUDGE_PRICES.get(trace.get("model"))
    if not price or trace.get("prompt_tokens") is None:
        return None
    return (trace["prompt_tokens"] * price[0] + (trace.get("completion_tokens") or 0) * price[1]) / 1e6
