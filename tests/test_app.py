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


def test_design_system_fonts_served():
    client = TestClient(create_app(manager_with()))
    css = client.get("/static/styles.css").text
    for f in ("orbitron-latin-800-normal.woff2", "jetbrains-mono-latin-400-normal.woff2"):
        assert f in css
        assert client.get(f"/static/vendor/fonts/{f}").status_code == 200
    # Body text stays in the normal system font; the display font is only for accents.
    assert '--font-ui: system-ui' in css


def test_neon_side_panels_present():
    client = TestClient(create_app(manager_with()))
    html = client.get("/").text
    assert 'class="tabs sidebar neon-panel"' in html and 'id="hud-panel"' in html
    assert html.count('class="tab') >= 8 and 'id="cmd"' in html
    assert client.get("/static/hud.js").status_code == 200


def test_series_palette_tokens_and_no_dual_axes():
    client = TestClient(create_app(manager_with()))
    css = client.get("/static/styles.css").text
    import re

    def lum(hexc):
        r, g, b = (int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5))
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)

    def contrast(a, b):
        hi, lo = sorted((lum(a), lum(b)), reverse=True)
        return (hi + 0.05) / (lo + 0.05)

    # dark block first, light theme second: every chart series must stand out from the background
    blocks = re.findall(r"--bg: (#[0-9a-f]{6});.*?--series-1: (#[0-9a-f]{6}); --series-2: (#[0-9a-f]{6}); "
                        r"--series-3: (#[0-9a-f]{6});", css, re.S)
    assert len(blocks) == 2
    for bg, *series in blocks:
        assert len(set(series)) == 3
        assert all(contrast(c, bg) >= 3.0 for c in series), (bg, series)
    for js in ("charts.js", "builder.js"):
        assert "overlaying" not in client.get(f"/static/{js}").text  # stacked panels, never two y-scales


def test_guide_assets():
    client = TestClient(create_app(manager_with()))
    html = client.get("/").text
    assert 'id="chain-summary"' in html and 'id="tour-open"' in html
    js = client.get("/static/guide.js").text
    for term in ('"iv":', '"delta":', '"pcr":', '"max pain":', '"theta":'):
        assert term in js


def test_fx_assets_and_reduced_motion_guard():
    client = TestClient(create_app(manager_with()))
    html = client.get("/").text
    assert 'id="fx-toggle"' in html and 'id="boot"' in html and "prefers-reduced-motion" in html
    assert client.get("/static/fx.js").status_code == 200
