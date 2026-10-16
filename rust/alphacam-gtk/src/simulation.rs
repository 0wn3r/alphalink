//! Simulation transport (simulation.py, ui/simulation.blp). STUB: being ported; the API below is what the window uses.

use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::glib;

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct SimulationBar {}

    #[glib::object_subclass]
    impl ObjectSubclass for SimulationBar {
        const NAME: &'static str = "AlphaLinkSimulationBar";
        type Type = super::SimulationBar;
        type ParentType = gtk::Box;
    }

    impl ObjectImpl for SimulationBar {}
    impl WidgetImpl for SimulationBar {}
    impl BoxImpl for SimulationBar {}
}

glib::wrapper! {
    pub struct SimulationBar(ObjectSubclass<imp::SimulationBar>)
        @extends gtk::Box, gtk::Widget,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget, gtk::Orientable;
}

use crate::canvas::LatheCanvas;

impl SimulationBar {
    /// `on_line(n)`: the simulation reached PRG line n (the Program page follows)
    pub fn new(_canvas: &LatheCanvas, _on_line: impl Fn(usize) + 'static) -> Self {
        glib::Object::new()
    }

    pub fn reset(&self) {}

    pub fn select_op(&self, _index: Option<usize>) {}

    pub fn goto(&self, _index: Option<usize>) {}
}
