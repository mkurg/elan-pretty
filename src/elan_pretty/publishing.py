from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from elan_pretty.config import ProjectConfig
from elan_pretty.github_pages import (
    PublicationEntry,
    discover_publications,
    public_url_for_path,
    remote_to_pages_base_url,
    write_publication_index,
    write_root_redirect,
)
from elan_pretty.models import InterlinearDocument
from elan_pretty.normalize import EafNormalizer
from elan_pretty.parser import EafParser
from elan_pretty.render import HTMLRenderer, render_pdf
from elan_pretty.utils import safe_slug


@dataclass(frozen=True, slots=True)
class RenderedPublication:
    document: InterlinearDocument
    publication: PublicationEntry | None
    html_path: Path
    json_path: Path
    pdf_path: Path | None
    public_url: str | None
    index_path: Path | None = None
    root_index_path: Path | None = None


@dataclass(frozen=True, slots=True)
class RemovedPublication:
    slug: str
    removed_path: Path
    index_path: Path
    root_index_path: Path | None


def render_eaf_publication(
    eaf_path: Path,
    output_dir: Path,
    config: ProjectConfig,
    *,
    pdf: bool = False,
    pdf_backend: str = "auto",
    github_pages: bool = False,
    repo_root: Path | None = None,
    pages_base_url: str | None = None,
    slug: str | None = None,
) -> RenderedPublication:
    raw = EafParser().parse(eaf_path)
    document = EafNormalizer(raw, config).normalize()
    return write_document_publication(
        document,
        output_dir,
        config,
        source_stem=eaf_path.stem,
        pdf=pdf,
        pdf_backend=pdf_backend,
        github_pages=github_pages,
        repo_root=repo_root,
        pages_base_url=pages_base_url,
        slug=slug,
    )


def write_document_publication(
    document: InterlinearDocument,
    output_dir: Path,
    config: ProjectConfig,
    *,
    source_stem: str,
    pdf: bool = False,
    pdf_backend: str = "auto",
    github_pages: bool = False,
    repo_root: Path | None = None,
    pages_base_url: str | None = None,
    slug: str | None = None,
) -> RenderedPublication:
    renderer = HTMLRenderer()
    stem = safe_slug(slug or source_stem)
    render_dir = output_dir / stem if github_pages else output_dir
    html_stem = "index" if github_pages else stem

    render_dir.mkdir(parents=True, exist_ok=True)
    json_path = render_dir / f"{stem}.json"
    json_path.write_text(document.model_dump_json(indent=2), encoding="utf-8")
    html_path = renderer.write(document, render_dir, config, stem=html_stem)

    pdf_path: Path | None = None
    if pdf:
        pdf_path = render_dir / f"{stem}.pdf"
        render_pdf(html_path, pdf_path, backend=pdf_backend)

    publication: PublicationEntry | None = None
    public_url: str | None = None
    index_path: Path | None = None
    root_index_path: Path | None = None
    if github_pages:
        public_url = (
            public_url_for_path(repo_root, render_dir, pages_base_url)
            if repo_root and pages_base_url
            else None
        )
        publication = PublicationEntry(
            title=document.title,
            slug=stem,
            url=public_url,
            html_path=html_path,
            json_path=json_path,
            pdf_path=pdf_path,
        )
        existing = discover_publications(
            output_dir,
            repo_root=repo_root,
            base_url=pages_base_url,
            exclude_slugs={stem},
        )
        index_path = write_publication_index(output_dir, [*existing, publication])
        if repo_root is not None:
            target = output_dir.resolve().relative_to(repo_root.resolve()).as_posix() + "/"
            root_index_path = write_root_redirect(repo_root, target)

    return RenderedPublication(
        document=document,
        publication=publication,
        html_path=html_path,
        json_path=json_path,
        pdf_path=pdf_path,
        public_url=public_url,
        index_path=index_path,
        root_index_path=root_index_path,
    )


def remove_github_publication(
    output_dir: Path,
    slug: str,
    *,
    repo_root: Path | None = None,
    pages_base_url: str | None = None,
) -> RemovedPublication:
    """Remove one GitHub Pages publication directory and rebuild the site index."""

    if not slug or slug != Path(slug).name:
        msg = f"Invalid publication slug: {slug!r}"
        raise ValueError(msg)

    site_root = output_dir.resolve()
    publication_dir = (output_dir / slug).resolve()
    if publication_dir.parent != site_root:
        msg = f"Publication slug escapes site root: {slug!r}"
        raise ValueError(msg)
    if not publication_dir.exists() or not publication_dir.is_dir():
        msg = f"No publication exists for slug: {slug}"
        raise ValueError(msg)

    shutil.rmtree(publication_dir)
    remaining = discover_publications(output_dir, repo_root=repo_root, base_url=pages_base_url)
    index_path = write_publication_index(output_dir, remaining)
    root_index_path: Path | None = None
    if repo_root is not None:
        target = output_dir.resolve().relative_to(repo_root.resolve()).as_posix() + "/"
        root_index_path = write_root_redirect(repo_root, target)
    return RemovedPublication(
        slug=slug,
        removed_path=publication_dir,
        index_path=index_path,
        root_index_path=root_index_path,
    )


def infer_repo_root(cwd: Path | None = None) -> Path:
    result = subprocess.run(
        ["git", "-C", str(cwd or Path.cwd()), "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip())


def infer_pages_base_url(repo_root: Path, remote: str = "origin") -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "remote", "get-url", remote],
        check=True,
        capture_output=True,
        text=True,
    )
    return remote_to_pages_base_url(result.stdout.strip())


def commit_and_push_paths(
    repo_root: Path,
    paths: list[Path],
    *,
    message: str,
    remote: str = "origin",
) -> bool:
    relative_paths = [
        path.resolve().relative_to(repo_root.resolve()).as_posix()
        for path in paths
        if path.exists()
    ]
    if not relative_paths:
        return False

    subprocess.run(["git", "-C", str(repo_root), "add", *relative_paths], check=True)
    changed = subprocess.run(
        ["git", "-C", str(repo_root), "diff", "--cached", "--quiet"],
        check=False,
    ).returncode != 0
    if not changed:
        return False

    subprocess.run(["git", "-C", str(repo_root), "commit", "-m", message], check=True)
    subprocess.run(["git", "-C", str(repo_root), "push", remote, "HEAD"], check=True)
    return True
