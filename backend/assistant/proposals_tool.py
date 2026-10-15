"""The agent can only create a pending proposal, never call a gateway."""
import os

from backend.assistant.tools import tool
from backend.assistant.proposals import create_proposal


@tool(name="propose_price", read_only=False, min_role="MANAGER", section="assistant",
      description="Только по запросу пользователя: предложить цену для одобрения человеком. Цена НЕ меняется.",
      parameters={"type": "object", "properties": {
          "sku": {"type": "string", "maxLength": 100},
          "new_price": {"type": "integer", "minimum": 1},
          "reason": {"type": "string", "minLength": 1, "maxLength": 300}},
          "required": ["sku", "new_price", "reason"], "additionalProperties": False})
async def propose_price(arguments, ctx):
    # Defense in depth for direct handler invocation; never read .env here.
    if os.environ.get("ASSISTANT_ALLOW_WRITE_TOOLS") != "1":
        raise ValueError("propose_price выключен: требуется ASSISTANT_ALLOW_WRITE_TOOLS=1")
    if not ctx.confirmed:
        raise ValueError("Требуется подтверждение пользователя")
    if set(arguments) != {"sku", "new_price", "reason"}:
        raise ValueError("Допустимы только sku, new_price, reason")
    if ctx.db is None:
        raise ValueError("DB-сессия не передана сервером")
    row = await create_proposal(ctx.db, ctx, **arguments)
    return {"id": row.id, "status": row.status,
            "message": "Предложение создано, ждёт одобрения человеком, цена НЕ изменена"}
