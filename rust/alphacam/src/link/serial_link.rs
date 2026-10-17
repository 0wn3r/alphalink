//! Alpha-T serial transfers (data/line_settings.json, docs/RE_NOTES.md Progress 26/29, tools/alphalink.py).
//!
//! Line: 9600 baud, 7 data bits, even parity, 2 stop bits, DTR and RTS asserted. Flow control is software:
//! the control sends DC1 (0x11) to start or resume and DC3 (0x13; 0x93 seen from some drivers) to pause, and the
//! sender must stop within 10 characters of DC3 (FANUC alarm 087). We therefore write in small chunks, paced at the
//! line rate, with an optional per-character delay that flushes USB adapters byte by byte (2 ms works on the 400T).
//!
//! Every function takes an already-open [`Port`] (a serial port from [`open_port`] or a test double) so the logic is
//! testable without hardware. `cancel` is an [`AtomicBool`] set from another thread; `progress(done, total)` is
//! called from the worker thread.
//!
//! The port's own flow control is off: XON/XOFF are read and honoured here, as the control expects.

use std::sync::atomic::{AtomicBool, Ordering};
use std::thread::sleep;
use std::time::{Duration, Instant};

use crate::error::{Error, Result};
use crate::i18n::tr;

pub const XON: u8 = 0x11;
pub const XOFFS: [u8; 2] = [0x13, 0x93];
pub const BAUD_RATE: u32 = 9600;
pub const BITS_PER_CHAR: u32 = 1 + 7 + 1 + 2;
/// DC2, the NC's punch start code (FANUC: DC2 before the data, DC4 after it).
pub const DC2: u8 = 0x12;
/// DC4, the NC's punch end code.
pub const DC4: u8 = 0x14;
/// How long a read waits for its bytes (pyserial's `timeout=0.05`).
pub const READ_TIMEOUT: Duration = Duration::from_millis(50);
/// How long a write may block (pyserial's `write_timeout=5`).
pub const WRITE_TIMEOUT: Duration = Duration::from_secs(5);
/// [`receive`]'s usual idle limit once data has flowed.
pub const IDLE_TIMEOUT: Duration = Duration::from_secs(10);

/// Each 7-bit character with even parity in bit 7; bytes above 0x7F become 0.
pub const EVEN_PARITY: [u8; 256] = {
    let mut table = [0u8; 256];
    let mut c = 0;
    while c < 128 {
        table[c] = if (c as u8).count_ones() & 1 == 1 { c as u8 | 0x80 } else { c as u8 };
        c += 1;
    }
    table
};

/// The state of the port's modem input lines; `None` where the port has none (ptys, RFCOMM).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct ModemLines {
    pub cts: Option<bool>,
    pub dsr: Option<bool>,
    pub cd: Option<bool>,
    pub ri: Option<bool>,
}

/// The parts of a serial port (pyserial's interface) the transfers use.
pub trait Port: Send {
    /// Up to `n` bytes: fewer, or none, when the read timeout passes first.
    fn read(&mut self, n: usize) -> Result<Vec<u8>>;
    /// Write all of `data`; returns the number of bytes written.
    fn write(&mut self, data: &[u8]) -> Result<usize>;
    /// Wait until what was written has left.
    fn flush(&mut self) -> Result<()> {
        Ok(())
    }
    /// Bytes received and not read yet.
    fn in_waiting(&mut self) -> Result<usize>;
    /// Throw away what was received and not read yet.
    fn reset_input_buffer(&mut self) -> Result<()>;
    fn modem_lines(&mut self) -> ModemLines {
        ModemLines::default()
    }
    /// The device path or address the port was opened with.
    fn name(&self) -> Option<String> {
        None
    }
}

