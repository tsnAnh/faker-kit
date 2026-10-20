use std::num::ParseIntError;

#[derive(Debug, PartialEq, Eq)]
pub enum PortError {
    Missing,
    Invalid(ParseIntError),
}

/// Reads the listening port from its raw configuration value.
///
/// # Errors
///
/// Returns [`PortError::Missing`] when no value is set and [`PortError::Invalid`] when it is not a port number.
pub fn port(raw: Option<&str>) -> Result<u16, PortError> {
    raw.ok_or(PortError::Missing)?.parse().map_err(PortError::Invalid)
}

#[must_use]
pub fn retry_policy() -> u32 {
    3
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_the_port() {
        assert_eq!(port(Some("8080")).unwrap(), 8080);
        assert_eq!(port(None), Err(PortError::Missing));
    }
}
