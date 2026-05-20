from __future__ import annotations

import asyncio
import os
import re
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from elan_pretty.config import ProjectConfig, RenderConfig, TierMapping
from elan_pretty.mapping_registry import MappingProfile, MappingRegistry
from elan_pretty.parser import EafParser
from elan_pretty.publishing import (
    commit_and_push_paths,
    infer_pages_base_url,
    render_eaf_publication,
)
from elan_pretty.raw import RawEafDocument
from elan_pretty.tier_detection import suggest_tier_mapping
from elan_pretty.utils import safe_slug

try:
    from telegram import BotCommand, Update
    from telegram.constants import ChatAction
    from telegram.ext import (
        Application,
        ApplicationBuilder,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )
except ImportError:  # pragma: no cover - optional dependency entry point.
    BotCommand = None  # type: ignore[assignment]
    Update = Any  # type: ignore[misc, assignment]
    ChatAction = None  # type: ignore[assignment]
    Application = None  # type: ignore[assignment]
    ApplicationBuilder = None  # type: ignore[assignment]
    CommandHandler = None  # type: ignore[assignment]
    ContextTypes = None  # type: ignore[assignment]
    MessageHandler = None  # type: ignore[assignment]
    filters = None  # type: ignore[assignment]


ROLE_NAMES = ("reference", "phrase", "words", "morphemes", "gloss", "translation")
ASSIGNMENT_RE = re.compile(
    r"\b(reference|phrase|words|morphemes|gloss|translation)\s*=\s*([^\s,;]+)",
    re.IGNORECASE,
)


class BotSettings(BaseModel):
    repo_root: Path
    work_dir: Path
    mapping_dir: Path
    pages_dir: Path
    pages_base_url: str | None = None
    remote: str = "origin"
    pdf_backend: str = "auto"
    auto_render: bool = False
    auto_render_confidence: float = 0.92
    auto_git_push: bool = False
    allowed_user_ids: set[int] | None = None

    @classmethod
    def from_env(cls) -> BotSettings:
        repo_root = Path(os.environ.get("ELAN_PRETTY_REPO", Path.cwd())).expanduser().resolve()
        work_dir = _resolve_path(
            os.environ.get("ELAN_PRETTY_WORK_DIR"),
            repo_root / "data" / "bot",
            repo_root,
        )
        mapping_dir = _resolve_path(
            os.environ.get("ELAN_PRETTY_MAPPING_DIR"),
            repo_root / "mappings",
            repo_root,
        )
        pages_dir = _resolve_path(
            os.environ.get("ELAN_PRETTY_PAGES_DIR"),
            repo_root / "published",
            repo_root,
        )
        return cls(
            repo_root=repo_root,
            work_dir=work_dir,
            mapping_dir=mapping_dir,
            pages_dir=pages_dir,
            pages_base_url=os.environ.get("ELAN_PRETTY_PAGES_BASE_URL"),
            remote=os.environ.get("ELAN_PRETTY_GIT_REMOTE", "origin"),
            pdf_backend=os.environ.get("ELAN_PRETTY_PDF_BACKEND", "auto"),
            auto_render=_env_bool("ELAN_PRETTY_AUTO_RENDER", default=False),
            auto_render_confidence=float(
                os.environ.get("ELAN_PRETTY_AUTO_RENDER_CONFIDENCE", "0.92")
            ),
            auto_git_push=_env_bool("ELAN_PRETTY_AUTO_GIT_PUSH", default=False),
            allowed_user_ids=_allowed_user_ids(os.environ.get("TELEGRAM_ALLOWED_USER_IDS")),
        )


class PendingJob(BaseModel):
    job_id: str
    chat_id: int
    source_name: str
    eaf_path: str
    mapping: TierMapping
    render: RenderConfig = Field(default_factory=RenderConfig)
    detector_confidence: float = 0.0
    registry_profile_id: str | None = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    warnings: list[str] = Field(default_factory=list)

    def as_config(self) -> ProjectConfig:
        return ProjectConfig(tiers=self.mapping, render=self.render)


