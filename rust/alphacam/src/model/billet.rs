//! The billet rows of New Part and Part Settings (ui/billet-group.blp).

use std::cell::Cell;
use std::rc::Rc;

use adw::prelude::*;
use adw::subclass::prelude::*;
use alphacam::model::project::Billet;
use gtk::glib;

/// Switching mm <-> inch converts the size rows already filled in (50 mm stays 50 mm, shown as 1.9685 in).
pub fn units_follow(units_row: &adw::ComboRow, rows: &[adw::SpinRow]) {
    let inch_now = Rc::new(Cell::new(units_row.selected() == 1));
    let rows = rows.to_vec();
    units_row.connect_selected_notify(move |row| {
        let inch = row.selected() == 1;
        if inch == inch_now.get() {
            return;
        }
        inch_now.set(inch);
        for r in &rows {
            let v = r.value();
            r.set_digits(if inch { 4 } else { 1 });
            r.set_value(if inch { alphacam::py::round(v / 25.4, 4) } else { alphacam::py::round(v * 25.4, 1) });
        }
    });
}

mod imp {
    use super::*;

    #[derive(Default, gtk::CompositeTemplate)]
    #[template(resource = "/io/github/_0wn3r/AlphaLinkCAM/ui/billet-group.ui")]
    pub struct BilletGroup {
        #[template_child]
        pub units_row: TemplateChild<adw::ComboRow>,
        #[template_child]
        pub diameter_row: TemplateChild<adw::SpinRow>,
        #[template_child]
        pub length_row: TemplateChild<adw::SpinRow>,
        #[template_child]
        pub bore_row: TemplateChild<adw::SpinRow>,
        #[template_child]
        pub jaws_row: TemplateChild<adw::ComboRow>,
        #[template_child]
        pub tailstock_row: TemplateChild<adw::SwitchRow>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for BilletGroup {
        const NAME: &'static str = "AlphaLinkBilletGroup";
        type Type = super::BilletGroup;
        type ParentType = adw::PreferencesGroup;

        fn class_init(klass: &mut Self::Class) {
            klass.bind_template();
        }

        fn instance_init(obj: &glib::subclass::InitializingObject<Self>) {
            obj.init_template();
        }
    }

    impl ObjectImpl for BilletGroup {}
    impl WidgetImpl for BilletGroup {}
    impl PreferencesGroupImpl for BilletGroup {}
}

glib::wrapper! {
    pub struct BilletGroup(ObjectSubclass<imp::BilletGroup>)
        @extends adw::PreferencesGroup, gtk::Widget,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget;
}

impl BilletGroup {
    /// Show `b`; inch parts get 4 decimals (a 0.75 in bar must not be rounded or clamped to 1). `units`: the units
    /// row too, first, and switching it converts the sizes.
    pub fn set_billet(&self, b: &Billet, inch: bool, units: bool) {
        let imp = self.imp();
        for (v, row) in [(b.diameter, &imp.diameter_row), (b.length, &imp.length_row), (b.bore, &imp.bore_row)] {
            row.set_digits(if inch { 4 } else { 1 });
            row.set_value(v);
        }
        imp.jaws_row.set_selected(if b.jaws == "internal" { 1 } else { 0 });
        imp.tailstock_row.set_active(b.tailstock);
        if units {
            imp.units_row.set_selected(if inch { 1 } else { 0 });
            imp.units_row.set_visible(true);
            units_follow(&imp.units_row, &[imp.diameter_row.get(), imp.length_row.get(), imp.bore_row.get()]);
        }
    }

    pub fn inch(&self) -> bool {
        self.imp().units_row.selected() == 1
    }

    pub fn billet(&self) -> Billet {
        let imp = self.imp();
        Billet {
            diameter: imp.diameter_row.value(),
            length: imp.length_row.value(),
            bore: imp.bore_row.value(),
            jaws: if imp.jaws_row.selected() == 1 { "internal" } else { "external" }.into(),
            tailstock: imp.tailstock_row.is_active(),
            ..Default::default()
        }
    }
}
