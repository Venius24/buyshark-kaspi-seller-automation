"""Extract public Kaspi card text; network access requires --input AND --fetch.

Offline files are named <sku>.html (or .htm). Only parsed text is cached.
Run directly: python scripts/fetch_kaspi_cards.py --help
No application imports, proxy rotation, browser resources, or image downloads.
"""

import argparse
from collections import Counter
import hashlib
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import random
import re
import time
from urllib.parse import unquote, urlsplit

import requests


_IMAGE = re.compile(r"\S*(?:cdn-kaspi|\.(?:jpe?g|png|webp|gif|svg|avif|bmp)\b)\S*", re.I)


class _HTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts = []
        self.title = []
        self.text = []
        self.script = None
        self.in_title = False
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.script = []
        elif tag == "title":
            self.in_title = True
        elif tag == "style":
            self.hidden += 1
        elif tag in {"p", "br", "div", "li"}:
            self.text.append(" ")

    def handle_endtag(self, tag):
        if tag == "script" and self.script is not None:
            self.scripts.append("".join(self.script))
            self.script = None
        elif tag == "title":
            self.in_title = False
        elif tag == "style":
            self.hidden = max(0, self.hidden - 1)
        self.text.append(" ")

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)
        elif not self.hidden:
            self.text.append(data)
            if self.in_title:
                self.title.append(data)


def _clean(value, limit):
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    parser = _HTML()
    parser.feed(str(value))
    return " ".join(_IMAGE.sub("", "".join(parser.text)).split())[:limit]


def _objects(script):
    """Decode JSON literals in JSON scripts and JS assignments, never execute JS."""
    decoder = json.JSONDecoder()
    pos = 0
    while match := re.search(r"[\[{]", script[pos:]):
        start = pos + match.start()
        try:
            value, end = decoder.raw_decode(script, start)
        except ValueError:
            pos = start + 1
            continue
        yield value
        pos = end


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def parse_card_html(html: str) -> dict:
    """Return only card text, with empty fields for missing data.

    Prefer BACKEND.components.item over JSON-LD Product; ignore Organization.
    Feature names are deduplicated case-insensitively after truncation (first wins).
    """
    page = _HTML()
    page.feed(html)
    nodes = [node for script in page.scripts for obj in _objects(script)
             for node in _walk(obj)]
    item = next((n for n in nodes if isinstance(n.get("card"), dict)), None)
    if item is None:
        item = next((n for n in nodes if isinstance(n.get("specifications"), list)), {})
    product = next((n for n in nodes if n.get("@type") == "Product"
                    or isinstance(n.get("@type"), list) and "Product" in n["@type"]), {})
    card = item.get("card") or {}
    promo = card.get("promoConditions") or {}
    promo = promo if isinstance(promo, dict) else {}
    brand = product.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name")
    breadcrumbs = item.get("breadcrumbs") or []
    category = next((b.get("title") for b in reversed(breadcrumbs)
                     if isinstance(b, dict) and b.get("title")), "") if isinstance(breadcrumbs, list) else ""
    description = item.get("description")
    if not description:
        descriptions = item.get("descriptions") or []
        if isinstance(descriptions, list):
            description = next((d.get("text") for d in descriptions
                                if isinstance(d, dict) and d.get("text")), "")
    result = {
        "name": _clean(card.get("title") or product.get("name") or "".join(page.title), 1000),
        "brand": _clean(promo.get("brand") or brand, 600),
        "category": _clean(category or product.get("category"), 600),
        "description": _clean(description or product.get("description"), 2000),
        "features": [],
    }
    seen = set()
    groups = item.get("specifications") or []
    for group in groups if isinstance(groups, list) else []:
        features = group.get("features") if isinstance(group, dict) else None
        for feature in features if isinstance(features, list) else []:
            if not isinstance(feature, dict):
                continue
            name = _clean(feature.get("name"), 100)
            values = feature.get("featureValues") or []
            values = [_clean(v.get("value"), 600) for v in values
                      if isinstance(v, dict)] if isinstance(values, list) else []
            value = ", ".join(v for v in values if v)
            unit = _clean(feature.get("featureUnit"), 100)
            value = (value + (" " + unit if unit else ""))[:600] if value else ""
            if name and value and name.casefold() not in seen:
                seen.add(name.casefold())
                result["features"].append([name, value])
    return result


def validate_url(url):
    try:
        parts = urlsplit(url)
        path = unquote(parts.path)
        valid = (parts.scheme == "https" and parts.hostname == "kaspi.kz"
                 and parts.port in (None, 443) and not parts.username and not parts.password
                 and path.startswith("/shop/p/") and len(path) > len("/shop/p/")
                 and "\\" not in path and not any(x in {".", ".."} for x in path.split("/"))
                 and not any(c.isspace() or ord(c) < 32 for c in url)
                 and not _IMAGE.search(path) and not parts.fragment)
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Only https://kaspi.kz/shop/p/<card>/ URLs are allowed")
    return url


