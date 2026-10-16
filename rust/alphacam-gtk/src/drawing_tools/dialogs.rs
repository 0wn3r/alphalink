//! The drawing tools' dialogs (ui/drawing-dialogs.blp) and popovers: text, erase drawing, edit element, the
//! fillet / chamfer size, which element of a corner, the long-press menu, and the numbers of a construction.

use std::rc::Rc;

use adw::prelude::*;
use alphacam::drawing;
use alphacam::model::Point;
use alphacam::py::fmt_g;
use gtk::{gdk, gio, glib};

use super::{DrawingTools, element, screen_rect, weak};
use crate::i18n::{gettext, tr};
use crate::op_editor::fit_spin;
use crate::ui::{builder, object};

const FILLET_CHIPS: &[f64] = &[0.5, 1.0, 2.0, 3.0, 5.0];
const CHAMFER_CHIPS: &[f64] = &[0.5, 1.0, 2.0, 3.0];
const FILLET_CHIPS_INCH: &[f64] = &[0.01, 0.02, 0.04, 0.06, 0.1];
const CHAMFER_CHIPS_INCH: &[f64] = &[0.01, 0.02, 0.04, 0.06];

/// A popover on the canvas pointing at a screen point; it leaves the canvas when it closes.
fn canvas_popover(pop: &impl IsA<gtk::Popover>, canvas: &impl IsA<gtk::Widget>, sx: f64, sy: f64) {
    let pop = pop.as_ref();
    pop.set_parent(canvas);
    pop.set_pointing_to(Some(&screen_rect(sx, sy)));
    pop.connect_closed(|p| {
        let p = p.clone();
        glib::idle_add_local_once(move || p.unparent());
    });
}

impl DrawingTools {
    // --- text (ALPHA-T T) and erase drawing (Q) ---------------------------------------------------------

    pub(super) fn text_dialog(&self, p: Point) {
        let Some(project) = self.project() else { return };
        let ui = builder("drawing-dialogs");
        let alert: adw::AlertDialog = object(&ui, "text_alert");
        alert.set_body(&tr("At X{x} Z{z}", &[("x", &fmt_g(p.x)), ("z", &fmt_g(p.z))]));
        let text: gtk::Entry = object(&ui, "text_entry");
        let size: adw::SpinRow = object(&ui, "text_height_row");
        size.set_value(if project.units == "mm" { 5.0 } else { 0.2 });
        let this = self.rc();
        let entry = text.clone();
        alert.connect_response(None, weak!(this, move |_, response| {
            let t = entry.text().to_string();
            if response == "add" && !t.trim().is_empty() {
                let h = size.value();
                this.edit(&gettext("Text"), |q| drawing::add_text(q, p, &t, h));
            }
        }));
        alert.present(Some(&self.w()));
        text.grab_focus();
    }

    pub(super) fn erase_drawing_dialog(&self) {
        let alert: adw::AlertDialog = object(&builder("drawing-dialogs"), "erase_alert");
        let this = self.rc();
        alert.connect_response(None, weak!(this, move |_, response| {
            if response == "erase"
                && this.edit(&gettext("Erase drawing"), |p| {
                    drawing::erase_drawing(p);
                    Ok(())
                })
            {
                this.finish_chain();
                this.select_element_(None);
            }
        }));
        alert.present(Some(&self.w()));
    }

    // --- edit element ---------------------------------------------------------------------------------

    /// Change the selected line's or arc's end points (and an arc's radius) by typed values. Ends of other elements
    /// that met it move too unless switched off, so the profile stays joined.
    pub(super) fn edit_selected_element(&self) {
        let Some(gid) = self.s.borrow().selected.clone() else { return };
        let Some(project) = self.project() else { return };
        let Some(g) = element(&project, &gid).cloned() else { return };
        let ui = builder("drawing-dialogs");
        let dlg: adw::AlertDialog = object(&ui, "edit_alert");
        dlg.set_heading(Some(&tr("Edit {what}", &[("what", &gid)])));
        let mut values = vec![("start_x", g.a().x), ("start_z", g.a().z), ("end_x", g.b().x), ("end_z", g.b().z)];
        match g.radius() {
            Some(r) => values.push(("radius", r)),
            None => {
                let group: adw::PreferencesGroup = object(&ui, "edit_group");
                group.remove(&object::<adw::SpinRow>(&ui, "edit_radius_row"));
            }
        }
        let digits = if project.units == "inch" { 4 } else { 3 };
        let rows: Vec<adw::SpinRow> = values
            .iter()
            .map(|(key, value)| {
                let r: adw::SpinRow = object(&ui, &format!("edit_{key}_row"));
                r.set_digits(digits);
                r.set_value(*value);
                fit_spin(&r, -10000.0, 10000.0, r.digits());
                r
            })
            .collect();
        let follow: adw::SwitchRow = object(&ui, "edit_follow_row");
        let this = self.rc();
        dlg.connect_response(None, weak!(this, move |_, r| {
            if r != "ok" {
                return;
            }
            let v: Vec<f64> = rows.iter().map(|row| row.value()).collect();
            let (a, b) = (Point::new(v[0], v[1]), Point::new(v[2], v[3]));
            let radius = v.get(4).copied();
            let follow = follow.is_active();
            if this.edit(&tr("Edit {what}", &[("what", &gid)]), |p| {
                drawing::edit_element(p, &gid, a, b, radius, follow)
            }) {
                this.select_element_(Some(gid.clone()));
            }
        }));
        dlg.present(Some(&self.w()));
    }

