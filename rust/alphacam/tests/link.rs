//! Serial link over a pty pair: the test plays the control on the master side (app/tests/test_link.py). No real
//! serial device is ever opened.
#![cfg(all(unix, feature = "serial"))]

use std::io::{ErrorKind, Read, Write};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread::{self, sleep};
use std::time::{Duration, Instant};

use alphacam::Error;
use alphacam::link::serial_link::{self as sl, Port};
use serialport::{SerialPort, TTYPort};

fn sample() -> Vec<u8> {
    let mut s = b"%\r\nO2\r\n#132=5\r\n".to_vec();
    for _ in 0..40 {
        s.extend_from_slice(b"G1X40.Z-50.F0.3\r\n");
    }
    s.extend_from_slice(b"M65\r\n%\r\n");
    s
}

/// The master end of a pty acting as the Alpha-T control.
struct PtyControl {
    master: TTYPort,
    received: Vec<u8>,
}

impl PtyControl {
    fn read_until(&mut self, n: usize, timeout: f64) {
        let t0 = Instant::now();
        let mut buf = [0u8; 4096];
        while self.received.len() < n && t0.elapsed().as_secs_f64() < timeout {
            match self.master.read(&mut buf) {
                Ok(k) => self.received.extend_from_slice(&buf[..k]),
                Err(e) if e.kind() == ErrorKind::TimedOut || e.kind() == ErrorKind::WouldBlock => {}
                Err(e) => panic!("{e}"),
            }
        }
    }

    fn write(&mut self, data: &[u8]) {
        self.master.write_all(data).unwrap();
    }
}

/// (the control, the app's port on the slave end, the slave fd kept open as in the Python test)
fn setup() -> (PtyControl, Box<dyn Port>, TTYPort) {
    let (mut master, slave) = TTYPort::pair().unwrap();
    master.set_timeout(Duration::from_millis(5)).unwrap();
    let ser = sl::open_port(&slave.name().unwrap(), false).unwrap();
    (PtyControl { master, received: Vec::new() }, ser, slave)
}

/// Write `data` to the control's end after `delay`, from another thread.
fn later(ctl: PtyControl, delay: f64, data: Vec<u8>) -> thread::JoinHandle<PtyControl> {
    thread::spawn(move || {
        let mut ctl = ctl;
        sleep(Duration::from_secs_f64(delay));
        ctl.write(&data);
        ctl
    })
}

fn cancel_after(delay: f64) -> Arc<AtomicBool> {
    let cancel = Arc::new(AtomicBool::new(false));
    let c = cancel.clone();
    thread::spawn(move || {
        sleep(Duration::from_secs_f64(delay));
        c.store(true, Ordering::SeqCst);
    });
    cancel
}

#[test]
fn send_waits_for_xon_and_honours_xoff() {
    let (ctl, mut ser, _slave) = setup();
    let sample = sample();
    let total = sample.len();
    let control = thread::spawn(move || {
        let mut ctl = ctl;
        sleep(Duration::from_millis(200));
        ctl.read_until(1, 0.05);
        let before_xon = ctl.received.len(); // nothing may arrive before DC1
        ctl.write(&[sl::XON]);
        ctl.read_until(100, 10.0);
        ctl.write(&[0x13]); // DC3: pause
        sleep(Duration::from_millis(50));
        ctl.read_until(1_000_000, 0.3);
        let paused_at = ctl.received.len();
        ctl.read_until(1_000_000, 0.3); // (the Python test sleeps here without reading)
        let still_paused = ctl.received.len() == paused_at;
        ctl.write(&[sl::XON]);
        ctl.read_until(total, 10.0);
        (ctl, before_xon, paused_at, still_paused) // the master stays open until the end, as in tearDown
    });
    let mut log = sl::TransferLog::new();
    let opts = sl::SendOptions { char_delay_ms: 0.0, ..Default::default() }; // paced like a 9600 baud UART
    let n = sl::send(&mut ser, &sample, &opts, None, None, Some(&mut log)).unwrap();
    let (ctl, before_xon, paused_at, still_paused) = control.join().unwrap();
    let received = ctl.received;
    assert_eq!(n, sample.len());
    assert_eq!(received, sample);
    assert_eq!(before_xon, 0);
    assert!(still_paused);
    assert!(paused_at < 100 + 10 + 4, "paused at {paused_at}"); // stopped within 10 characters of DC3 (FANUC 087)
    assert!(log.events.iter().any(|(_, e)| e.contains("XOFF")));
    assert!(log.text().contains("waiting for DC1") && log.text().contains("end of transmission"));
}

#[test]
fn send_cancel_while_waiting() {
    let (mut ctl, mut ser, _slave) = setup();
    let cancel = cancel_after(0.1);
    let r = sl::send(&mut ser, &sample(), &sl::SendOptions::default(), None, Some(&cancel), None);
    assert!(matches!(r, Err(Error::Cancelled(ref d)) if d.is_empty()), "{r:?}");
    ctl.read_until(1, 0.1);
    assert!(ctl.received.is_empty());
}

