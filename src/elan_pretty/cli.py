from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from elan_pretty.config import ProjectConfig, RenderConfig, load_config
from elan_pretty.normalize import EafNormalizer
from elan_pretty.parser import EafParser
from elan_pretty.raw import RawEafDocument
from elan_pretty.render import HTMLRenderer, render_pdf
from elan_pretty.utils import safe_slug


app = typer.Typer(help="Render ELAN .eaf files as publication-quality interlinear text.")


@app.command()
def render(
    input_path: Path = typer.Argument(..., help="An .eaf file or a directory containing .eaf files."),
    output_dir: Path = typer.Argument(..., help="Directory for HTML, CSS, and JSON output."),
    config_path: Optional[Path] = typer.Option(
        None, "--config", "-c", help="YAML tier/render configuration."
    ),
    pdf: bool = typer.Option(False, "--pdf", help="Also render a PDF."),
    pdf_backend: str = typer.Option(
        "auto", "--pdf-backend", help="PDF backend: auto, weasyprint, or chromium."
    ),
    audio_links: bool = typer.Option(False, "--audio-links", help="Enable timestamped audio controls."),
    theme: Optional[str] = typer.Option(None, "--theme", help="Theme: light, dark, or system."),
    title: Optional[str] = typer.Option(None, "--title", help="Override the document title."),
    inspect_tiers: bool = typer.Option(False, "--inspect-tiers", help="Print tier inventory."),
) -> None:
    config = _with_cli_overrides(load_config(config_path), audio_links=audio_links, theme=theme, title=title)
    eaf_paths = _resolve_inputs(input_path)
    if not eaf_paths:
        raise typer.BadParameter(f"No .eaf files found at {input_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    parser = EafParser()
    renderer = HTMLRenderer()

    for eaf_path in eaf_paths:
        raw = parser.parse(eaf_path)
        if inspect_tiers:
            _print_tiers(raw)

        document = EafNormalizer(raw, config).normalize()
        stem = safe_slug(eaf_path.stem)

        json_path = output_dir / f"{stem}.json"
        json_path.write_text(document.model_dump_json(indent=2), encoding="utf-8")

        html_path = renderer.write(document, output_dir, config, stem=stem)
        typer.echo(f"Wrote {html_path}")
        typer.echo(f"Wrote {json_path}")

        if pdf:
            pdf_path = output_dir / f"{stem}.pdf"
            render_pdf(html_path, pdf_path, backend=pdf_backend)
            typer.echo(f"Wrote {pdf_path}")

        for warning in document.warnings:
            typer.secho(f"warning: {warning}", fg=typer.colors.YELLOW, err=True)


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
