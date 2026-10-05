#!/usr/bin/env bash
# Deploy an immutable offer-service release without touching /root/buyshark.
set -Eeuo pipefail

SHA="${1:?usage: deploy-offer.sh SHA ARCHIVE}"
ARCHIVE="${2:?usage: deploy-offer.sh SHA ARCHIVE}"
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "Invalid commit SHA" >&2; exit 2; }
[[ -f "$ARCHIVE" ]] || { echo "Release archive missing" >&2; exit 2; }

BASE=/opt/buyshark-offer
RELEASES="$BASE/releases"
CURRENT="$BASE/current"
RELEASE="$RELEASES/$SHA"
DATA_DIR=/var/lib/buyshark-offer
DATA_DB="$DATA_DIR/offers.db"
ENV_FILE=/etc/buyshark-offer.env
UNIT_FILE=/etc/systemd/system/offer-service.service
LEGACY_DB=/root/buyshark/offer_service/data/offers.db
LEGACY_ENV=/root/buyshark/.env

mkdir -p "$RELEASES" "$DATA_DIR"
OLD_RELEASE="$(readlink -f "$CURRENT" 2>/dev/null || true)"

if [[ ! -d "$RELEASE" ]]; then
  STAGE="$(mktemp -d "$RELEASES/.stage-${SHA}.XXXXXX")"
  trap 'rm -rf -- "$STAGE"' EXIT
  tar -xzf "$ARCHIVE" -C "$STAGE"
  test -f "$STAGE/offer_service/main.py"
  test -f "$STAGE/offer_service/requirements.txt"
  test -f "$STAGE/deploy/offer-service.service"
  python3 -m venv "$STAGE/venv"
  "$STAGE/venv/bin/pip" install --disable-pip-version-check -r "$STAGE/offer_service/requirements.txt"
  mv -- "$STAGE" "$RELEASE"
  trap - EXIT
fi

# On the first deploy, move only the offer token to a dedicated environment.
if [[ ! -s "$ENV_FILE" ]]; then
  TOKEN_LINE="$(grep -m1 '^OFFER_ADMIN_TOKEN=' "$LEGACY_ENV" 2>/dev/null || true)"
  [[ -n "$TOKEN_LINE" ]] || { echo "OFFER_ADMIN_TOKEN unavailable" >&2; exit 1; }
  printf '%s\n' "$TOKEN_LINE" > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi
grep -q '^OFFER_ADMIN_TOKEN=.' "$ENV_FILE" || { echo "OFFER_ADMIN_TOKEN empty" >&2; exit 1; }

UNIT_BACKUP="$(mktemp "$BASE/.unit-before.XXXXXX")"
if [[ -f "$UNIT_FILE" ]]; then
  cp "$UNIT_FILE" "$UNIT_BACKUP"
else
  rm -f "$UNIT_BACKUP"
  UNIT_BACKUP=""
fi

atomic_link() {
  local target="$1"
  ln -sfn "$target" "$CURRENT.next"
  mv -Tf "$CURRENT.next" "$CURRENT"
}

MIGRATED_DB=0
RECOVER_NEEDED=1
rollback_on_exit() {
  [[ "$RECOVER_NEEDED" == 1 ]] || return 0
  set +e
  echo "Offer deploy failed; restoring previous service" >&2
  systemctl stop offer-service
  if [[ "$MIGRATED_DB" == 1 ]]; then
    local saved_data="$DATA_DIR.failed-$SHA-$(date +%s)"
    mv "$DATA_DIR" "$saved_data"
    mkdir -p "$DATA_DIR"
    echo "New database snapshot retained at $saved_data" >&2
  fi
  if [[ -n "$OLD_RELEASE" && -d "$OLD_RELEASE" ]]; then
    atomic_link "$OLD_RELEASE"
  else
    rm -f "$CURRENT"
  fi
  if [[ -n "$UNIT_BACKUP" ]]; then
    cp "$UNIT_BACKUP" "$UNIT_FILE"
    rm -f "$UNIT_BACKUP"
  else
    rm -f "$UNIT_FILE"
  fi
  systemctl daemon-reload
  systemctl restart offer-service
}
trap rollback_on_exit EXIT

# SQLite backup includes any WAL pages. Stop the old writer for the one-time move.
if [[ ! -e "$DATA_DB" && -f "$LEGACY_DB" ]]; then
  systemctl stop offer-service
  MIGRATED_DB=1
  if ! python3 - "$LEGACY_DB" "$DATA_DB" <<'PY'
import sqlite3
import sys

source = sqlite3.connect(sys.argv[1])
target = sqlite3.connect(sys.argv[2])
try:
    source.backup(target)
finally:
    target.close()
    source.close()
PY
  then
    echo "Offer database backup failed" >&2
    exit 1
  fi
  chmod 600 "$DATA_DB"
fi

health_check() {
  local i
  for i in $(seq 1 40); do
    if curl -fsS --max-time 2 -o /dev/null http://127.0.0.1:8030/healthz; then
      break
    fi
    sleep 1
  done
  curl -fsS --max-time 5 -o /dev/null http://127.0.0.1:8030/healthz || return 1
  if python3 - "$DATA_DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as conn:
    exists = conn.execute("SELECT 1 FROM offers WHERE slug = 'pro-kz'").fetchone()
sys.exit(0 if exists else 1)
PY
  then
    curl -fsS --max-time 10 -o /dev/null http://127.0.0.1:8030/pro-kz || return 1
  fi
}

cp "$RELEASE/deploy/offer-service.service" "$UNIT_FILE"
systemctl daemon-reload
atomic_link "$RELEASE"

if systemctl restart offer-service && health_check; then
  RECOVER_NEEDED=0
  trap - EXIT
  rm -f "$UNIT_BACKUP"
  echo "Offer service deployed: $SHA"
  exit 0
fi

echo "Offer health check failed" >&2
exit 1
