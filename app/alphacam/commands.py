"""Undo/redo for project edits (DESIGN.md 3: the UI changes the model only through commands).

A project is small (a few kB of JSON), so each command stores the project's JSON before and after the change.
That keeps every edit undoable without writing an inverse for each kind of change."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .model.project import Project


@dataclass
class Command:
    label: str
    before: dict
    after: dict


class UndoStack:
    def __init__(self, project: Project, on_change: Callable[[Project], None] | None = None, limit: int = 200):
        self.project = project
        self.on_change = on_change
        self.limit = limit
        self._undo: list[Command] = []
        self._redo: list[Command] = []
        self._saved: dict = project.to_dict(with_setups=False)

    def apply(self, label: str, mutate: Callable[[Project], None]) -> None:
        """Run `mutate(project)` as one undoable step. If it raises, the project is left unchanged."""
        before = self.project.to_dict(with_setups=False)  # each setup has its own stack
        trial = Project.from_dict(before)
        mutate(trial)
        after = trial.to_dict(with_setups=False)
        if after == before:
            return
        self._set(after)
        self._undo.append(Command(label, before, after))
        del self._undo[:-self.limit]
        self._redo.clear()

    def undo(self) -> str | None:
        if not self._undo:
            return None
        cmd = self._undo.pop()
        self._redo.append(cmd)
        self._set(cmd.before)
        return cmd.label

    def redo(self) -> str | None:
        if not self._redo:
            return None
        cmd = self._redo.pop()
        self._undo.append(cmd)
        self._set(cmd.after)
        return cmd.label

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def modified(self) -> bool:
        return self.project.to_dict(with_setups=False) != self._saved

    def mark_saved(self) -> None:
        self._saved = self.project.to_dict(with_setups=False)

    def mark_unsaved(self) -> None:
        """For a part that exists only in memory (e.g. a fresh DXF import)."""
        self._saved = {}

    def _set(self, data: dict) -> None:
        fresh = Project.from_dict(data)
        fresh.setups = self.project.setups  # the other setups are not part of this stack's history
        # keep the same Project object so views holding a reference stay valid
        self.project.__dict__.update(fresh.__dict__)
        if self.on_change:
            self.on_change(self.project)
