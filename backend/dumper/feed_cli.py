"""
Ручной запуск прайс-фида: dry-run по умолчанию, отправка — только явно.

    python -m backend.dumper.feed_cli --store <store_id>
    python -m backend.dumper.feed_cli --store <store_id> --save /tmp/feed.xml
    python -m backend.dumper.feed_cli --store <store_id> --send --confirm <merchant_id>

Без `--send` не делается ни одного пишущего запроса: только бесплатные GET.
`--send` дополнительно требует `XML_FEED_UPLOAD_ENABLED=1` и совпадения
`--confirm` с merchant_id магазина — три независимых замка, см. feed_uploader.

На проде запускать с загруженным окружением, иначе скрипт молча уедет на
локальную dev-sqlite вместо прод-Postgres:

    cd /root/buyshark && set -a && source .env && set +a && ./venv/bin/python -m backend.dumper.feed_cli --store ...
"""
import argparse
import logging
import sys


def _resolve_store(store_id: str):
    """(token, merchant_id) магазина. Токен лежит в БД зашифрованным."""
    from backend.db.models import Store
    from backend.security.crypto import decrypt_data
    from backend.state_helpers import _get_sync_db_session

    session = _get_sync_db_session()
    if not session:
        raise SystemExit("нет соединения с БД — окружение точно загружено?")
    try:
        store = session.get(Store, store_id)
        if not store:
            existing = [(s.id, s.name, s.merchant_id) for s in session.query(Store).all()]
            raise SystemExit(
                "магазин {} не найден. Есть: {}".format(store_id, existing or "ни одного")
            )
        if not store.encrypted_api_key:
            raise SystemExit("у магазина {} нет сохранённого токена".format(store.name))
        token = decrypt_data(store.encrypted_api_key)
        if not token:
            raise SystemExit("токен магазина не расшифровался — проверить ENCRYPTION_KEY")
        return token, str(store.merchant_id), store.name
    finally:
        session.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Прайс-фид Kaspi: dry-run и отправка")
    parser.add_argument("--store", required=True, help="store_id из таблицы stores")
    parser.add_argument("--save", help="куда положить собранный XML")
    parser.add_argument("--limit", type=int, default=25, help="сколько изменений печатать")
    parser.add_argument("--send", action="store_true", help="ОТПРАВИТЬ файл в Kaspi")
    parser.add_argument("--confirm", default="", help="merchant_id — подтверждение отправки")
    parser.add_argument("--allow-changes", action="store_true",
                        help="разрешить отправку плана с изменениями (не этап 4)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from backend.dumper.feed_plan import build_feed_plan, render_plan_report

    token, merchant_id, name = _resolve_store(args.store)
    print("магазин: {} (merchant {})".format(name, merchant_id))

    plan = build_feed_plan(token, args.store)
    print()
    print(render_plan_report(plan, limit=args.limit))

    if args.save:
        with open(args.save, "wb") as fh:
            fh.write(plan.xml)
        print()
        print("файл сохранён: {} ({} байт)".format(args.save, len(plan.xml)))

    if not args.send:
        print()
        print("dry-run завершён. В Kaspi ничего не отправлено.")
        return 0

    from backend.dumper.feed_uploader import send_plan

    print()
    print("=== ОТПРАВКА ===")
    verdict = send_plan(
        plan, args.store, merchant_id,
        confirm=args.confirm, allow_changes=args.allow_changes,
    )
    print(verdict.summary())
    print("результат: {}".format("ОК" if verdict.ok else "НЕ ОК"))
    return 0 if verdict.ok else 1


if __name__ == "__main__":
    sys.exit(main())
