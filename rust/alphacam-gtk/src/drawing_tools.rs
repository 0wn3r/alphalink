//! The drawing tools on the Part page (drawing_tools.py, ui/drawing-tools.blp). STUB: being ported; the API below is
//! what the window uses.

use std::rc::Rc;

use gtk::prelude::*;

use crate::canvas::LatheCanvas;
use crate::window::MainWindow;

pub struct DrawingTools {
    /// the value chip on the last drawn segment (touch), over the drawing
    pub chip: gtk::Widget,
    pub construct_banner: gtk::Widget,
    pub pick_banner: gtk::Widget,
    /// the tool bar under the drawing
    pub bar: gtk::Widget,
}

impl DrawingTools {
    pub fn new(_window: &MainWindow, _canvas: &LatheCanvas) -> Rc<Self> {
        let hidden = || {
            let w: gtk::Widget = gtk::Box::new(gtk::Orientation::Horizontal, 0).upcast();
            w.set_visible(false);
            w
        };
        Rc::new(DrawingTools { chip: hidden(), construct_banner: hidden(), pick_banner: hidden(), bar: hidden() })
    }

    pub fn set_touch(&self, _touch: bool) {}

    pub fn set_tool(&self, _name: &str) {}

    /// end the line being drawn
    pub fn finish_chain(&self) {}

    pub fn select_element(&self, _id: Option<&str>) {}

    /// pick the contour of operation n on the drawing
    pub fn start_pick(&self, _op_index: usize) {}

    /// the operation whose contour is being picked
    pub fn pick_op(&self) -> Option<usize> {
        None
    }

    /// after undo/redo: drop what refers to elements that are gone
    pub fn after_history(&self) {}

    /// the keyboard put the cursor (the read-out shows it until the pointer really moves)
    pub fn key_pointer(&self) -> bool {
        false
    }
}
