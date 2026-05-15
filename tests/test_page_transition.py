from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_landing_to_start_uses_green_wash_reveal_hooks():
    index_html = (ROOT / "stale/web/templates/index.html").read_text()
    start_html = (ROOT / "stale/web/templates/start.html").read_text()
    style_css = (ROOT / "stale/web/static/style.css").read_text()

    assert "staleStartArrival', 'green-wash'" in index_html
    assert "is-washing" in index_html
    assert "start-wash-arrival" in start_html
    assert ".home-page .page-transition.is-washing" in style_css
    assert "html.start-wash-arrival body.start-page::before" in style_css


def test_green_wash_reveal_has_a_deliberate_pace():
    index_html = (ROOT / "stale/web/templates/index.html").read_text()
    start_html = (ROOT / "stale/web/templates/start.html").read_text()
    style_css = (ROOT / "stale/web/static/style.css").read_text()

    assert "}, 1000);" in index_html
    assert "}, 2500);" in index_html
    assert "window.location.href = link.href;" in index_html
    assert "}, 5900);" in index_html
    assert "fast-mode" in index_html
    assert "}, 2600);" in start_html
    assert "opacity 1050ms var(--ease-out)" in style_css
    assert "max-width 1900ms var(--ease-in-out)" in style_css
    assert "radial-gradient(circle at 50% 46%" in style_css
    assert "background-color 3400ms var(--ease-in-out)" in style_css
    assert ".home-page .page-transition::before" not in style_css
    assert ".home-page .page-transition::after" not in style_css
    assert ".home-page .page-transition.is-washing .transition-word" in style_css
    assert "opacity 3400ms var(--ease-in-out)" in style_css
    assert "filter: blur(0)" in style_css
    assert "opacity 1500ms var(--ease-out)" in style_css
