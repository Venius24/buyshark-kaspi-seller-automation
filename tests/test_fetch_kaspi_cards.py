"""All HTTP is replaced; an accidental real transport call fails before I/O."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from scripts import fetch_kaspi_cards as cards


FIXTURE = Path(__file__).parent / "fixtures" / "kaspi_card_sample.html"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Real HTTP transport is forbidden")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", forbidden)
    monkeypatch.setattr(cards.time, "sleep", lambda seconds: None)


@pytest.fixture
def html():
    return FIXTURE.read_text(encoding="utf-8")


class Client:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True


def response(code=200, text=""):
    return SimpleNamespace(status_code=code, text=text)


def input_args(tmp_path, count=2):
    path = tmp_path / "input.json"
    path.write_text(json.dumps([{"sku": str(i), "url": f"https://kaspi.kz/shop/p/test-{i}/?c=750000000"}
                                for i in range(count)]), encoding="utf-8")
    return ["--input", str(path), "--out-dir", str(tmp_path / "out"), "--fetch"]


def output(tmp_path, name):
    return json.loads((tmp_path / "out" / name).read_text(encoding="utf-8"))


def test_fixture_and_parse(html):
    assert FIXTURE.stat().st_size < 20_000
    assert not FIXTURE.read_bytes().startswith(b"\xef\xbb\xbf")
    data = cards.parse_card_html(html)
    assert data["name"] == "AAT игрушка игрушка спящий панда плюшевый"
    assert data["brand"] == "AAT"
    assert data["category"] == "Прочие аксессуары для автомобиля"
    assert "Плюшевая игрушка" in data["description"]
    assert "<" not in data["description"]
    assert ["Цвет", "черный"] in data["features"]
    assert len(data["features"]) == 4
    for text in (html, json.dumps(data, ensure_ascii=False)):
        assert not any(s in text.lower() for s in ("jpg", "png", "webp", "cdn-kaspi"))
    assert set(data) == {"name", "brand", "category", "description", "features"}


@pytest.mark.parametrize("html", ["", "<html></html>", "<script>bad JSON {</script>",
                                  '<script>{"specifications":null,"descriptions":null}</script>'])
def test_empty(html):
    assert cards.parse_card_html(html) == {
        "name": "", "brand": "", "category": "", "description": "", "features": []}


def test_json_ld_fallback_and_order():
    data = cards.parse_card_html('''<title>Wrong fallback</title>
    <script type="application/ld+json">{"@graph":[
    {"name":"Organization ignored","@type":"Organization"},
    {"description":"<p>One</p><p>two &amp; three</p>","brand":{"name":"Brand"},
     "category":"Cat","name":"Product","@type":["Thing","Product"]}]}</script>''')
    assert data == {"name": "Product", "brand": "Brand", "category": "Cat",
                    "description": "One two & three", "features": []}
    assert cards.parse_card_html("<title>Title only</title>")["name"] == "Title only"


def test_normal_recaptcha_initialization_is_not_a_block(html):
    assert not cards._blocked(html + "<script>BACKEND.components.reCAPTCHA = {};</script>")
    assert cards._blocked('<div class="g-recaptcha"></div>')


def test_feature_units_nulls_duplicates_limits_and_images():
    features = [
        {"featureValues": [{"value": None}, {"value": ""}, {"value": "10"}, {"value": "20"}],
         "featureUnit": "см", "name": "Размер"},
        {"name": "размер", "featureValues": [{"value": "duplicate"}]},
        {"name": "Empty", "featureValues": None, "featureUnit": "кг"},
        {"name": "Н" * 120, "featureValues": [{"value": "З" * 700}]},
        {"name": "Zero", "featureValues": [{"value": 0}]},
        {"name": "Picture", "featureValues": [{"value": "https://example.org/a.PNG?q=1"}]},
        {"name": "Docs", "featureValues": [{"value": "https://example.org/manual"}]},
        None,
    ]
    item = {"description": "<p>" + "text " * 1000 + "</p>",
            "specifications": [{"features": features}, {"features": None}, None]}
    data = cards.parse_card_html("<script>window.item = " + json.dumps(item) + ";</script>")
    assert data["features"] == [["Размер", "10, 20 см"], ["Н" * 100, "З" * 600],
                                ["Zero", "0"], ["Docs", "https://example.org/manual"]]
    assert len(data["description"]) == 2000
    assert not any(s in json.dumps(data).lower() for s in (".png", ".jpg", ".webp", "cdn-kaspi"))


def test_no_fetch_no_client(tmp_path, monkeypatch, capsys):
    args = input_args(tmp_path)
    monkeypatch.setattr(cards, "make_client", lambda: pytest.fail("Client must not be created"))
    args.remove("--fetch")
    assert cards.main(args) == 2
    assert "--fetch" in capsys.readouterr().out
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("url", ["https://example.org/shop/p/a-1/", "https://kaspi.kz/shop/search/",
    "http://kaspi.kz/shop/p/a/", "https://kaspi.kz.evil.org/shop/p/a/",
    "https://user@kaspi.kz/shop/p/a/", "https://kaspi.kz:8080/shop/p/a/",
    "https://kaspi.kz/shop/p/../other/", "https://kaspi.kz/shop/p/%2e%2e/other/",
    "https://kaspi.kz/shop/p/image.jpg", "https://kaspi.kz/shop/p/"])
def test_invalid_url(url, tmp_path, monkeypatch):
    args = input_args(tmp_path)
    (tmp_path / "input.json").write_text(json.dumps([{"sku": "1", "url": url}]), encoding="utf-8")
    monkeypatch.setattr(cards, "make_client", lambda: pytest.fail("Invalid URL reached client"))
    with pytest.raises(SystemExit) as exc:
        cards.main(args)
    assert exc.value.code == 2


@pytest.mark.parametrize("delay", ["0.9", "-1", "nan", "inf"])
def test_invalid_delay(tmp_path, delay):
    with pytest.raises(SystemExit) as exc:
        cards.main(input_args(tmp_path) + ["--delay", delay])
    assert exc.value.code == 2


@pytest.mark.parametrize("code,body", [(429, ""), (403, ""), (200, '<form id="captcha">Robot</form>'),
                                      (200, "Подтвердите, что вы не робот")])
def test_block_stops_immediately(tmp_path, monkeypatch, code, body):
    client = Client([response(code, body)])
    monkeypatch.setattr(cards, "make_client", lambda: client)
    assert cards.main(input_args(tmp_path)) == 3
    assert len(client.calls) == 1
    assert client.closed
    assert output(tmp_path, "fetch_log.json") == {"0": "blocked"}
    assert output(tmp_path, "products.json") == []


def test_404_continues_and_output_contract(tmp_path, monkeypatch, html, capsys):
    client = Client([response(404), response(200, html)])
    monkeypatch.setattr(cards, "make_client", lambda: client)
    assert cards.main(input_args(tmp_path)) == 0
    assert len(client.calls) == 2
    assert all(kwargs == {"timeout": 15, "allow_redirects": False} for _, kwargs in client.calls)
    assert output(tmp_path, "fetch_log.json") == {"0": "not_found", "1": "ok"}
    assert output(tmp_path, "products.json") == [{"sku": "1", **{key: cards.parse_card_html(html)[key]
                                               for key in ("name", "description", "category")}}]
    assert output(tmp_path, "rows.json") == {"1": dict(cards.parse_card_html(html)["features"])}
    assert "Плюшевая" not in capsys.readouterr().out


def test_cache_resume_without_client(tmp_path, monkeypatch, html):
    client = Client([response(200, html), response(200, html)])
    monkeypatch.setattr(cards, "make_client", lambda: client)
    args = input_args(tmp_path) + ["--cache-dir", str(tmp_path / "cache")]
    assert cards.main(args) == 0
    expected = output(tmp_path, "products.json")
    cache = list((tmp_path / "cache").iterdir())
    assert len(cache) == 2
    for path in cache:
        text = path.read_text(encoding="utf-8")
        assert "<script" not in text and "<!doctype" not in text
        assert set(json.loads(text)) == {"sku", "card"}
    monkeypatch.setattr(cards, "make_client", lambda: pytest.fail("Cached sku reached network"))
    assert cards.main(args) == 0
    assert output(tmp_path, "products.json") == expected


@pytest.mark.parametrize("recovered", [True, False])
def test_network_retry_once_and_continue(tmp_path, monkeypatch, html, recovered):
    sleeps = []
    monkeypatch.setattr(cards.time, "sleep", sleeps.append)
    client = Client([requests.ConnectionError("private transport detail"),
                     response(200, html) if recovered else requests.Timeout("private detail"),
                     response(200, html)])
    monkeypatch.setattr(cards, "make_client", lambda: client)
    assert cards.main(input_args(tmp_path)) == 0
    assert len(client.calls) == 3
    assert sleeps[0] == 10
    assert 3 <= sleeps[1] <= 3.9
    assert output(tmp_path, "fetch_log.json") == {"0": "ok" if recovered else "error", "1": "ok"}


def test_offline_parse_empty_and_limit(tmp_path, monkeypatch, html):
    directory = tmp_path / "html"
    directory.mkdir()
    (directory / "first.html").write_text(html, encoding="utf-8")
    (directory / "second.html").write_text("", encoding="utf-8")
    monkeypatch.setattr(cards, "make_client", lambda: pytest.fail("Offline mode created client"))
    args = ["--html-dir", str(directory), "--out-dir", str(tmp_path / "out")]
    assert cards.main(args + ["--limit", "1"]) == 0
    assert output(tmp_path, "fetch_log.json") == {"first": "ok"}
    assert cards.main(args) == 0
    assert output(tmp_path, "fetch_log.json") == {"first": "ok", "second": "parse_empty"}
    assert output(tmp_path, "rows.json")["second"] == {}


def test_redirect_never_followed(tmp_path, monkeypatch):
    client = Client([response(302)])
    monkeypatch.setattr(cards, "make_client", lambda: client)
    assert cards.main(input_args(tmp_path, count=1)) == 0
    assert len(client.calls) == 1
    assert client.calls[0][1]["allow_redirects"] is False
    assert output(tmp_path, "fetch_log.json") == {"0": "error"}


def test_client_settings():
    client = cards.make_client()
    try:
        assert client.trust_env is False
        assert client.headers["User-Agent"].startswith("Mozilla/5.0")
        assert client.headers["Accept-Language"].startswith("ru")
        assert client.get_adapter("https://kaspi.kz").max_retries.total == 0
    finally:
        client.close()