#[test]
fn xon_timeout() {
    let (_ctl, mut ser, _slave) = setup();
    let opts = sl::SendOptions { xon_timeout: Some(Duration::from_millis(200)), ..Default::default() };
    let r = sl::send(&mut ser, &sample(), &opts, None, None, None);
    assert!(
        matches!(r, Err(Error::TransferTimeout { ref message, .. }) if message == "no DC1 (XON) from the control"),
        "{r:?}"
    );
}

#[test]
fn receive_until_second_percent() {
    let (ctl, mut ser, _slave) = setup();
    let backup = b"%\r\n:0001\r\nG01X1.Z-2.\r\n%\r\n";
    let _ctl = later(ctl, 0.1, [&backup[..], b"trailing"].concat());
    let got = sl::receive(&mut ser, None, None, Some(Duration::from_secs(3)), Some(sl::IDLE_TIMEOUT), None, None);
    assert_eq!(got.unwrap(), &backup[..backup.len() - 2]); // up to and including the second %, like ATMANOP Save
}

#[test]
fn receive_idle_timeout() {
    let (ctl, mut ser, _slave) = setup();
    let _ctl = later(ctl, 0.05, b"%\r\nhalf".to_vec());
    let r = sl::receive(
        &mut ser,
        None,
        None,
        Some(Duration::from_secs(3)),
        Some(Duration::from_millis(300)),
        None,
        None,
    );
    match r {
        Err(Error::TransferTimeout { message, data }) => {
            assert_eq!(data, b"%\r\nhalf"); // what arrived is kept, not thrown away
            assert_eq!(message, "transfer stopped after 7 bytes (no second %)");
        }
        r => panic!("{r:?}"),
    }
}

#[test]
fn receive_nothing() {
    let (_ctl, mut ser, _slave) = setup();
    let r = sl::receive(&mut ser, None, None, Some(Duration::from_millis(200)), None, None, None);
    assert_eq!(r, Err(Error::TransferTimeout { message: "nothing received".into(), data: vec![] }));
}

/// The control pauses between operations: with no idle limit the receive waits, reports the pause, and a stop by
/// the user keeps what arrived.
#[test]
fn receive_waits_through_pauses_and_keeps_data_on_stop() {
    let (ctl, mut ser, _slave) = setup();
    let ctl = later(ctl, 0.05, b"%\r\nfirst\r\n".to_vec());
    let _ctl = thread::spawn(move || {
        let mut ctl = ctl.join().unwrap();
        sleep(Duration::from_secs_f64(3.45));
        ctl.write(b"second\r\n");
        ctl
    });
    let cancel = cancel_after(6.0);
    let mut quiet = vec![];
    let mut on_quiet = |s: f64| quiet.push(s);
    let mut log = sl::TransferLog::new();
    let r = sl::receive(
        &mut ser,
        None,
        Some(&cancel),
        Some(Duration::from_secs(3)),
        None,
        Some(&mut log),
        Some(&mut on_quiet),
    );
    assert_eq!(r, Err(Error::Cancelled(b"%\r\nfirst\r\nsecond\r\n".to_vec())));
    assert!(!quiet.is_empty() && quiet.iter().all(|&q| q > 2.0), "{quiet:?}");
    assert!(log.events.iter().any(|(_, e)| e.starts_with("pause of ")));
    assert!(log.events.iter().any(|(_, e)| e.starts_with("stopped by the user after 18 bytes, 1 %")));
}

/// The NC ends its output with DC4; a backup with one % (or none) still ends there.
#[test]
fn receive_ends_at_dc4() {
    let (ctl, mut ser, _slave) = setup();
    let backup = b"\x12%\r\n:0001\r\nG01X1.Z-2.\r\n\x14";
    let _ctl = later(ctl, 0.1, [&backup[..], b"trailing"].concat());
    let got = sl::receive(&mut ser, None, None, Some(Duration::from_secs(3)), Some(Duration::from_secs(2)), None, None);
    assert_eq!(got.unwrap(), backup);
}

#[test]
fn soft_parity_on_the_wire() {
    let (mut master, slave) = TTYPort::pair().unwrap();
    master.set_timeout(Duration::from_millis(5)).unwrap();
    let mut ser = sl::open_port(&slave.name().unwrap(), true).unwrap();
    ser.write(b"C%").unwrap();
    let mut ctl = PtyControl { master, received: vec![] };
    ctl.read_until(2, 2.0);
    assert_eq!(ctl.received, [0xC3, 0xA5]);
    ctl.write(&[0x91, 0x25]); // DC1 with its parity bit
    let t0 = Instant::now();
    let mut got = vec![];
    while got.len() < 2 && t0.elapsed() < Duration::from_secs(2) {
        got.extend(ser.read(2).unwrap());
    }
    assert_eq!(got, b"\x11%");
}

// Pure

#[test]
fn manops_load_parity() {
    assert_eq!(sl::manops_load_bytes(b"%\x00A"), [0x25 | 0x80, 0x00, 0x41]);
    assert_eq!(sl::manops_load_bytes(b"\xff"), b"\x00");
    // 'C' = 0x43 has three bits set -> parity bit on
    assert_eq!(sl::manops_load_bytes(b"C"), [0xC3]);
}