    // --- fillet / chamfer size --------------------------------------------------------------------------

    pub(super) fn size_popover(&self, tool: &str, corner: Point) {
        let Some(project) = self.project() else { return };
        let tool: Rc<str> = tool.into();
        let pop = gtk::Popover::new();
        let (sx, sy) = self.c.state().view.to_screen(corner.x, corner.z);
        pop.set_parent(&self.c);
        pop.set_pointing_to(Some(&screen_rect(sx, sy)));
        let vbox = gtk::Box::new(gtk::Orientation::Vertical, 8);
        let point = format!("X{} Z{}", fmt_g(corner.x), fmt_g(corner.z));
        let title = gtk::Label::new(Some(&tr(
            if &*tool == "fillet" { "Radius at {point}" } else { "Chamfer at {point}" },
            &[("point", &point)],
        )));
        title.add_css_class("heading");
        vbox.append(&title);
        let angle = (&*tool == "chamfer").then(|| {
            // angle between the chamfer and the more axial line (lathe drawings: "2 × 30°", angle from the axis)
            let arow = gtk::Box::new(gtk::Orientation::Horizontal, 6);
            arow.append(&gtk::Label::builder().label(gettext("Angle")).xalign(0.0).hexpand(true).build());
            let angle = gtk::SpinButton::with_range(1.0, 89.0, 1.0);
            angle.set_digits(1);
            angle.set_value(45.0);
            angle.set_tooltip_text(Some(&gettext(
                "Between the chamfer and the line along Z; the size is measured along that line",
            )));
            for a in [30.0, 45.0, 60.0] {
                let ab = gtk::Button::with_label(&format!("{}°", fmt_g(a)));
                let spin = angle.clone();
                ab.connect_clicked(move |_| spin.set_value(a));
                arow.append(&ab);
            }
            arow.append(&angle);
            vbox.append(&arow);
            angle
        });
        let ang = {
            let angle = angle.clone();
            move || angle.as_ref().map_or(45.0, |a| a.value())
        };
        let chips = gtk::FlowBox::builder()
            .selection_mode(gtk::SelectionMode::None)
            .max_children_per_line(5)
            .column_spacing(6)
            .row_spacing(6)
            .build();
        let inch = project.units == "inch";
        let sizes = match (&*tool == "fillet", inch) {
            (true, true) => FILLET_CHIPS_INCH,
            (true, false) => FILLET_CHIPS,
            (false, true) => CHAMFER_CHIPS_INCH,
            (false, false) => CHAMFER_CHIPS,
        };
        let mut chip_buttons = vec![];
        let this = self.rc();
        for &v in sizes {
            let label = if &*tool == "fillet" { format!("R{}", fmt_g(v)) } else { format!("{}×45°", fmt_g(v)) };
            let b = gtk::Button::with_label(&label);
            b.add_css_class("pill");
            let (pop2, tool2, ang2) = (pop.clone(), tool.clone(), ang.clone());
            b.connect_clicked(weak!(this, move |_| {
                pop2.popdown();
                this.apply_corner(&tool2, corner, v, ang2());
            }));
            chips.append(&b);
            chip_buttons.push((b, v));
        }
        if let Some(angle) = &angle {
            angle.connect_value_changed(move |s| {
                for (b, v) in &chip_buttons {
                    b.set_label(&format!("{}×{}°", fmt_g(*v), fmt_g(s.value())));
                }
            });
        }
        vbox.append(&chips);
        let other = gtk::Box::new(gtk::Orientation::Horizontal, 6);
        let spin = gtk::SpinButton::with_range(0.01, 100.0, 0.1);
        spin.set_digits(3);
        spin.set_value(if inch { 0.04 } else { 1.0 });
        let ok = gtk::Button::with_mnemonic(&gettext("_Apply"));
        ok.add_css_class("suggested-action");
        let apply: Rc<dyn Fn()> = {
            let (spin, pop, tool) = (spin.clone(), pop.clone(), tool.clone());
            Rc::new(weak!(this, move || {
                spin.update(); // a typed value not yet committed
                pop.popdown();
                this.apply_corner(&tool, corner, spin.value(), ang());
            }))
        };
        let a = apply.clone();
        ok.connect_clicked(move |_| a());
        // keyboard: type the size, Enter applies (a spin button's own Enter only commits the value; capture phase gets
        // the key before it)
        let enter = gtk::EventControllerKey::new();
        enter.set_propagation_phase(gtk::PropagationPhase::Capture);
        enter.connect_key_pressed(move |_, kv, _, _| {
            if matches!(kv, gdk::Key::Return | gdk::Key::KP_Enter) {
                apply();
                return glib::Propagation::Stop;
            }
            glib::Propagation::Proceed
        });
        spin.add_controller(enter);
        other.append(&spin);
        other.append(&ok);
        vbox.append(&other);
        pop.set_child(Some(&vbox));
        pop.connect_closed(weak!(this, move |p| {
            let p = p.clone();
            glib::idle_add_local_once(move || p.unparent());
            this.c.grab_focus();
        }));
        pop.popup();
        spin.grab_focus();
        spin.select_region(0, -1);
    }

