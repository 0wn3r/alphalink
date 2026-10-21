//! AlphaLink CAM core: the part model and its `.alcam` file, ALPHA-T's file formats, the toolpaths and the
//! ALPHA-T PRG post. No GTK here; the app is the `alphalink-cam` crate.

pub mod checks;
pub mod commands;
pub mod cycletime;
pub mod drawing;
pub mod error;
pub mod features;
pub mod i18n;
pub mod io;
pub mod link;
pub mod model;
pub mod moves;
pub mod numfmt;
pub mod post;
pub mod preview;
pub mod py;
pub mod safety;
pub mod setups;
pub mod stock;
pub mod tool_shapes;
pub mod toolpath;

pub use error::{Error, Result};