impl<P: Port + ?Sized> Port for Box<P> {
    fn read(&mut self, n: usize) -> Result<Vec<u8>> {
        (**self).read(n)
    }
    fn write(&mut self, data: &[u8]) -> Result<usize> {
        (**self).write(data)
    }
    fn flush(&mut self) -> Result<()> {
        (**self).flush()
    }
    fn in_waiting(&mut self) -> Result<usize> {
        (**self).in_waiting()
    }
    fn reset_input_buffer(&mut self) -> Result<()> {
        (**self).reset_input_buffer()
    }
    fn modem_lines(&mut self) -> ModemLines {
        (**self).modem_lines()
    }
    fn name(&self) -> Option<String> {
        (**self).name()
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct SendOptions {
    /// Wait for the control's DC1 before the first byte (recommended, TOUCH/RE_NOTES).
    pub wait_xon: bool,
    /// `None` = until cancelled.
    pub xon_timeout: Option<Duration>,
    pub char_delay_ms: f64,
    pub line_delay_ms: f64,
    /// Bytes per write when no per-character delay is used.
    pub chunk: usize,
    /// Sleep for the bytes' time on the wire (keeps ptys/sockets realistic).
    pub pace: bool,
}

impl Default for SendOptions {
    fn default() -> Self {
        Self { wait_xon: true, xon_timeout: None, char_delay_ms: 2.0, line_delay_ms: 0.0, chunk: 4, pace: true }
    }
}

/// Timestamped record of a transfer, for diagnosis (DESIGN.md 7).
#[derive(Debug, Clone)]
pub struct TransferLog {
    /// (seconds since the log started, text)
    pub events: Vec<(f64, String)>,
    pub t0: Instant,
}

impl Default for TransferLog {
    fn default() -> Self {
        Self { events: Vec::new(), t0: Instant::now() }
    }
}

impl TransferLog {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn add(&mut self, text: impl Into<String>) {
        self.events.push((self.t0.elapsed().as_secs_f64(), text.into()));
    }

    pub fn text(&self) -> String {
        self.events.iter().map(|(t, e)| format!("{t:8.3}  {e}\n")).collect()
    }
}

/// bt://AA:BB:CC:DD:EE:FF[/channel]: a paired Bluetooth serial (SPP) adapter, straight over RFCOMM.
pub const BT_PREFIX: &str = "bt://";
pub const SPP_UUID: &str = "00001101-0000-1000-8000-00805f9b34fb";

pub fn is_bluetooth(path: &str) -> bool {
    !path.is_empty() && (path.starts_with(BT_PREFIX) || basename(path).starts_with("rfcomm"))
}

fn basename(path: &str) -> &str {
    path.rsplit('/').next().unwrap_or(path)
}

/// For adapters that only frame 8 data bits: set them to 9600 8N2 and the app makes the 7E2 characters itself
/// (even parity in bit 7 going out, bit 7 dropped coming in). On the wire it is the same frame as 7E2.
pub struct SoftParityPort<P> {
    pub inner: P,
}

impl<P: Port> SoftParityPort<P> {
    pub fn new(inner: P) -> Self {
        Self { inner }
    }
}

impl<P: Port> Port for SoftParityPort<P> {
    fn read(&mut self, n: usize) -> Result<Vec<u8>> {
        Ok(self.inner.read(n)?.into_iter().map(|b| b & 0x7F).collect())
    }
    fn write(&mut self, data: &[u8]) -> Result<usize> {
        let framed: Vec<u8> = data.iter().map(|&b| EVEN_PARITY[(b & 0x7F) as usize]).collect();
        self.inner.write(&framed)
    }
    fn flush(&mut self) -> Result<()> {
        self.inner.flush()
    }
    fn in_waiting(&mut self) -> Result<usize> {
        self.inner.in_waiting()
    }
    fn reset_input_buffer(&mut self) -> Result<()> {
        self.inner.reset_input_buffer()
    }
    fn modem_lines(&mut self) -> ModemLines {
        self.inner.modem_lines()
    }
    fn name(&self) -> Option<String> {
        self.inner.name()
    }
}

fn check(cancel: Option<&AtomicBool>) -> Result<()> {
    match cancel {
        Some(c) if c.load(Ordering::SeqCst) => Err(Error::Cancelled(Vec::new())),
        _ => Ok(()),
    }
}

fn hex(data: &[u8], sep: &str) -> String {
    data.iter().map(|b| format!("{b:02x}")).collect::<Vec<_>>().join(sep)
}

pub fn wait_for_xon(
    ser: &mut dyn Port,
    timeout: Option<Duration>,
    cancel: Option<&AtomicBool>,
    mut log: Option<&mut TransferLog>,
) -> Result<()> {
    let t0 = Instant::now();
    loop {
        check(cancel)?;
        let b = ser.read(1)?;
        if let Some(&first) = b.first() {
            if let Some(log) = log.as_deref_mut() {
                log.add(format!("got {}", hex(&b, "")));
            }
            if first == XON {
                return Ok(());
            }
        }
        if timeout.is_some_and(|t| t0.elapsed() > t) {
            return Err(Error::TransferTimeout { message: "no DC1 (XON) from the control".into(), data: Vec::new() });
        }
    }
}

/// Send `data` unchanged (CR LF kept). Returns the number of bytes sent.
pub fn send(
    ser: &mut dyn Port,
    data: &[u8],
    opts: &SendOptions,
    mut progress: Option<&mut dyn FnMut(usize, usize)>,
    cancel: Option<&AtomicBool>,
    log: Option<&mut TransferLog>,
) -> Result<usize> {
    let mut own = TransferLog::new();
    let log = log.unwrap_or(&mut own);
    if opts.wait_xon {
        log.add("waiting for DC1");
        wait_for_xon(ser, opts.xon_timeout, cancel, Some(&mut *log))?;
    }
    log.add(format!("sending {} bytes", data.len()));
    let per_char = opts.char_delay_ms > 0.0 || opts.line_delay_ms > 0.0;
    let step = if per_char { 1 } else { opts.chunk.max(1) };
    let (mut paused, mut i) = (false, 0);
    while i < data.len() {
        check(cancel)?;
        let waiting = ser.in_waiting()?;
        if waiting > 0 {
            for b in ser.read(waiting)? {
                if XOFFS.contains(&b) && !paused {
                    paused = true;
                    log.add(format!("XOFF at {i}"));
                } else if b == XON && paused {
                    paused = false;
                    log.add(format!("XON at {i}"));
                }
            }
        }
        if paused {
            sleep(Duration::from_millis(10));
            continue;
        }
        let blk = &data[i..(i + step).min(data.len())];
        ser.write(blk)?;
        i += blk.len();
        if opts.pace {
            sleep(Duration::from_secs_f64((blk.len() as u32 * BITS_PER_CHAR) as f64 / BAUD_RATE as f64));
        }
        if per_char {
            ser.flush()?;
            let mut delay = opts.char_delay_ms;
            if blk == b"\r" || blk == b"\n" {
                delay += opts.line_delay_ms;
            }
            sleep(Duration::from_secs_f64(delay / 1000.0));
        }
        if let Some(p) = progress.as_deref_mut() {
            p(i, data.len());
        }
    }
    ser.flush()?;
    log.add("end of transmission");
    Ok(i)
}

fn tail(data: &[u8]) -> String {
    hex(&data[data.len().saturating_sub(48)..], " ")
}

/// Receive until the second '%' (Manual Ops Save, as ATMANOP stores it), or the NC's DC4 end code once data has
/// come. Raw bytes are returned before any decoding. first_timeout: give up if nothing arrives at all;
/// idle_timeout: give up after a pause once data flowed (the bytes so far ride on the TransferTimeout); None waits
/// for the end mark or the user (the control pauses between operations). quiet(seconds) is called about once a second
/// while the line is quiet after data, so the user sees the pause.
///
/// The usual idle limit is [`IDLE_TIMEOUT`] (Python's default).
pub fn receive(
    ser: &mut dyn Port,
    mut progress: Option<&mut dyn FnMut(usize)>,
    cancel: Option<&AtomicBool>,
    first_timeout: Option<Duration>,
    idle_timeout: Option<Duration>,
    log: Option<&mut TransferLog>,
    mut quiet: Option<&mut dyn FnMut(f64)>,
) -> Result<Vec<u8>> {
    let mut own = TransferLog::new();
    let log = log.unwrap_or(&mut own);
    let mut out: Vec<u8> = Vec::new();
    let mut percents = 0;
    let mut t_last = Instant::now();
    let mut t_quiet: Option<Instant> = None;
    loop {
        if cancel.is_some_and(|c| c.load(Ordering::SeqCst)) {
            log.add(format!("stopped by the user after {} bytes, {percents} %; last bytes: {}", out.len(), tail(&out)));
            return Err(Error::Cancelled(out));
        }
        let chunk = match ser.read(256) {
            Ok(chunk) => chunk,
            Err(Error::Io(message)) if !out.is_empty() => {
                log.add(format!("port failed after {} bytes ({message}); last bytes: {}", out.len(), tail(&out)));
                return Err(Error::ConnectionLost { message, data: out });
            }
            Err(e) => return Err(e),
        };
        let now = Instant::now();
        let since = now.duration_since(t_last);
        if !chunk.is_empty() {
            if !out.is_empty() && since.as_secs_f64() > 2.0 {
                log.add(format!("pause of {:.1} s after {} bytes", since.as_secs_f64(), out.len()));
            }
            if out.is_empty() {
                log.add(format!("first bytes: {}", hex(&chunk[..chunk.len().min(24)], " ")));
            }
            t_last = now;
            for b in chunk {
                out.push(b);
                if b & 0x7F == 0x25 {
                    percents += 1;
                    log.add(format!("% at byte {}", out.len()));
                    if percents == 2 {
                        log.add(format!("second % after {} bytes", out.len()));
                        return Ok(out);
                    }
                } else if b & 0x7F == DC4 && out.len() > 1 {
                    log.add(format!("DC4 (end) after {} bytes, {percents} %; last bytes: {}", out.len(), tail(&out)));
                    return Ok(out);
                }
            }
            if let Some(p) = progress.as_deref_mut() {
                p(out.len());
            }
        } else if out.is_empty() && first_timeout.is_some_and(|t| since > t) {
            return Err(Error::TransferTimeout { message: tr("nothing received", &[]), data: Vec::new() });
        } else if !out.is_empty() && idle_timeout.is_some_and(|t| since > t) {
            log.add(format!("idle timeout after {} bytes, {percents} %; last bytes: {}", out.len(), tail(&out)));
            let message = tr("transfer stopped after {n} bytes (no second %)", &[("n", &out.len())]);
            return Err(Error::TransferTimeout { message, data: out });
        } else if !out.is_empty()
            && quiet.is_some()
            && since.as_secs_f64() > 2.0
            && t_quiet.is_none_or(|q| now.duration_since(q) >= Duration::from_secs(1))
        {
            t_quiet = Some(now);
            if let Some(q) = quiet.as_deref_mut() {
                q(since.as_secs_f64());
            }
        }
    }
}

/// Manual Ops Load (ATMANOP): even parity put in bit 7 of each byte, bytes above 0x7F become 0.
pub fn manops_load_bytes(data: &[u8]) -> Vec<u8> {
    data.iter().map(|&b| EVEN_PARITY[b as usize]).collect()
}

/// What [`loopback`] sends unless told otherwise.
pub const LOOPBACK_MESSAGE: &[u8] = b"%\r\nO1234 LOOPBACK TEST\r\n%\r\n";

/// With pins 2 and 3 bridged at the adapter, what goes out must come straight back.
pub fn loopback(ser: &mut dyn Port, message: &[u8], wait: Duration) -> Result<(bool, Vec<u8>)> {
    ser.reset_input_buffer()?;
    ser.write(message)?;
    ser.flush()?;
    let mut got = Vec::new();
    let t0 = Instant::now();
    while t0.elapsed() < wait && got.len() < message.len() {
        got.extend(ser.read(message.len() - got.len())?);
    }
    Ok((got == message, got))
}

#[cfg(feature = "serial")]
pub use hw::*;

/// Real ports: tty devices through the `serialport` crate, bt:// addresses over an RFCOMM socket.
#[cfg(feature = "serial")]
mod hw {
    use std::io::{ErrorKind, Read, Write};
    use std::time::{Duration, Instant};

    use serialport::{ClearBuffer, DataBits, FlowControl, Parity, SerialPort, StopBits};

    use super::{BAUD_RATE, BT_PREFIX, ModemLines, Port, READ_TIMEOUT, SoftParityPort, WRITE_TIMEOUT};
    use crate::error::{Error, Result};

    fn io_error(e: impl std::fmt::Display) -> Error {
        Error::Io(e.to_string())
    }

    /// A serial port opened by [`open_port`], with pyserial's read and write semantics: a read waits up to
    /// [`READ_TIMEOUT`] for all the bytes asked for, a write may block up to [`WRITE_TIMEOUT`].
    pub struct SerialLink {
        pub port: Box<dyn SerialPort>,
    }

    impl Port for SerialLink {
        fn read(&mut self, n: usize) -> Result<Vec<u8>> {
            let mut buf = vec![0u8; n];
            let mut got = 0;
            let deadline = Instant::now() + READ_TIMEOUT;
            while got < n {
                let now = Instant::now();
                if now >= deadline {
                    break;
                }
                self.port.set_timeout(deadline - now).map_err(io_error)?;
                match self.port.read(&mut buf[got..]) {
                    Ok(0) => {
                        return Err(Error::Io(
                            "device reports readiness to read but returned no data (device disconnected or \
                             multiple access on port?)"
                                .into(),
                        ));
                    }
                    Ok(k) => got += k,
                    Err(e) if e.kind() == ErrorKind::TimedOut => break,
                    Err(e) if e.kind() == ErrorKind::Interrupted => {}
                    Err(e) => return Err(e.into()),
                }
            }
            buf.truncate(got);
            Ok(buf)
        }

        fn write(&mut self, data: &[u8]) -> Result<usize> {
            let deadline = Instant::now() + WRITE_TIMEOUT;
            let mut done = 0;
            while done < data.len() {
                let now = Instant::now();
                if now >= deadline {
                    return Err(Error::Io("Write timeout".into()));
                }
                self.port.set_timeout(deadline - now).map_err(io_error)?;
                match self.port.write(&data[done..]) {
                    Ok(k) => done += k,
                    Err(e) if matches!(e.kind(), ErrorKind::TimedOut | ErrorKind::Interrupted) => {}
                    Err(e) => return Err(e.into()),
                }
            }
            Ok(done)
        }

        fn flush(&mut self) -> Result<()> {
            Ok(self.port.flush()?)
        }

        fn in_waiting(&mut self) -> Result<usize> {
            Ok(self.port.bytes_to_read().map_err(io_error)? as usize)
        }

        fn reset_input_buffer(&mut self) -> Result<()> {
            self.port.clear(ClearBuffer::Input).map_err(io_error)
        }

        fn modem_lines(&mut self) -> ModemLines {
            ModemLines {
                cts: self.port.read_clear_to_send().ok(),
                dsr: self.port.read_data_set_ready().ok(),
                cd: self.port.read_carrier_detect().ok(),
                ri: self.port.read_ring_indicator().ok(),
            }
        }

        fn name(&self) -> Option<String> {
            self.port.name()
        }
    }

    /// The tty device with the line's framing (9600, 2 stop bits, no hardware or OS flow control), DTR and RTS
    /// asserted where the device has them.
    fn open_serial(path: &str, data_bits: DataBits, parity: Parity) -> Result<SerialLink> {
        let mut port = serialport::new(path, BAUD_RATE)
            .data_bits(data_bits)
            .parity(parity)
            .stop_bits(StopBits::Two)
            .flow_control(FlowControl::None)
            .timeout(READ_TIMEOUT)
            .exclusive(false) // as pyserial: no TIOCEXCL
            .open()
            .map_err(io_error)?;
        port.clear(ClearBuffer::Input).map_err(io_error)?; // pyserial's open() resets the input buffer
        // ptys and some devices have no modem lines
        let _ = port.write_data_terminal_ready(true);
        let _ = port.write_request_to_send(true);
        Ok(SerialLink { port })
    }

    /// A serial port (or, for bt:// addresses, an RFCOMM socket), with the line's 7E2 framing; soft_parity: 8-bit
    /// framing with the parity made here (adapters without 7-bit framing).
    pub fn open_port(path: &str, soft_parity: bool) -> Result<Box<dyn Port>> {
        if path.starts_with(BT_PREFIX) {
            let port = rfcomm_port(path)?;
            return Ok(if soft_parity { Box::new(SoftParityPort::new(port)) } else { port });
        }
        if soft_parity {
            return Ok(Box::new(SoftParityPort::new(open_serial(path, DataBits::Eight, Parity::None)?)));
        }
        Ok(Box::new(open_serial(path, DataBits::Seven, Parity::Even)?))
    }

    #[cfg(target_os = "linux")]
    fn rfcomm_port(path: &str) -> Result<Box<dyn Port>> {
        Ok(Box::new(RfcommPort::open(path)?))
    }

    #[cfg(not(target_os = "linux"))]
    fn rfcomm_port(_path: &str) -> Result<Box<dyn Port>> {
        Err(Error::Io("Bluetooth RFCOMM sockets are only supported on Linux".into()))
    }

    /// A Bluetooth serial adapter reached over an RFCOMM socket, with the parts of pyserial's interface the
    /// transfers use. The line itself (9600, 7E2) is set in the adapter: RFCOMM carries bytes, not the PC's port
    /// settings, and has no modem lines (shown as unknown).
    #[cfg(target_os = "linux")]
    pub struct RfcommPort {
        pub port: String,
        pub address: String,
        pub channel: u8,
        sock: socket2::Socket,
    }

    #[cfg(target_os = "linux")]
    impl RfcommPort {
        const AF_BLUETOOTH: u16 = 31;
        const BTPROTO_RFCOMM: i32 = 3;
        /// sizeof(struct sockaddr_rc): family, bdaddr (6 bytes, least significant first), channel, padding.
        const SOCKADDR_RC_LEN: u32 = 10;

        /// Connect to the adapter (10 s to connect, reads wait [`READ_TIMEOUT`]).
        pub fn open(url: &str) -> Result<Self> {
            Self::open_with(url, READ_TIMEOUT, Duration::from_secs(10))
        }

        pub fn open_with(url: &str, timeout: Duration, connect_timeout: Duration) -> Result<Self> {
            let (address, channel) = Self::parse(url)?;
            let mut mac = [0u8; 6];
            let parts: Vec<&str> = address.split(':').collect();
            if parts.len() != 6 {
                return Err(Error::Io("bad bluetooth address".into()));
            }
            for (slot, part) in mac.iter_mut().rev().zip(&parts) {
                *slot = u8::from_str_radix(part, 16).map_err(|_| Error::Io("bad bluetooth address".into()))?;
            }
            let mut storage = socket2::SockAddrStorage::zeroed();
            // SAFETY: the storage is a zeroed sockaddr_storage, larger than these 10 plain bytes.
            let raw = unsafe { storage.view_as::<[u8; 10]>() };
            raw[..2].copy_from_slice(&Self::AF_BLUETOOTH.to_ne_bytes());
            raw[2..8].copy_from_slice(&mac);
            raw[8] = channel;
            // SAFETY: the bytes above are a complete struct sockaddr_rc.
            let addr = unsafe { socket2::SockAddr::new(storage, Self::SOCKADDR_RC_LEN) };
            let sock = socket2::Socket::new(
                socket2::Domain::from(Self::AF_BLUETOOTH as i32),
                socket2::Type::STREAM,
                Some(socket2::Protocol::from(Self::BTPROTO_RFCOMM)),
            )?;
            sock.connect_timeout(&addr, connect_timeout)?;
            Self::with_socket_timeout(url, sock, timeout)
        }

        /// Over an already-connected socket (tests use a socket pair).
        pub fn with_socket(url: &str, sock: socket2::Socket) -> Result<Self> {
            Self::with_socket_timeout(url, sock, READ_TIMEOUT)
        }

        fn with_socket_timeout(url: &str, sock: socket2::Socket, timeout: Duration) -> Result<Self> {
            let (address, channel) = Self::parse(url)?;
            sock.set_read_timeout(Some(timeout))?;
            sock.set_write_timeout(Some(timeout))?;
            Ok(Self { port: url.to_string(), address, channel, sock })
        }

        /// (MAC in capitals, channel): SPP adapters listen on channel 1.
        fn parse(url: &str) -> Result<(String, u8)> {
            let addr = url.strip_prefix(BT_PREFIX).unwrap_or(url);
            let (mac, ch) = addr.split_once('/').unwrap_or((addr, ""));
            let channel = if ch.is_empty() {
                1
            } else {
                ch.parse()
                    .map_err(|_| Error::Value(format!("invalid literal for int() with base 10: '{ch}'")))?
            };
            Ok((mac.to_uppercase(), channel))
        }
    }

    #[cfg(target_os = "linux")]
    impl Port for RfcommPort {
        fn read(&mut self, n: usize) -> Result<Vec<u8>> {
            let mut buf = vec![0u8; n];
            match (&self.sock).read(&mut buf) {
                Ok(k) => {
                    buf.truncate(k);
                    Ok(buf)
                }
                Err(e) if matches!(e.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut) => Ok(Vec::new()),
                Err(e) => Err(e.into()),
            }
        }

        fn write(&mut self, data: &[u8]) -> Result<usize> {
            (&self.sock).write_all(data)?;
            Ok(data.len())
        }

        /// The socket's queue, peeked without blocking (Python's RfcommPort has no in_waiting).
        fn in_waiting(&mut self) -> Result<usize> {
            let mut buf = [std::mem::MaybeUninit::<u8>::uninit(); 4096];
            self.sock.set_nonblocking(true)?;
            let peeked = self.sock.peek(&mut buf);
            self.sock.set_nonblocking(false)?;
            match peeked {
                Ok(k) => Ok(k),
                Err(e) if e.kind() == ErrorKind::WouldBlock => Ok(0),
                Err(e) => Err(e.into()),
            }
        }

        fn reset_input_buffer(&mut self) -> Result<()> {
            self.sock.set_nonblocking(true)?;
            let mut buf = [0u8; 4096];
            while let Ok(k) = (&self.sock).read(&mut buf) {
                if k == 0 {
                    break;
                }
            }
            self.sock.set_nonblocking(false)?;
            Ok(())
        }

        fn name(&self) -> Option<String> {
            Some(self.port.clone())
        }
    }

    /// (device, description) for the serial ports present, USB adapters first.
    pub fn list_ports() -> Vec<(String, String)> {
        // end-to-end tests: only virtual ports (socat ptys) standing in for the lathe, never a real adapter
        // (tests/e2e)
        let test: Vec<String> = std::env::var("ALPHALINK_TEST_PORTS")
            .unwrap_or_default()
            .split(':')
            .filter(|d| !d.is_empty())
            .map(String::from)
            .collect();
        if !test.is_empty() {
            return test.into_iter().map(|d| (d, "test port".to_string())).collect();
        }
        // serialport scans /sys/class/tty without libudev, and panics where it is missing (sandboxes)
        if cfg!(target_os = "linux") && !std::path::Path::new("/sys/class/tty").is_dir() {
            return super::bluetooth_devices();
        }
        let mut ports: Vec<(String, String)> = serialport::available_ports()
            .unwrap_or_default()
            .into_iter()
            .map(|p| {
                let name = p.port_name.rsplit('/').next().unwrap_or(&p.port_name).to_string();
                let description = if p.port_name.contains("rfcomm") {
                    "Bluetooth (rfcomm)".to_string()
                } else if let serialport::SerialPortType::UsbPort(usb) = &p.port_type {
                    usb.product.clone().or_else(|| usb.manufacturer.clone()).unwrap_or(name)
                } else {
                    name
                };
                (p.port_name, description)
            })
            .collect();
        ports.sort_by_key(|p| (!p.0.starts_with("/dev/ttyUSB"), p.0.clone()));
        ports.extend(super::bluetooth_devices());
        ports
    }
}

/// Paired Bluetooth devices with a serial port profile (from BlueZ over D-Bus), as bt:// ports. Without the `bluez`
/// feature, or without BlueZ or a system bus, there are none.
pub fn bluetooth_devices() -> Vec<(String, String)> {
    #[cfg(feature = "bluez")]
    {
        bluez::devices().unwrap_or_default()
    }
    #[cfg(not(feature = "bluez"))]
    {
        Vec::new()
    }
}

#[cfg(feature = "bluez")]
mod bluez {
    use std::collections::HashMap;
    use std::time::Duration;

    use zbus::zvariant::OwnedValue;

    use super::{BT_PREFIX, SPP_UUID};

    fn string(dev: &HashMap<String, OwnedValue>, key: &str) -> Option<String> {
        dev.get(key).and_then(|v| String::try_from(v.try_clone().ok()?).ok())
    }

    pub(super) fn devices() -> zbus::Result<Vec<(String, String)>> {
        let conn = zbus::blocking::connection::Builder::system()?.method_timeout(Duration::from_secs(2)).build()?;
        let manager =
            zbus::blocking::fdo::ObjectManagerProxy::builder(&conn).destination("org.bluez")?.path("/")?.build()?;
        let mut out = Vec::new();
        for ifaces in manager.get_managed_objects()?.into_values() {
            let Some(dev) = ifaces.iter().find(|(name, _)| name.as_str() == "org.bluez.Device1").map(|(_, d)| d) else {
                continue;
            };
            let paired = dev.get("Paired").and_then(|v| bool::try_from(v).ok()).unwrap_or(false);
            if !paired {
                continue;
            }
            let uuids: Vec<String> = dev
                .get("UUIDs")
                .and_then(|v| Vec::<String>::try_from(v.try_clone().ok()?).ok())
                .unwrap_or_default()
                .into_iter()
                .map(|u| u.to_lowercase())
                .collect();
            if !uuids.is_empty() && !uuids.iter().any(|u| u == SPP_UUID) {
                continue; // a device that lists nothing may still be a serial adapter
            }
            let icon = string(dev, "Icon").unwrap_or_default();
            if ["audio", "phone", "input", "computer", "video"].iter().any(|p| icon.starts_with(p)) {
                continue; // earbuds and phones offer a serial profile too (for their apps), but are not adapters
            }
            let Some(address) = string(dev, "Address") else { continue };
            let name = [string(dev, "Alias"), string(dev, "Name")]
                .into_iter()
                .flatten()
                .find(|s| !s.is_empty())
                .unwrap_or_else(|| address.clone());
            out.push((format!("{BT_PREFIX}{address}"), format!("{name} (Bluetooth)")));
        }
        out.sort();
        Ok(out)
    }
}
