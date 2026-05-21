from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

import typer

from elan_pretty.config import ProjectConfig, RenderConfig, load_config
from elan_pretty.github_pages import (
    PublicationEntry,
    discover_publications,
    public_url_for_path,
    remote_to_pages_base_url,
    write_publication_index,
    write_root_redirect,
)
from elan_pretty.mapping_registry import MappingRegistry
from elan_pretty.normalize import EafNormalizer
from elan_pretty.parser import EafParser
from elan_pretty.raw import RawEafDocument
from elan_pretty.render import HTMLRenderer, render_pdf
from elan_pretty.tier_detection import TierDetectionResult, suggest_tier_mapping
from elan_pretty.utils import safe_slug


app = typer.Typer(help="Render ELAN .eaf files as publication-quality interlinear text.")


@app.command()
def render(
    input_path: Path = typer.Argument(..., help="An .eaf file or a directory containing .eaf files."),
    output_dir: Path = typer.Argument(..., help="Directory for HTML, CSS, and JSON output."),
    config_path: Optional[Path] = typer.Option(
        None, "--config", "-c", help="YAML tier/render configuration."
    ),
    auto_detect_tiers: bool = typer.Option(
        False,
        "--auto-detect-tiers",
        help="Use heuristic tier detection for each input file.",
    ),
    suggest_tiers: bool = typer.Option(
        False,
        "--suggest-tiers",
        help="Print a heuristic tier mapping suggestion.",
    ),
    mapping_profile: Optional[str] = typer.Option(
        None,
        "--mapping-profile",
        help="Load a saved mapping profile from --mapping-dir.",
    ),
    mapping_dir: Path = typer.Option(
        Path("mappings"),
        "--mapping-dir",
        help="Directory containing saved mapping profile YAML files.",
    ),
    save_mapping: Optional[str] = typer.Option(
        None,
        "--save-mapping",
        help="Save the active mapping as a reusable profile.",
    ),
    pdf: bool = typer.Option(False, "--pdf", help="Also render a PDF."),
    pdf_backend: str = typer.Option(
        "auto", "--pdf-backend", help="PDF backend: auto, weasyprint, or chromium."
    ),
    audio_links: bool = typer.Option(False, "--audio-links", help="Enable timestamped audio controls."),
    theme: Optional[str] = typer.Option(None, "--theme", help="Theme: light, dark, or system."),
    title: Optional[str] = typer.Option(None, "--title", help="Override the document title."),
    inspect_tiers: bool = typer.Option(False, "--inspect-tiers", help="Print tier inventory."),
    github_pages: bool = typer.Option(
        False,
        "--github-pages",
        help="Render as GitHub Pages publications under OUTPUT_DIR/<slug>/.",
    ),
    pages_base_url: Optional[str] = typer.Option(
        None,
        "--pages-base-url",
        help="Override inferred GitHub Pages base URL.",
    ),
    commit_and_push: bool = typer.Option(
        False,
        "--commit-and-push",
        help="Commit rendered GitHub Pages artifacts and push to the configured remote.",
    ),
    remote: str = typer.Option("origin", "--remote", help="Git remote used for URL inference and push."),
    commit_message: Optional[str] = typer.Option(
        None, "--commit-message", help="Commit message for --commit-and-push."
    ),
) -> None:
    if config_path and mapping_profile:
        raise typer.BadParameter("Use either --config or --mapping-profile, not both")
    config = _with_cli_overrides(
        _load_project_config(config_path, mapping_profile, mapping_dir),
        audio_links=audio_links,
        theme=theme,
        title=title,
    )
    eaf_paths = _resolve_inputs(input_path)
    if not eaf_paths:
        raise typer.BadParameter(f"No .eaf files found at {input_path}")
    if commit_and_push and not github_pages:
        raise typer.BadParameter("--commit-and-push requires --github-pages")

    output_dir.mkdir(parents=True, exist_ok=True)
    parser = EafParser()
    renderer = HTMLRenderer()
    repo_root = _repo_root() if github_pages or commit_and_push else None
    if github_pages and repo_root is not None and not output_dir.resolve().is_relative_to(repo_root.resolve()):
        raise typer.BadParameter("GitHub Pages OUTPUT_DIR must be inside the git repository")
    base_url = _pages_base_url(repo_root, pages_base_url, remote) if github_pages else None
    publications: list[PublicationEntry] = []

    for eaf_path in eaf_paths:
        raw = parser.parse(eaf_path)
        if inspect_tiers:
            _print_tiers(raw)
        detected = suggest_tier_mapping(raw) if (suggest_tiers or auto_detect_tiers) else None
        if detected and suggest_tiers:
            _print_suggested_tiers(detected)

        active_config = config
        if detected and auto_detect_tiers:
            active_config = ProjectConfig(tiers=detected.mapping, render=config.render)
        if save_mapping:
            profile = MappingRegistry(mapping_dir).save(save_mapping, active_config, raw=raw)
            typer.echo(f"Saved mapping profile {profile.id} to {mapping_dir}")

        document = EafNormalizer(raw, active_config).normalize()
        stem = safe_slug(eaf_path.stem)
        render_dir = output_dir / stem if github_pages else output_dir
        html_stem = "index" if github_pages else stem

        render_dir.mkdir(parents=True, exist_ok=True)
        json_path = render_dir / f"{stem}.json"
        json_path.write_text(document.model_dump_json(indent=2), encoding="utf-8")

        html_path = renderer.write(document, render_dir, active_config, stem=html_stem)
        typer.echo(f"Wrote {html_path}")
        typer.echo(f"Wrote {json_path}")

        pdf_path: Path | None = None
        if pdf:
            pdf_path = render_dir / f"{stem}.pdf"
            render_pdf(html_path, pdf_path, backend=pdf_backend)
            typer.echo(f"Wrote {pdf_path}")

        if github_pages:
            url = public_url_for_path(repo_root, render_dir, base_url) if repo_root and base_url else None
            if url:
                typer.echo(f"Public URL: {url}")
            publications.append(
                PublicationEntry(
                    title=document.title,
                    slug=stem,
                    url=url,
                    html_path=html_path,
                    json_path=json_path,
                    pdf_path=pdf_path,
                )
            )

        for warning in document.warnings:
            typer.secho(f"warning: {warning}", fg=typer.colors.YELLOW, err=True)

    if github_pages:
        existing_publications = discover_publications(
            output_dir,
            repo_root=repo_root,
            base_url=base_url,
            exclude_slugs={publication.slug for publication in publications},
        )
        index_path = write_publication_index(output_dir, [*existing_publications, *publications])
        typer.echo(f"Wrote {index_path}")
        if repo_root is not None:
            root_index = write_root_redirect(
                repo_root,
                output_dir.resolve().relative_to(repo_root.resolve()).as_posix() + "/",
            )
            typer.echo(f"Wrote {root_index}")

    if commit_and_push:
        if repo_root is None:
            repo_root = _repo_root()
        message = commit_message or "Publish ELAN Pretty output"
        _commit_and_push(repo_root, [output_dir, repo_root / "index.html"], message, remote)


