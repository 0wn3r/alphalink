use thiserror::Error;

/// What the core reports to people (already translated through [`crate::i18n::tr`]).
#[derive(Debug, Clone, PartialEq, Error)]
pub enum Error {
    /// Bad input: a value out of range, a contour that does not connect, a file that is not what it claims.
    #[error("{0}")]
    Value(String),
    /// Something ALPHA-T does not do usably, or that no sample pins down yet.
    #[error("{0}")]
    NotImplemented(String),
    /// A serial transfer stopped by the user (Python's `serial_link.Cancelled`); a receive keeps what arrived.
    #[error("")]
    Cancelled(Vec<u8>),
    /// A serial transfer that timed out (Python's `serial_link.TransferTimeout`); what arrived before the line went
    /// quiet is never thrown away.
    #[error("{message}")]
    TransferTimeout { message: String, data: Vec<u8> },
    /// The port or a file failed (Python's OSError / SerialException), with the system's message.
    #[error("{0}")]
    Io(String),
}

impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        Error::Io(e.to_string())
    }
}

pub type Result<T> = std::result::Result<T, Error>;

pub(crate) fn value<T>(msg: String) -> Result<T> {
    Err(Error::Value(msg))
}

pub(crate) fn not_implemented<T>(msg: String) -> Result<T> {
    Err(Error::NotImplemented(msg))
}
