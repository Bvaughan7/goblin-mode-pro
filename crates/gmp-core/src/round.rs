//! Rounding that matches Python's, because two implementations reporting
//! different numbers for the same input is a bug users can see.
//!
//! Python's `round` is half-to-EVEN, and it rounds the exact binary value
//! rather than a scaled copy of it. Both halves of that matter, and both have
//! already caught this port out:
//!
//! * `round(2.5)` is 2, where `f64::round` gives 3. Session percentiles index
//!   with `round(q * (n - 1))`, so six samples at the median hit it.
//! * `round(51.15, 1)` is 51.1, because 51.15 is really 51.1499999... as a
//!   double. Scaling by ten first pushes it over the halfway mark and gives
//!   51.2 instead.
//!
//! Formatting does both correctly: Rust's float formatting rounds the exact
//! value half-to-even, which is the same rule.

/// `round(x)` - to the nearest integer, halves to even.
pub(crate) fn half_even(x: f64) -> f64 {
    format!("{x:.0}").parse().unwrap_or(x)
}

/// `round(x, 1)` - to one decimal place, halves to even.
pub(crate) fn one_dp(x: f64) -> f64 {
    format!("{x:.1}").parse().unwrap_or(x)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn halves_go_to_even_not_away_from_zero() {
        assert_eq!(half_even(2.5), 2.0);
        assert_eq!(half_even(3.5), 4.0);
        assert_eq!(half_even(-2.5), -2.0);
    }

    #[test]
    fn one_decimal_rounds_the_exact_binary_value() {
        assert_eq!(one_dp(51.15), 51.1); // really 51.1499999...
        assert_eq!(one_dp(66.75), 66.8);
        assert_eq!(one_dp(55.25), 55.2);
    }
}
