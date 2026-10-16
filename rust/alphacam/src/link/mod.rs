//! Serial link to the Alpha-T control (DESIGN.md 7). No GTK here; transfers run in worker threads.
//!
//! The transfers (send, receive, loopback) work on any [`serial_link::Port`]; opening real ports (tty devices through
//! the `serialport` crate, bt:// addresses over an RFCOMM socket) needs the crate feature `serial` (on by default),
//! and listing paired Bluetooth adapters from BlueZ the feature `bluez`.

pub mod serial_link;