def _with_cli_overrides(
    config: ProjectConfig,
    *,
    audio_links: bool,
    theme: str | None,
    title: str | None,
) -> ProjectConfig:
    render_payload = config.render.model_dump()
    if audio_links:
        render_payload["audio_links"] = True
    if theme:
        render_payload["theme"] = theme
    if title:
        render_payload["title"] = title
    return ProjectConfig(tiers=config.tiers, render=RenderConfig.model_validate(render_payload))


def _load_project_config(
    config_path: Path | None,
    mapping_profile: str | None,
    mapping_dir: Path,
) -> ProjectConfig:
    if mapping_profile:
        return MappingRegistry(mapping_dir).load(mapping_profile).as_config()
    return load_config(config_path)


def _resolve_inputs(input_path: Path) -> list[Path]:
    if input_path.is_file():
        if input_path.suffix.lower() != ".eaf":
            raise typer.BadParameter(f"Input file is not an .eaf file: {input_path}")
        return [input_path]
    if input_path.is_dir():
        return sorted(input_path.rglob("*.eaf"))
    raise typer.BadParameter(f"Input path does not exist: {input_path}")


def _print_tiers(raw: RawEafDocument) -> None:
    typer.echo(f"\nTiers in {raw.path}:")
    typer.echo("TIER_ID\tPARENT_REF\tLINGUISTIC_TYPE\tANNOTATIONS")
    for tier in raw.tiers.values():
        typer.echo(
            f"{tier.id}\t{tier.parent_ref or ''}\t"
            f"{tier.linguistic_type_ref or ''}\t{len(tier.annotations)}"
        )


def _print_suggested_tiers(result: TierDetectionResult) -> None:
    typer.echo("\nSuggested tier mapping:")
    for role in ("reference", "phrase", "words", "morphemes", "gloss", "translation"):
        tier_id = getattr(result.mapping, role)
        value = ", ".join(tier_id) if isinstance(tier_id, list) else tier_id or ""
        typer.echo(f"{role}\t{value}")
    typer.echo(f"confidence\t{result.confidence:.0%}")
    for suggestion in result.roles:
        typer.echo(
            f"reason\t{suggestion.role}\t{suggestion.tier_id}\t"
            f"{suggestion.confidence:.0%}\t{suggestion.reason}"
        )


def _repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip())


def _pages_base_url(repo_root: Path | None, override: str | None, remote: str) -> str | None:
    if override:
        return override
    if repo_root is None:
        return None
    result = subprocess.run(
        ["git", "-C", str(repo_root), "remote", "get-url", remote],
        check=True,
        capture_output=True,
        text=True,
    )
    base_url = remote_to_pages_base_url(result.stdout.strip())
    if base_url is None:
        typer.secho(
            f"warning: could not infer GitHub Pages URL from remote {remote!r}",
            fg=typer.colors.YELLOW,
            err=True,
        )
    return base_url


def _commit_and_push(repo_root: Path, paths: list[Path], message: str, remote: str) -> None:
    relative_paths = [
        path.resolve().relative_to(repo_root.resolve()).as_posix()
        for path in paths
        if path.exists()
    ]
    if not relative_paths:
        typer.echo("No GitHub Pages paths exist to commit.")
        return

    subprocess.run(["git", "-C", str(repo_root), "add", *relative_paths], check=True)
    changed = subprocess.run(
        ["git", "-C", str(repo_root), "diff", "--cached", "--quiet"],
        check=False,
    ).returncode != 0
    if not changed:
        typer.echo("No rendered GitHub Pages changes to commit.")
        return

    subprocess.run(["git", "-C", str(repo_root), "commit", "-m", message], check=True)
    subprocess.run(["git", "-C", str(repo_root), "push", remote, "HEAD"], check=True)
