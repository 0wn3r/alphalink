//! Undo/redo for project edits (DESIGN.md 3: the UI changes the model only through commands).
//!
//! A project is small (a few kB of JSON), so each command stores the project's JSON before and after the change.
//! That keeps every edit undoable without writing an inverse for each kind of change.

use serde_json::Value;

use crate::error::Result;
use crate::model::Project;

#[derive(Debug, Clone, PartialEq)]
pub struct Command {
    pub label: String,
    pub before: Value,
    pub after: Value,
}

/// Called with the project after every change the stack makes to it.
pub type OnChange = Box<dyn FnMut(&Project)>;

/// The stack owns the project it edits. Each setup has its own stack: the project's further setups are not part of
/// its history and are carried over unchanged.
pub struct UndoStack {
    pub project: Project,
    pub on_change: Option<OnChange>,
    pub limit: usize,
    undo: Vec<Command>,
    redo: Vec<Command>,
    /// None: never saved (a part that exists only in memory)
    saved: Option<Value>,
}

impl UndoStack {
    pub fn new(project: Project, on_change: Option<OnChange>, limit: usize) -> Self {
        let saved = Some(project.to_json(false));
        UndoStack { project, on_change, limit, undo: vec![], redo: vec![], saved }
    }

    /// Run `mutate(project)` as one undoable step. If it fails, the project is left unchanged.
    pub fn apply(&mut self, label: &str, mutate: impl FnOnce(&mut Project) -> Result<()>) -> Result<()> {
        let before = self.project.to_json(false); // each setup has its own stack
        let mut trial = Project::from_json(&before)?;
        mutate(&mut trial)?;
        let after = trial.to_json(false);
        if after == before {
            return Ok(());
        }
        self.set(&after);
        self.undo.push(Command { label: label.into(), before, after });
        // Python's `del undo[:-limit]`: a limit of 0 keeps everything
        if self.limit > 0 && self.undo.len() > self.limit {
            let excess = self.undo.len() - self.limit;
            self.undo.drain(..excess);
        }
        self.redo.clear();
        Ok(())
    }

    pub fn undo(&mut self) -> Option<String> {
        let cmd = self.undo.pop()?;
        self.set(&cmd.before);
        let label = cmd.label.clone();
        self.redo.push(cmd);
        Some(label)
    }

    pub fn redo(&mut self) -> Option<String> {
        let cmd = self.redo.pop()?;
        self.set(&cmd.after);
        let label = cmd.label.clone();
        self.undo.push(cmd);
        Some(label)
    }

    pub fn can_undo(&self) -> bool {
        !self.undo.is_empty()
    }

    pub fn can_redo(&self) -> bool {
        !self.redo.is_empty()
    }

    pub fn modified(&self) -> bool {
        self.saved.as_ref() != Some(&self.project.to_json(false))
    }

    pub fn mark_saved(&mut self) {
        self.saved = Some(self.project.to_json(false));
    }

    /// For a part that exists only in memory (e.g. a fresh DXF import).
    pub fn mark_unsaved(&mut self) {
        self.saved = None;
    }

    fn set(&mut self, data: &Value) {
        let mut fresh = Project::from_json(data).expect("a project's own JSON loads");
        // the other setups are not part of this stack's history
        fresh.setups = std::mem::take(&mut self.project.setups);
        self.project = fresh;
        if let Some(f) = &mut self.on_change {
            f(&self.project);
        }
    }
}
