from __future__ import annotations

from pathlib import Path

from elan_pretty.github_pages import (
    discover_publications,
    public_url_for_path,
    remote_to_pages_base_url,
)
from elan_pretty.publishing import remove_github_publication, remove_site_publication


def test_infers_project_pages_url_from_ssh_remote() -> None:
    assert (
        remote_to_pages_base_url("git@github.com:mkurg/elan-pretty.git")
        == "https://mkurg.github.io/elan-pretty/"
    )


def test_infers_user_pages_url_from_https_remote() -> None:
    assert (
        remote_to_pages_base_url("https://github.com/mkurg/mkurg.github.io.git")
        == "https://mkurg.github.io/"
    )


def test_builds_public_url_for_repo_path() -> None:
    repo_root = Path("/repo")
    path = Path("/repo/published/example_text")

    assert (
        public_url_for_path(repo_root, path, "https://mkurg.github.io/elan-pretty/")
        == "https://mkurg.github.io/elan-pretty/published/example_text/"
    )


def test_discovers_existing_publications(tmp_path: Path) -> None:
    site_root = tmp_path / "published"
    publication = site_root / "sample"
    publication.mkdir(parents=True)
    (publication / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (publication / "sample.json").write_text('{"title": "Sample Text"}', encoding="utf-8")
    (publication / "sample.pdf").write_bytes(b"%PDF-1.4\n")

    entries = discover_publications(
        site_root,
        repo_root=tmp_path,
        base_url="https://mkurg.github.io/elan-pretty/",
    )

    assert len(entries) == 1
    assert entries[0].title == "Sample Text"
    assert entries[0].url == "https://mkurg.github.io/elan-pretty/published/sample/"
    assert entries[0].pdf_path == publication / "sample.pdf"


def test_discovers_static_site_publications_from_site_root(tmp_path: Path) -> None:
    site_root = tmp_path / "published"
    publication = site_root / "sample"
    publication.mkdir(parents=True)
    (publication / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (publication / "sample.json").write_text('{"title": "Sample Text"}', encoding="utf-8")

    entries = discover_publications(
        site_root,
        url_root=site_root,
        base_url="https://elan.example.org/published/",
    )

    assert len(entries) == 1
    assert entries[0].url == "https://elan.example.org/published/sample/"


def test_removes_publication_and_rebuilds_index(tmp_path: Path) -> None:
    site_root = tmp_path / "published"
    first = site_root / "first"
    second = site_root / "second"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (first / "first.json").write_text('{"title": "First"}', encoding="utf-8")
    (second / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (second / "second.json").write_text('{"title": "Second"}', encoding="utf-8")

    removed = remove_github_publication(site_root, "first", repo_root=tmp_path)

    assert removed.slug == "first"
    assert not first.exists()
    assert second.exists()
    assert (site_root / "index.html").exists()
    index_html = (site_root / "index.html").read_text(encoding="utf-8")
    assert "Second" in index_html
    assert "First" not in index_html


def test_removes_static_site_publication_and_rebuilds_index(tmp_path: Path) -> None:
    site_root = tmp_path / "published"
    first = site_root / "first"
    second = site_root / "second"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (first / "first.json").write_text('{"title": "First"}', encoding="utf-8")
    (second / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (second / "second.json").write_text('{"title": "Second"}', encoding="utf-8")

    removed = remove_site_publication(
        site_root,
        "first",
        url_root=site_root,
        base_url="https://elan.example.org/published/",
    )

    assert removed.slug == "first"
    assert not first.exists()
    assert second.exists()
    index_html = (site_root / "index.html").read_text(encoding="utf-8")
    assert "https://elan.example.org/published/second/" in index_html