class ElanPrettyTelegramBot:
    def __init__(self, settings: BotSettings) -> None:
        self.settings = settings
        self.registry = MappingRegistry(settings.mapping_dir)
        self.parser = EafParser()
        self.settings.work_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.pending_dir.mkdir(parents=True, exist_ok=True)

    @property
    def upload_dir(self) -> Path:
        return self.settings.work_dir / "uploads"

    @property
    def pending_dir(self) -> Path:
        return self.settings.work_dir / "pending"

    def run(self, token: str) -> None:
        if ApplicationBuilder is None:
            msg = "Install Telegram support with: pip install -e '.[bot]'"
            raise RuntimeError(msg)

        application = (
            ApplicationBuilder()
            .token(token)
            .post_init(self._post_init)
            .build()
        )
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("help", self.help))
        application.add_handler(CommandHandler("whoami", self.whoami))
        application.add_handler(CommandHandler("mappings", self.mappings))
        application.add_handler(CommandHandler("use", self.use_mapping))
        application.add_handler(CommandHandler("cancel", self.cancel))
        application.add_handler(MessageHandler(filters.Document.ALL, self.eaf_document))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.text_reply))
        application.run_polling(allowed_updates=Update.ALL_TYPES)

    async def _post_init(self, application: Application) -> None:
        if BotCommand is None:
            return
        await application.bot.set_my_commands(
            [
                BotCommand("start", "Show bot overview"),
                BotCommand("whoami", "Show your Telegram user ID"),
                BotCommand("mappings", "List saved tier mappings"),
                BotCommand("use", "Use a saved mapping for the pending file"),
                BotCommand("cancel", "Cancel the pending file"),
                BotCommand("help", "Show usage help"),
            ]
        )

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        await self._reply(update, self._help_text())

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        await self._reply(update, self._help_text())

    async def whoami(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        user = update.effective_user
        if user is None:
            await self._reply(update, "No Telegram user is attached to this update.")
            return
        await self._reply(update, f"Your Telegram user ID is {user.id}.")

    async def mappings(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        profiles = self.registry.list_profiles()
        if not profiles:
            await self._reply(update, "No saved mappings yet. Upload an .eaf and use `ok save Name`.")
            return
        lines = ["Saved mappings:"]
        for profile in profiles:
            roles = ", ".join(
                f"{role}={tier_id}"
                for role, tier_id in profile.tiers.configured_roles().items()
                if not role.startswith("metadata.")
            )
            lines.append(f"- {profile.id}: {profile.name} ({roles})")
        lines.append("\nFor a pending upload, send `/use mapping-id`.")
        await self._reply(update, "\n".join(lines))

    async def use_mapping(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        chat_id = self._chat_id(update)
        if chat_id is None:
            return
        pending = self._load_pending(chat_id)
        if pending is None:
            await self._reply(update, "No pending .eaf file. Upload one first.")
            return
        if not context.args:
            await self._reply(update, "Send `/use mapping-id`, for example `/use gurung-w4r`.")
            return
        try:
            profile = self.registry.load(context.args[0])
        except ValueError as exc:
            await self._reply(update, str(exc))
            return
        pending.mapping = profile.tiers
        pending.render = profile.render
        pending.registry_profile_id = profile.id
        self._save_pending(pending)
        await self._reply(
            update,
            "Using saved mapping:\n\n"
            f"{self._format_profile(profile)}\n\n"
            "Reply `ok` to render, or adjust with pairs like `gloss=ge@A translation=ft@A`.",
        )

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        chat_id = self._chat_id(update)
        if chat_id is None:
            return
        path = self._pending_path(chat_id)
        if path.exists():
            path.unlink()
            await self._reply(update, "Canceled the pending upload.")
        else:
            await self._reply(update, "No pending upload to cancel.")

    async def eaf_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        chat_id = self._chat_id(update)
        message = update.effective_message
        if chat_id is None or message is None or message.document is None:
            return

        document = message.document
        filename = document.file_name or "upload.eaf"
        if not filename.casefold().endswith(".eaf"):
            await message.reply_text("Please send an ELAN `.eaf` file.")
            return

        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        job_id = uuid4().hex[:10]
        target = self.upload_dir / f"{job_id}-{safe_slug(Path(filename).stem)}.eaf"
        telegram_file = await context.bot.get_file(document.file_id)
        await telegram_file.download_to_drive(custom_path=target)

        try:
            raw = await asyncio.to_thread(self.parser.parse, target)
        except Exception as exc:  # noqa: BLE001 - surface parse errors to chat.
            await message.reply_text(f"I could not parse that EAF file:\n{exc}")
            return

        pending = self._make_pending(chat_id, job_id, filename, target, raw)
        self._save_pending(pending)

        if self.settings.auto_render and pending.detector_confidence >= self.settings.auto_render_confidence:
            await message.reply_text("The mapping looks very confident, so I am rendering it now.")
            await self._render_pending(update, context, pending, save_name=None)
            return

        await message.reply_text(self._suggestion_text(raw, pending))

    async def text_reply(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update):
            return
        chat_id = self._chat_id(update)
        message = update.effective_message
        if chat_id is None or message is None or not message.text:
            return
        pending = self._load_pending(chat_id)
        if pending is None:
            await message.reply_text("Upload an `.eaf` file first, then I can suggest a mapping.")
            return

        text = message.text.strip()
        lowered = text.casefold()
        if lowered in {"ok", "render", "yes", "go"}:
            await self._render_pending(update, context, pending, save_name=None)
            return

        save_name = self._save_name_from_text(text)
        if save_name is not None:
            await self._render_pending(update, context, pending, save_name=save_name)
            return

        assignments = self._parse_assignments(text)
        if assignments:
            payload = pending.mapping.model_dump()
            payload.update(assignments)
            pending.mapping = TierMapping.model_validate(payload)
            pending.registry_profile_id = None
            self._save_pending(pending)
            await message.reply_text(
                "Updated the pending mapping:\n\n"
                f"{self._format_mapping(pending.mapping)}\n\n"
                "Reply `ok` to render, or `ok save Name` to render and remember this mapping."
            )
            return

        await message.reply_text(
            "I have a pending `.eaf`. Reply `ok`, `ok save Name`, or corrections like "
            "`words=wd@A morphemes=mb@A gloss=ge@A`."
        )

    async def _render_pending(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        pending: PendingJob,
        *,
        save_name: str | None,
    ) -> None:
        message = update.effective_message
        chat_id = pending.chat_id
        if message is None:
            return

        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        saved_profile: MappingProfile | None = None
        config = pending.as_config()
        if save_name:
            raw = await asyncio.to_thread(self.parser.parse, Path(pending.eaf_path))
            saved_profile = self.registry.save(save_name, config, raw=raw, overwrite=True)

        slug = f"{safe_slug(Path(pending.source_name).stem)}-{pending.job_id}"
        pages_base_url = self._pages_base_url()

        try:
            rendered = await asyncio.to_thread(
                render_eaf_publication,
                Path(pending.eaf_path),
                self.settings.pages_dir,
                config,
                pdf=True,
                pdf_backend=self.settings.pdf_backend,
                github_pages=True,
                repo_root=self.settings.repo_root,
                pages_base_url=pages_base_url,
                slug=slug,
            )
        except Exception as exc:  # noqa: BLE001 - user needs actionable bot feedback.
            details = str(exc) or exc.__class__.__name__
            await message.reply_text(f"Rendering failed:\n{details}")
            traceback.print_exc()
            return

        pushed = False
        push_error: str | None = None
        if self.settings.auto_git_push:
            commit_paths = [self.settings.pages_dir, self.settings.repo_root / "index.html"]
            if saved_profile is not None:
                commit_paths.append(self.settings.mapping_dir)
            try:
                pushed = await asyncio.to_thread(
                    commit_and_push_paths,
                    self.settings.repo_root,
                    commit_paths,
                    message=f"Publish {Path(pending.source_name).stem}",
                    remote=self.settings.remote,
                )
            except Exception as exc:  # noqa: BLE001 - include deployment issue in reply.
                push_error = str(exc) or exc.__class__.__name__

        lines = ["Rendered the ELAN file."]
        if saved_profile:
            lines.append(f"Saved mapping: {saved_profile.id}")
        if rendered.public_url:
            lines.append(f"HTML: {rendered.public_url}")
        else:
            lines.append(f"HTML path: {rendered.html_path}")
        if self.settings.auto_git_push:
            lines.append("GitHub Pages push: done" if pushed else "GitHub Pages push: no changes")
        if push_error:
            lines.append(f"GitHub push failed: {push_error}")
        if rendered.document.warnings:
            warning_preview = "\n".join(rendered.document.warnings[:5])
            lines.append(f"\nWarnings:\n{warning_preview}")
        await message.reply_text("\n".join(lines))

        if rendered.pdf_path and rendered.pdf_path.exists():
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
            with rendered.pdf_path.open("rb") as handle:
                await message.reply_document(
                    document=handle,
                    filename=rendered.pdf_path.name,
                    caption="PDF export",
                )

        self._pending_path(chat_id).unlink(missing_ok=True)

    def _make_pending(
        self,
        chat_id: int,
        job_id: str,
        filename: str,
        path: Path,
        raw: RawEafDocument,
    ) -> PendingJob:
        detected = suggest_tier_mapping(raw)
        registry_suggestion = self.registry.suggest(raw)

        mapping = detected.mapping
        render = RenderConfig()
        profile_id: str | None = None
        confidence = detected.confidence
        if registry_suggestion and registry_suggestion.confidence >= 0.72:
            mapping = registry_suggestion.profile.tiers
            render = registry_suggestion.profile.render
            profile_id = registry_suggestion.profile.id
            confidence = registry_suggestion.confidence

        warnings = [*raw.warnings, *detected.warnings]
        if registry_suggestion and profile_id:
            warnings.append(
                f"Using saved mapping {profile_id} ({registry_suggestion.reason})."
            )
        return PendingJob(
            job_id=job_id,
            chat_id=chat_id,
            source_name=filename,
            eaf_path=str(path),
            mapping=mapping,
            render=render,
            detector_confidence=confidence,
            registry_profile_id=profile_id,
            warnings=warnings,
        )

    def _suggestion_text(self, raw: RawEafDocument, pending: PendingJob) -> str:
        detected = suggest_tier_mapping(raw)
        lines = [
            f"Got `{pending.source_name}`.",
            "",
            "Suggested mapping:",
            self._format_mapping(pending.mapping),
            "",
            f"Confidence: {pending.detector_confidence:.0%}",
        ]
        if pending.registry_profile_id:
            lines.append(f"Matched saved mapping: {pending.registry_profile_id}")
        else:
            role_reasons = [
                f"- {role.role}: {role.tier_id} ({role.confidence:.0%}, {role.reason})"
                for role in detected.roles
            ]
            if role_reasons:
                lines.extend(["", "Why:", *role_reasons[:6]])
        lines.extend(
            [
                "",
                "Reply `ok` to render.",
                "Reply `ok save Name` to render and remember this mapping.",
                "Or correct it with pairs like `gloss=ge@A translation=ft@A`.",
                "",
                "Note: GitHub Pages output is public.",
            ]
        )
        return "\n".join(lines)

    def _format_profile(self, profile: MappingProfile) -> str:
        return f"{profile.id}: {profile.name}\n{self._format_mapping(profile.tiers)}"

    def _format_mapping(self, mapping: TierMapping) -> str:
        lines = []
        for role in ROLE_NAMES:
            tier_id = getattr(mapping, role)
            lines.append(f"{role}: {tier_id or '-'}")
        return "\n".join(lines)

    def _save_name_from_text(self, text: str) -> str | None:
        lowered = text.casefold()
        for prefix in ("ok save ", "render save ", "save "):
            if lowered.startswith(prefix):
                name = text[len(prefix) :].strip()
                return name or "Telegram mapping"
        return None

    def _parse_assignments(self, text: str) -> dict[str, str]:
        assignments: dict[str, str] = {}
        for match in ASSIGNMENT_RE.finditer(text):
            assignments[match.group(1).casefold()] = match.group(2)
        return assignments

    def _pending_path(self, chat_id: int) -> Path:
        return self.pending_dir / f"{chat_id}.json"

    def _load_pending(self, chat_id: int) -> PendingJob | None:
        path = self._pending_path(chat_id)
        if not path.exists():
            return None
        return PendingJob.model_validate_json(path.read_text(encoding="utf-8"))

    def _save_pending(self, pending: PendingJob) -> None:
        self._pending_path(pending.chat_id).write_text(
            pending.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def _pages_base_url(self) -> str | None:
        if self.settings.pages_base_url:
            return self.settings.pages_base_url
        try:
            return infer_pages_base_url(self.settings.repo_root, self.settings.remote)
        except Exception:  # noqa: BLE001 - bot can still render local artifacts.
            return None

    async def _authorized(self, update: Update) -> bool:
        allowed = self.settings.allowed_user_ids
        if allowed is None:
            return True
        user = update.effective_user
        if user and user.id in allowed:
            return True
        await self._reply(update, "This bot is private.")
        return False

    async def _reply(self, update: Update, text: str) -> None:
        message = update.effective_message
        if message is not None:
            await message.reply_text(text)

    def _chat_id(self, update: Update) -> int | None:
        chat = update.effective_chat
        return chat.id if chat else None

    def _help_text(self) -> str:
        return (
            "Send me an ELAN `.eaf` file. I will suggest a tier mapping, then you can:\n\n"
            "- reply `ok` to render HTML/PDF\n"
            "- reply `ok save Name` to render and save the mapping\n"
            "- reply with corrections like `words=wd@A morphemes=mb@A gloss=ge@A`\n"
            "- use `/mappings` and `/use mapping-id` for saved profiles\n\n"
            "When GitHub publishing is enabled on the server, the HTML link I return is public."
        )


def _resolve_path(value: str | None, default: Path, repo_root: Path) -> Path:
    path = Path(value).expanduser() if value else default
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.casefold() in {"1", "true", "yes", "on"}


def _allowed_user_ids(value: str | None) -> set[int] | None:
    if not value:
        return None
    ids: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        ids.add(int(item))
    return ids


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        msg = "Set TELEGRAM_BOT_TOKEN before starting the bot."
        raise SystemExit(msg)
    settings = BotSettings.from_env()
    ElanPrettyTelegramBot(settings).run(token)


if __name__ == "__main__":
    main()
