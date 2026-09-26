"""Project endpoints. An entry can belong to many projects."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import CreativeEntry, EntryTag, Project, ProjectEntry, utcnow
from app.routes.entries import load_entry
from app.schemas import (
    ProjectCreate,
    ProjectDetail,
    ProjectEntryAdd,
    ProjectOut,
    ProjectUpdate,
    entry_summary,
    project_out,
)
from app.services.history import record_revision
from app.services.search import index_entry

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _entry_count(db: Session, project_id: int) -> int:
    return db.scalar(
        select(func.count()).select_from(ProjectEntry).join(CreativeEntry, CreativeEntry.id == ProjectEntry.entry_id)
        .where(ProjectEntry.project_id == project_id, CreativeEntry.deleted_at.is_(None))
    ) or 0


def _get(db: Session, project_id: int, include_deleted: bool = False) -> Project:
    project = db.get(Project, project_id)
    if project is None or (project.deleted_at is not None and not include_deleted):
        raise HTTPException(404, "Project not found")
    return project


def _detail(db: Session, project_id: int) -> ProjectDetail:
    project = _get(db, project_id)
    entries = db.scalars(
        select(CreativeEntry)
        .join(ProjectEntry, ProjectEntry.entry_id == CreativeEntry.id)
        .where(ProjectEntry.project_id == project_id, CreativeEntry.deleted_at.is_(None))
        .options(selectinload(CreativeEntry.entry_tags).selectinload(EntryTag.tag),
                 selectinload(CreativeEntry.projects), selectinload(CreativeEntry.attachments))
        .order_by(CreativeEntry.created_at.desc())
    ).all()
    return ProjectDetail(**project_out(project, len(entries)).model_dump(),
                         entries=[entry_summary(e) for e in entries])


@router.get("", response_model=list[ProjectOut])
def list_projects(trashed: bool = False, db: Session = Depends(get_db)):
    """Active projects, or with `trashed=true` the ones in the trash."""
    cond = Project.deleted_at.is_not(None) if trashed else Project.deleted_at.is_(None)
    order = Project.deleted_at.desc() if trashed else Project.updated_at.desc()
    projects = db.scalars(select(Project).where(cond).order_by(order)).all()
    return [project_out(p, _entry_count(db, p.id)) for p in projects]


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)):
    project = Project(
        name=payload.name.strip(),
        description=payload.description,
        project_type=payload.project_type.value,
        status=payload.status.value,
    )
    db.add(project)
    db.commit()
    return project_out(project, 0)


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project(project_id: int, db: Session = Depends(get_db)):
    return _detail(db, project_id)


@router.put("/{project_id}", response_model=ProjectDetail)
def update_project(project_id: int, payload: ProjectUpdate, db: Session = Depends(get_db)):
    project = _get(db, project_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(project, key, value.value if hasattr(value, "value") else value)
    db.commit()
    return _detail(db, project_id)


@router.post("/{project_id}/entries", response_model=ProjectDetail)
def add_entry_to_project(project_id: int, payload: ProjectEntryAdd, db: Session = Depends(get_db)):
    project = _get(db, project_id)
    entry = load_entry(db, payload.entry_id)
    if project not in entry.projects:
        entry.projects.append(project)
        project.updated_at = utcnow()
        record_revision(db, entry, f"Added to project “{project.name}”")
        index_entry(db, entry)
        db.commit()
    return _detail(db, project_id)


@router.delete("/{project_id}", status_code=204)
def trash_project(project_id: int, db: Session = Depends(get_db)):
    """Move a project to the trash. Its entries stay in the archive, untouched."""
    project = _get(db, project_id)
    project.deleted_at = utcnow()
    db.commit()
    return Response(status_code=204)


@router.post("/{project_id}/restore", response_model=ProjectDetail)
def restore_project(project_id: int, db: Session = Depends(get_db)):
    project = _get(db, project_id, include_deleted=True)
    project.deleted_at = None
    db.commit()
    return _detail(db, project_id)


@router.delete("/{project_id}/entries/{entry_id}", status_code=204)
def remove_entry_from_project(project_id: int, entry_id: int, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    entry = load_entry(db, entry_id)
    if project is not None and project in entry.projects:
        entry.projects.remove(project)
        record_revision(db, entry, f"Removed from project “{project.name}”")
        db.commit()
    return Response(status_code=204)
