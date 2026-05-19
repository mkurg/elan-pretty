from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


@dataclass(frozen=True, slots=True)
class PublicationEntry:
    title: str
    slug: str
    url: str | None
    html_path: Path
    json_path: Path
    pdf_path: Path | None = None


def remote_to_pages_base_url(remote_url: str) -> str | None:
    """Infer the default GitHub Pages project URL from a GitHub remote."""

    normalized = remote_url.strip().removesuffix("/")
    patterns = (
        r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>.+?)(?:\.git)?$",
        r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>.+?)(?:\.git)?$",
        r"^ssh://git@github\.com/(?P<owner>[^/]+)/(?P<repo>.+?)(?:\.git)?$",
    )
    for pattern in patterns:
        match = re.match(pattern, normalized)
        if not match:
            continue
        owner = match.group("owner")
        repo = match.group("repo").removesuffix(".git")
        if repo.lower() == f"{owner.lower()}.github.io":
            return f"https://{owner}.github.io/"
        return f"https://{owner}.github.io/{repo}/"
    return None


def public_url_for_path(repo_root: Path, path: Path, base_url: str) -> str:
    relative = path.resolve().relative_to(repo_root.resolve())
    quoted = "/".join(quote(part) for part in relative.parts)
    if quoted:
        return f"{base_url.rstrip('/')}/{quoted}/"
    return f"{base_url.rstrip('/')}/"


def write_publication_index(site_root: Path, entries: list[PublicationEntry]) -> Path:
    site_root.mkdir(parents=True, exist_ok=True)
    index_path = site_root / "index.html"
    rows = "\n".join(_publication_row(entry) for entry in sorted(entries, key=lambda item: item.title))
    index_path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ELAN Pretty Publications</title>
  <style>
    :root {{
      color-scheme: light dark;
      font-family: "Charis SIL", "Libertinus Serif", "Noto Serif", Georgia, serif;
      background: #fbfaf7;
      color: #18211c;
    }}
    body {{
      margin: 0;
      padding: clamp(1rem, 4vw, 4rem);
    }}
    main {{
      max-width: 68rem;
      margin-inline: auto;
    }}
    h1 {{
      margin: 0 0 1.5rem;
      font-size: clamp(2rem, 5vw, 4rem);
      line-height: 1.05;
    }}
    ul {{
      display: grid;
      gap: 0.75rem;
      padding: 0;
      list-style: none;
    }}
    li {{
      border-block-start: 1px solid #d8d1c8;
      padding-block: 1rem;
    }}
    a {{
      color: #2a6f73;
      font-weight: 700;
    }}
    .links {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
      margin-block-start: 0.35rem;
      font-family: system-ui, sans-serif;
      font-size: 0.9rem;
    }}
  </style>
</head>
<body>
  <main>
    <h1>ELAN Pretty Publications</h1>
    <ul>
{rows}
    </ul>
  </main>
</body>
</html>
""",
        encoding="utf-8",
    )
    return index_path


def discover_publications(
    site_root: Path,
    *,
    repo_root: Path | None = None,
    base_url: str | None = None,
    exclude_slugs: set[str] | None = None,
) -> list[PublicationEntry]:
    excluded = exclude_slugs or set()
    entries: list[PublicationEntry] = []
    if not site_root.exists():
        return entries

    for directory in sorted(path for path in site_root.iterdir() if path.is_dir()):
        if directory.name in excluded:
            continue
        html_path = directory / "index.html"
        json_candidates = sorted(directory.glob("*.json"))
        if not html_path.exists() or not json_candidates:
            continue

        json_path = json_candidates[0]
        title = directory.name.replace("_", " ")
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict) and isinstance(payload.get("title"), str):
            title = payload["title"]

        pdf_candidates = sorted(directory.glob("*.pdf"))
        pdf_path = pdf_candidates[0] if pdf_candidates else None
        url = public_url_for_path(repo_root, directory, base_url) if repo_root and base_url else None
        entries.append(
            PublicationEntry(
                title=title,
                slug=directory.name,
                url=url,
                html_path=html_path,
                json_path=json_path,
                pdf_path=pdf_path,
            )
        )

    return entries


def write_root_redirect(repo_root: Path, target: str) -> Path:
    index_path = repo_root / "index.html"
    escaped_target = html.escape(target, quote=True)
    index_path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="0; url={escaped_target}">
  <title>ELAN Pretty</title>
</head>
<body>
  <main>
    <h1>ELAN Pretty</h1>
    <p><a href="{escaped_target}">Open the published interlinear texts</a>.</p>
  </main>
</body>
</html>
""",
        encoding="utf-8",
    )
    return index_path


def _publication_row(entry: PublicationEntry) -> str:
    title = html.escape(entry.title)
    json_href = html.escape(_relative_href(entry.html_path.parent, entry.json_path), quote=True)
    pdf_link = ""
    if entry.pdf_path is not None:
        pdf_href = html.escape(_relative_href(entry.html_path.parent, entry.pdf_path), quote=True)
        pdf_link = f'<a href="{entry.slug}/{pdf_href}">PDF</a>'
    public = ""
    if entry.url:
        public_url = html.escape(entry.url, quote=True)
        public = f'<a href="{public_url}">Public URL</a>'
    return f"""      <li>
        <a href="{entry.slug}/">{title}</a>
        <div class="links">
          <a href="{entry.slug}/">HTML</a>
          <a href="{entry.slug}/{json_href}">JSON</a>
          {pdf_link}
          {public}
        </div>
      </li>"""


def _relative_href(base_dir: Path, target: Path) -> str:
    return target.resolve().relative_to(base_dir.resolve()).as_posix()
