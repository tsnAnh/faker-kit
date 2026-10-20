# Rust patterns

Examples for each rule in `SKILL.md`. The snippet compiles as one crate with `thiserror` as its only dependency.

```rust
use std::fmt;

/// An order id, validated when it is created.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct OrderId(String);

impl TryFrom<&str> for OrderId {
    type Error = ParseError;

    fn try_from(raw: &str) -> Result<Self, Self::Error> {
        if raw.starts_with("ord_") && raw.len() == 16 {
            Ok(Self(raw.to_owned()))
        } else {
            Err(ParseError::BadOrderId(raw.to_owned()))
        }
    }
}

impl fmt::Display for OrderId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// Errors a caller is expected to handle.
#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum ParseError {
    #[error("{0} is not an order id")]
    BadOrderId(String),
}

/// Only real states exist: no `loading: bool` next to `Option<Vec<Item>>`.
#[derive(Debug, PartialEq, Eq)]
pub enum CartState {
    Loading,
    Ready(Items),
    Failed(Failure),
}

#[derive(Debug, PartialEq, Eq)]
pub enum Failure {
    Network,
    SignedOut,
}

/// A non-empty list: the empty case cannot be constructed.
#[derive(Debug, PartialEq, Eq)]
pub struct Items {
    first: String,
    rest: Vec<String>,
}

impl Items {
    #[must_use]
    pub fn new(first: String, rest: Vec<String>) -> Self {
        Self { first, rest }
    }

    #[must_use]
    pub fn count(&self) -> usize {
        1 + self.rest.len()
    }

    #[must_use]
    pub fn first(&self) -> &str {
        &self.first
    }
}

/// No `_ =>` arm: a new `CartState` variant fails to compile here.
#[must_use]
pub fn title(state: &CartState) -> String {
    match state {
        CartState::Loading => "Loading".to_owned(),
        CartState::Ready(items) => format!("Cart ({})", items.count()),
        CartState::Failed(Failure::Network | Failure::SignedOut) => "Cart".to_owned(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_valid_ids_and_rejects_others() {
        assert_eq!(OrderId::try_from("ord_0123456789ab").unwrap().to_string(), "ord_0123456789ab");
        assert_eq!(OrderId::try_from("42"), Err(ParseError::BadOrderId("42".to_owned())));
    }

    #[test]
    fn titles_follow_the_state() {
        let items = Items::new("tea".to_owned(), vec!["cup".to_owned()]);
        assert_eq!(items.first(), "tea");
        assert_eq!(title(&CartState::Ready(items)), "Cart (2)");
        assert_eq!(title(&CartState::Loading), "Loading");
    }
}
```
