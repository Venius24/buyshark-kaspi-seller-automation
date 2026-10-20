"""Решение модели answer / clarify / safe_unknown / escalate (backend/chatbot/llm.py)
и метрики решений в evals/autoresponder/run.py."""
import json

from backend.chatbot.llm import parse_model_output


def test_action_parsed_from_tool_arguments():
    for action in ("answer", "clarify", "safe_unknown", "escalate"):
        reply, got, reason, mode = parse_model_output(
            json.dumps({"reply": "Текст", "action": action, "handoff_reason": ""}), structured=True)
        assert (reply, got, mode) == ("Текст", action, "json")


def test_legacy_handoff_flag_means_escalate():
    _, action, reason, _ = parse_model_output(
        json.dumps({"reply": "Черновик", "handoff": True, "handoff_reason": "жалоба"}), structured=True)
    assert action == "escalate" and reason == "жалоба"


def test_unknown_action_falls_back_to_answer():
    _, action, _, _ = parse_model_output(json.dumps({"reply": "Ок", "action": "maybe"}), structured=True)
    assert action == "answer"


def test_override_mark_in_text_escalates_in_any_mode():
    reply, action, _, mode = parse_model_output("Передам вопрос [HUMAN_OVERRIDE]", structured=False)
    assert (reply, action, mode) == ("Передам вопрос", "escalate", "text")
    reply, action, _, _ = parse_model_output(
        json.dumps({"reply": "Уточню [HUMAN_OVERRIDE]", "action": "answer", "handoff_reason": ""}), structured=True)
    assert (reply, action) == ("Уточню", "escalate")


def test_decision_metrics():
    from evals.autoresponder.run import decision_metrics

    cases = [
        {"id": "need", "label": {"verdict": "escalation_needed"}},
        {"id": "no", "label": {"verdict": "ok"}},
        {"id": "disputed", "label": {"verdict": "inexact"}},
    ]
    results = {
        "need": [{"outcome": "escalated_ai", "action": "escalate"}, {"outcome": "reply", "action": "answer"}],
        "no": [{"outcome": "reply", "action": "clarify", "judge": {"verdict": "ok"}},
               {"outcome": "escalated_trigger"}],  # старый прогон без action: выводится из исхода
        "disputed": [{"outcome": "reply", "action": "safe_unknown", "judge": {"verdict": "unsupported"}},
                     {"outcome": "api_error"}],
    }
    m = decision_metrics(cases, results)
    assert m["runs"] == 5
    assert m["actions"] == {"answer": 1, "clarify": 1, "safe_unknown": 1, "escalate": 2}
    assert m["escalation_recall_n"] == [1, 2]
    assert m["escalation_precision_n"] == [1, 2]
    assert m["escalations_on_disputed_n"] == [0, 2]
    assert m["autonomy_n"] == [3, 5]
    assert m["answer_judged_n"] == 2 and m["answer_quality_pct"] == 50.0
    assert m["resolved_by_bot_pct"] == 20.0