    // --- which element (TOUCH.md 5.2) and the long-press menu (TOUCH.md 4) ----------------------------------

    /// Two or more elements equally close to the tap (a corner): a small popover lists them.
    pub(super) fn choose_element(&self, gids: &[String]) {
        let pop = gtk::Popover::new();
        let vbox = gtk::Box::new(gtk::Orientation::Vertical, 4);
        let this = self.rc();
        for gid in gids {
            let b = gtk::Button::with_label(&self.element_label(gid));
            b.add_css_class("flat");
            let (pop2, gid) = (pop.clone(), gid.clone());
            b.connect_clicked(weak!(this, move |_| {
                pop2.popdown();
                this.select_element_(Some(gid.clone()));
            }));
            vbox.append(&b);
        }
        pop.set_child(Some(&vbox));
        let (sx, sy) = self.c.state().last_tap.screen;
        canvas_popover(&pop, &self.c, sx, sy);
        pop.popup();
    }

    /// A long press (or right click, or the Menu key): a context menu at the finger.
    pub(super) fn long_pressed(&self, sx: f64, sy: f64, element: &str) {
        if !self.w().has_project() || self.s.borrow().pick_op.is_some() {
            return;
        }
        self.s.borrow_mut().ctx_at = (sx, sy);
        let menu = gio::Menu::new();
        if !element.is_empty() {
            self.select_element_(Some(element.into()));
            let items = gio::Menu::new();
            items.append(Some(&gettext("Edit Values…")), Some("ctx.edit"));
            items.append(Some(&gettext("Delete")), Some("ctx.delete"));
            menu.append_section(None, &items);
        }
        let view = gio::Menu::new();
        view.append(Some(&gettext("Zoom Here")), Some("ctx.zoom"));
        view.append(Some(&gettext("Fit Part")), Some("win.fit"));
        menu.append_section(None, &view);
        let pop = gtk::PopoverMenu::from_model(Some(&menu));
        pop.insert_action_group("ctx", Some(&self.ctx));
        canvas_popover(&pop, &self.c, sx, sy);
        pop.set_has_arrow(true);
        pop.popup();
    }

    // --- numbers of a construction ----------------------------------------------------------------------

    /// A dialog with a number row per field (label, value, lowest, highest); Apply gives `done` the values, Cancel
    /// ends the construction.
    pub(super) fn numbers(&self, title: &str, fields: &[(String, f64, f64, f64)], done: impl Fn(Vec<f64>) + 'static) {
        let Some(project) = self.project() else { return };
        let ui = builder("drawing-dialogs");
        let dlg: adw::AlertDialog = object(&ui, "numbers_alert");
        dlg.set_heading(Some(title));
        let group: adw::PreferencesGroup = object(&ui, "numbers_group");
        let digits = if project.units == "inch" { 4 } else { 3 };
        let spins: Vec<adw::SpinRow> = fields
            .iter()
            .map(|(label, value, lo, hi)| {
                let r = adw::SpinRow::with_range(*lo, *hi, 0.5);
                r.set_title(label);
                r.set_digits(digits);
                r.set_value(*value);
                fit_spin(&r, *lo, *hi, digits); // room for the widest value ("100.000", not "l00.000")
                group.add(&r);
                r
            })
            .collect();
        let this = self.rc();
        dlg.connect_response(None, weak!(this, move |_, r| {
            if r == "ok" {
                done(spins.iter().map(|s| s.value()).collect());
            } else {
                this.cancel_construct();
            }
        }));
        dlg.present(Some(&self.w()));
    }
}
