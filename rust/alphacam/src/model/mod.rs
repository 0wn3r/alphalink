//! The part model: geometry, operations, tools and the project with its `.alcam` file.

pub mod geometry;
pub mod operations;
pub mod project;
pub mod serde_text;
pub mod tools;

pub use geometry::{Arc, Element, Line, Point, Text};
pub use operations::{
    AreaClear, Datum, Drill, Dwell, FaceClear, FinishPass, Groove, GrooveMode, MoveTo, OP_KINDS, Operation, Thread,
    ToolChange,
};
pub use project::{Billet, MachineProfile, Project, Reference};
pub use tools::{Tool, ToolLibrary, standard_library};
