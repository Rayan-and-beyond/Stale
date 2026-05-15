from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_start_page_uses_run_page_top_alignment():
    start_html = (ROOT / "stale/web/templates/start.html").read_text()
    style_css = (ROOT / "stale/web/static/style.css").read_text()

    assert 'class="start-page-nav"' in start_html
    assert 'class="start-centered-brand"' in start_html
    assert start_html.index('class="start-page-nav"') < start_html.index('class="start-intro-grid"')
    assert ".start-page-nav" in style_css
    assert ".start-centered-brand" in style_css


def test_start_page_hero_title_uses_green_token():
    style_css = (ROOT / "stale/web/static/style.css").read_text()
    h1_block = style_css.split(".start-hero h1 {", 1)[1].split("}", 1)[0]

    assert "color: var(--home-green);" in h1_block
