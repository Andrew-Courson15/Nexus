"""Export the whole archive as plain, readable files: Markdown + every original file.

Unlike a backup (which is for restoring NEXUS), this is for people: it opens in any text
editor or notes app (Obsidian, Notion import, VS Code ...), needs no NEXUS, and keeps the
same honesty as the app: your words, your notes and the AI's layers are separate, labeled
sections.

    NEXUS export <date>/
      README.md                 index of every entry, newest first
      entries/<date> <title> (#id).md
      projects/<name>.md        each project and its entries
      files/<id>/<original file name>
"""
from __future__ import annotations

import re
import tempfile
from urllib.parse import quote
import zipfile
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.models import CreativeEntry, EntryTag, Project, ProjectEntry
from app.services.relationships import relationships_for

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def _safe(name: str, limit: int = 80) -> str:
    return _UNSAFE.sub(" ", name).strip().strip(".")[:limit].strip() or "Untitled"


def _yaml(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def attachment_name(n: int, a) -> str:
    return f"{n + 1}-{_safe(a.original_filename, 120)}"


def entry_filename(e: CreativeEntry) -> str:
    return f"{e.created_at:%Y-%m-%d} {_safe(e.title or 'Untitled')} (#{e.id}).md"


def entry_markdown(db: Session, e: CreativeEntry, names: dict[int, str]) -> str:
    tags = sorted(et.tag.name for et in e.entry_tags)
    meta = e.ai_metadata or {}
    lines = ["---", f"id: {e.id}", f"title: {_yaml(e.title or 'Untitled')}",
             f"title_by: {'you' if e.title_source == 'user' else 'AI'}", f"type: {e.entry_type}",
             f"status: {e.status}", f"captured: {e.created_at.isoformat()}", f"source: {e.source_type}"]
    if tags:
        lines.append("tags: [" + ", ".join(_yaml(t) for t in tags) + "]")
    projects = [p.name for p in e.projects if p.deleted_at is None]
    if projects:
        lines.append("projects: [" + ", ".join(_yaml(p) for p in projects) + "]")
    if e.shared_from:
        lines.append(f"shared_by: {_yaml(e.shared_from.get('by', ''))}")
    lines += ["---", "", f"# {e.title or 'Untitled'}", ""]

    who = (e.shared_from or {}).get("by")

    def section(title: str, body: str | None, note: str = "") -> None:
        if body and str(body).strip():
            lines.extend([f"## {title}", *([f"*{note}*", ""] if note else []), str(body).strip(), ""])

    section(f"{who}'s words" if who else "Your words", e.original_text, "exactly as captured, never modified")
    if who:
        section(f"{who}'s notes", (e.shared_from or {}).get("notes"))
    section("Your notes" if who else "Notes", e.user_notes)
    section("Transcript", e.transcript, "machine transcription of the recording")
    if e.attachments:
        lines.append("## Files")
        for n, a in enumerate(e.attachments):
            rel = f"../files/{e.id}/{attachment_name(n, a)}"
            link = quote(rel)
            lines.append(f"- {'!' if a.kind == 'image' and a.mime_type in ('image/jpeg', 'image/png', 'image/gif', 'image/webp') else ''}"
                         f"[{a.original_filename}]({link})")
        lines.append("")
    section("AI summary", e.ai_summary, "written by the AI, not by you")
    section("AI interpretation", e.ai_interpretation, "the AI's reading")
    section("AI inference", e.ai_inference, "possibilities, not facts")
    if meta.get("uncertainties"):
        section("Unknown / uncertain", "\n".join(f"- {u}" for u in meta["uncertainties"]))
    rels = relationships_for(db, e.id)
    if rels:
        lines.append("## Related")
        for r in rels:
            other = r.target_entry if r.source_entry_id == e.id else r.source_entry
            if other.id not in names:
                continue
            arrow = r.relationship_type.replace("_", " ")
            who_linked = "you" if r.origin == "user" or r.status == "accepted" else "AI suggestion"
            lines.append(f"- {arrow}: [{other.title or 'Untitled'}]({quote(names[other.id])}) ({who_linked})")
        lines.append("")
    return "\n".join(lines)


def export_markdown(db: Session) -> tuple[Path, str]:
    """Write the export zip to a temporary file. Returns (path, suggested file name)."""
    data_dir = get_settings().data_dir
    entries = db.scalars(select(CreativeEntry).where(CreativeEntry.deleted_at.is_(None))
                         .options(selectinload(CreativeEntry.entry_tags).selectinload(EntryTag.tag),
                                  selectinload(CreativeEntry.projects), selectinload(CreativeEntry.attachments))
                         .order_by(CreativeEntry.created_at.desc(), CreativeEntry.id.desc())).all()
    names = {e.id: entry_filename(e) for e in entries}
    root = f"NEXUS export {datetime.now():%Y-%m-%d}"
    tmp = Path(tempfile.mkstemp(suffix=".zip")[1])
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        index = [f"# {root}", "", f"{len(entries)} entries, newest first. Each file keeps your words, your notes "
                 "and the AI's layers in separate, labeled sections.", ""]
        for e in entries:
            zf.writestr(f"{root}/entries/{names[e.id]}", entry_markdown(db, e, names))
            index.append(f"- {e.created_at:%Y-%m-%d} · {e.entry_type} · [{e.title or 'Untitled'}]"
                         f"(entries/{quote(names[e.id])})")
            for n, a in enumerate(e.attachments):
                src = data_dir / a.stored_path
                if src.is_file():
                    zf.write(src, f"{root}/files/{e.id}/{attachment_name(n, a)}", compress_type=zipfile.ZIP_STORED)
        projects = db.scalars(select(Project).where(Project.deleted_at.is_(None)).order_by(Project.name)).all()
        if projects:
            index += ["", "## Projects", ""]
        for p in projects:
            ids = set(db.scalars(select(ProjectEntry.entry_id).where(ProjectEntry.project_id == p.id)))
            members = [e for e in entries if e.id in ids]
            body = [f"# {p.name}", "", f"*{p.project_type} · {p.status}*", ""]
            if p.description:
                body += [p.description, ""]
            body += [f"- [{e.title or 'Untitled'}](../entries/{quote(names[e.id])})" for e in members]
            fname = f"{_safe(p.name)}.md"
            zf.writestr(f"{root}/projects/{fname}", "\n".join(body) + "\n")
            index.append(f"- [{p.name}](projects/{quote(fname)}) ({len(members)} entries)")
        zf.writestr(f"{root}/README.md", "\n".join(index) + "\n")
    return tmp, f"{root}.zip"