struct Echo {
    buf: Vec<u8>,
}

impl Port for Echo {
    fn read(&mut self, n: usize) -> alphacam::Result<Vec<u8>> {
        let n = n.min(self.buf.len());
        Ok(self.buf.drain(..n).collect())
    }
    fn write(&mut self, data: &[u8]) -> alphacam::Result<usize> {
        self.buf.extend_from_slice(data);
        Ok(data.len())
    }
    fn in_waiting(&mut self) -> alphacam::Result<usize> {
        Ok(self.buf.len())
    }
    fn reset_input_buffer(&mut self) -> alphacam::Result<()> {
        self.buf.clear();
        Ok(())
    }
}

#[test]
fn loopback_with_echo() {
    let (ok, got) = sl::loopback(&mut Echo { buf: b"stale".to_vec() }, sl::LOOPBACK_MESSAGE, Duration::from_millis(500))
        .unwrap();
    assert!(ok);
    assert_eq!(got, sl::LOOPBACK_MESSAGE);
}

#[test]
fn transfer_log_text() {
    let mut log = sl::TransferLog::new();
    log.events.push((1.23456, "got 11".into()));
    log.events.push((12.0, "end".into()));
    assert_eq!(log.text(), "   1.235  got 11\n  12.000  end\n");
}

// Bluetooth

#[cfg(target_os = "linux")]
#[test]
fn rfcomm_port_over_a_socket() {
    use socket2::{Domain, Socket, Type};

    let (a, b) = Socket::pair(Domain::UNIX, Type::STREAM, None).unwrap();
    let mut port = sl::RfcommPort::with_socket("bt://aa:bb:cc:dd:ee:ff/3", a).unwrap();
    assert_eq!((port.address.as_str(), port.channel), ("AA:BB:CC:DD:EE:FF", 3));
    assert_eq!(port.read(10).unwrap(), b""); // nothing yet: a timeout, not an error
    (&b).write_all(b"\x11%\r\n").unwrap();
    assert_eq!(port.in_waiting().unwrap(), 4);
    assert_eq!(port.read(10).unwrap(), b"\x11%\r\n");
    port.write(b"O1").unwrap();
    let mut buf = [0u8; 10];
    let n = (&b).read(&mut buf).unwrap();
    assert_eq!(&buf[..n], b"O1");
    // no modem lines over RFCOMM
    assert_eq!(port.modem_lines(), sl::ModemLines { cts: None, dsr: None, cd: None, ri: None });
    (&b).write_all(b"junk").unwrap();
    port.reset_input_buffer().unwrap();
    assert_eq!(port.in_waiting().unwrap(), 0);
    assert_eq!(sl::RfcommPort::with_socket("bt://AA:BB:CC:DD:EE:FF", b).unwrap().channel, 1);
}

struct Wire {
    out: Vec<u8>,
    inp: Vec<u8>,
}

impl Port for Wire {
    fn read(&mut self, n: usize) -> alphacam::Result<Vec<u8>> {
        let n = n.min(self.inp.len());
        Ok(self.inp.drain(..n).collect())
    }
    fn write(&mut self, data: &[u8]) -> alphacam::Result<usize> {
        self.out.extend_from_slice(data);
        Ok(data.len())
    }
    fn in_waiting(&mut self) -> alphacam::Result<usize> {
        Ok(self.inp.len())
    }
    fn reset_input_buffer(&mut self) -> alphacam::Result<()> {
        self.inp.clear();
        Ok(())
    }
}

#[test]
fn soft_parity() {
    let mut port = sl::SoftParityPort::new(Wire { out: vec![], inp: vec![] });
    port.write(b"C%").unwrap(); // C = 0x43 has three bits set: parity on; % = 0x25, three bits: on
    assert_eq!(port.inner.out, [0xC3, 0xA5]);
    port.write(&sl::manops_load_bytes(b"C")).unwrap(); // already with parity (restore): not doubled up
    assert_eq!(*port.inner.out.last().unwrap(), 0xC3);
    port.inner.inp = vec![0x91, 0x25]; // DC1 with its parity bit
    assert_eq!(port.read(2).unwrap(), b"\x11%");
}

#[test]
fn bluetooth_ports_are_recognised() {
    assert!(sl::is_bluetooth("bt://AA:BB:CC:DD:EE:FF"));
    assert!(sl::is_bluetooth("/dev/rfcomm0"));
    assert!(!sl::is_bluetooth("/dev/ttyUSB0"));
    assert!(!sl::is_bluetooth(""));
}

#[test]
fn test_ports_only_in_end_to_end_tests() {
    // SAFETY: no other test reads or writes this variable.
    unsafe { std::env::set_var("ALPHALINK_TEST_PORTS", "/dev/pts/98::/dev/pts/99") };
    let ports = sl::list_ports();
    unsafe { std::env::remove_var("ALPHALINK_TEST_PORTS") };
    assert_eq!(
        ports,
        [("/dev/pts/98".to_string(), "test port".to_string()), ("/dev/pts/99".to_string(), "test port".to_string())]
    );
}
