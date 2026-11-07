import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")

RAW_IMAGE = re.compile(r"https://raw\.githubusercontent\.com/FSIBT/pysrim2/main/([^\s\"'()<>]+)")
DOCS_LINK = re.compile(r"https://pysrim2\.readthedocs\.io/en/latest/([^\s\"'()<>#]+)\.html")


def test_readme_raw_images_exist():
    paths = RAW_IMAGE.findall(README)

    assert paths, "the README should show gallery pictures"
    for path in paths:
        assert (ROOT / path).is_file(), f"README image {path} is not in the repository"


def test_readme_docs_links_are_pages():
    pages = DOCS_LINK.findall(README)

    assert pages, "the README should link to the documentation"
    for page in pages:
        sources = [ROOT / "docs" / f"{page}.md", ROOT / "docs" / f"{page}.rst"]
        assert any(source.is_file() for source in sources), (
            f"README links to docs page {page}.html, which has no source"
        )


def test_readme_cites_the_original_paper():
    assert "10.21105/joss.00829" in README
