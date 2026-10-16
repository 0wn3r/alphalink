//! AlphaLink CAM core: the part model and its `.alcam` file, ALPHA-T's file formats, the toolpaths and the
//! ALPHA-T PRG post. No GTK here; the app is the `alphalink-cam` crate.

pub mod error;
pub mod i18n;
pub mod io;
pub mod model;
pub mod numfmt;
pub mod post;
pub mod py;
pub mod toolpath;

pub use error::{Error, Result};
