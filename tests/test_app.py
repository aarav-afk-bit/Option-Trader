from fastapi.testclient import TestClient

from option_trader.app import create_app

from .helpers import manager_with


def test_health():
    client = TestClient(create_app(manager_with()))
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_index_and_static_served():
    client = TestClient(create_app(manager_with()))
    r = client.get("/")
    assert r.status_code == 200 and "Option Trader" in r.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200


def test_dark_theme_is_default():
    client = TestClient(create_app(manager_with()))
    html = client.get("/").text
    css = client.get("/static/styles.css").text
    assert 'id="theme-toggle"' in html
    root = css[css.index(":root {"):css.index("}", css.index(":root {"))]
    assert "color-scheme: dark" in root
    assert ':root[data-theme="light"]' in css


def test_builder_has_optimise_button():
    client = TestClient(create_app(manager_with()))
    html = client.get("/").text
    assert 'id="opt-run"' in html and 'id="opt-goal"' in html
