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
}

pub type Result<T> = std::result::Result<T, Error>;

pub(crate) fn value<T>(msg: String) -> Result<T> {
    Err(Error::Value(msg))
}

pub(crate) fn not_implemented<T>(msg: String) -> Result<T> {
    Err(Error::NotImplemented(msg))
}