def make_client():
    # Deliberately independent of backend.dumper and its proxy/app configuration.
    client = requests.Session()
    client.trust_env = False
    client.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Accept": "text/html",
    })
    return client


def _blocked(html):
    page = _HTML()
    page.feed(html)
    # Normal cards contain BACKEND.components.reCAPTCHA = {}; that is not a challenge.
    visible = " ".join("".join(page.text).split())
    challenge_text = re.search(
        r"captcha|капч|подтвердите.{0,60}(?:робот|человек)|"
        r"verify.{0,60}(?:human|robot)|access denied|доступ (?:ограничен|заблокирован)",
        visible, re.I)
    # Actual challenge widgets may have no visible text. Ignore JS variables.
    widget = re.search(r'<(?:form|iframe|div|input)\b[^>]*\b(?:id|class|src|name)\s*=\s*'
                       r'[\"\'][^\"\']*(?:captcha|challenge-platform)[^\"\']*[\"\']', html, re.I)
    return bool(challenge_text or widget)


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--html-dir", type=Path, help="Offline <sku>.html files")
    modes.add_argument("--input", type=Path, help="JSON list of sku/url objects")
    parser.add_argument("--fetch", action="store_true", help="Explicitly enable HTTP")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--delay", type=float, default=3.0)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    if not math.isfinite(args.delay) or args.delay < 1.0:
        parser.error("--delay must be finite and at least 1.0 seconds")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.input and not args.fetch:
        print("HTTP disabled: --input requires explicit --fetch; nothing downloaded.")
        return 2
    try:
        if args.input:
            entries = json.loads(args.input.read_text(encoding="utf-8"))
            if not isinstance(entries, list):
                raise ValueError("Input must be a list")
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("sku"), str) or not entry["sku"].strip():
                    raise ValueError("Every entry needs a nonempty string sku")
                validate_url(entry.get("url"))
        else:
            if not args.html_dir.is_dir():
                raise ValueError("--html-dir must be a directory")
            entries = [{"sku": p.stem, "path": p} for p in sorted(args.html_dir.iterdir())
                       if p.is_file() and p.suffix.lower() in {".html", ".htm"}]
        if len({e["sku"] for e in entries}) != len(entries):
            raise ValueError("Duplicate sku in input")
    except (OSError, ValueError) as exc:
        # Do not echo input values or page text to the console.
        parser.error("Invalid input: " + (str(exc) if isinstance(exc, ValueError)
                                          and not isinstance(exc, json.JSONDecodeError) else "cannot read JSON/files"))
    entries = entries[:args.limit]
    products, rows, log = [], {}, {}
    client = None
    requested = False
    exit_code = 0
    try:
        for entry in entries:
            sku = entry["sku"]
            cache = (args.cache_dir / (hashlib.sha256(sku.encode("utf-8")).hexdigest() + ".json")) if args.cache_dir else None
            data = None
            if cache and cache.exists():
                try:
                    cached = json.loads(cache.read_text(encoding="utf-8"))
                    if (isinstance(cached, dict) and cached.get("sku") == sku
                            and isinstance(cached.get("card"), dict)):
                        data = cached["card"]
                except (OSError, ValueError):
                    pass  # Interrupted/corrupt cache is fetched again.
            status = "ok"
            if data is None:
                if args.html_dir:
                    html = entry["path"].read_text(encoding="utf-8-sig")
                    status = "blocked" if _blocked(html) else "ok"
                else:
                    if client is None:
                        client = make_client()
                    response = None
                    for attempt in range(2):
                        if attempt:
                            time.sleep(max(10.0, args.delay * random.uniform(1.0, 1.3)))
                        elif requested:
                            time.sleep(args.delay * random.uniform(1.0, 1.3))
                        requested = True
                        try:
                            response = client.get(entry["url"], timeout=15, allow_redirects=False)
                            break
                        except requests.RequestException:
                            status = "error"
                    if response is None:
                        status, html = "error", ""
                    else:
                        html = response.text
                        if response.status_code in (403, 429) or _blocked(html):
                            status = "blocked"
                        elif response.status_code == 404:
                            status = "not_found"
                        elif response.status_code != 200:
                            status = "error"  # Redirects are never followed.
                        else:
                            status = "ok"
                if status == "ok":
                    data = parse_card_html(html)
                    if cache:
                        _write_json(cache, {"sku": sku, "card": data})
            if data is not None:
                status = "ok" if any(data.values()) else "parse_empty"
                products.append({"sku": sku, **{key: data[key] for key in
                                                ("name", "description", "category")}})
                rows[sku] = dict(data["features"])
            log[sku] = status
            print("sku=" + json.dumps(sku, ensure_ascii=True))
            if status == "blocked":
                exit_code = 3
                break
    finally:
        if client is not None:
            client.close()
        _write_json(args.out_dir / "products.json", products)
        _write_json(args.out_dir / "rows.json", rows)
        _write_json(args.out_dir / "fetch_log.json", log)
    print(json.dumps(dict(Counter(log.values())), sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
