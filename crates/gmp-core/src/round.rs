//! Float arithmetic that matches CPython's, because two implementations
//! reporting different numbers for the same input is a bug users can see.
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

/// `sum(values)` for floats - which is NOT a fold.
///
/// CPython's `sum` stopped being a plain left-to-right addition in 3.12: over
/// floats it now carries a compensation term (the improved Kahan-Babuska
/// algorithm, due to Neumaier) and returns a result far closer to the exact
/// one than any single pass can be. `iter().sum::<f64>()` is the fold, so the
/// two disagree by an ulp or two on any set of samples large enough to lose
/// bits - and a session average passes through `round(x, 1)`, which turns
/// that ulp into a different frame rate whenever the mean lands near a
/// boundary. Found exactly that way: 40 frame rates whose exact mean is
/// 55.15, reported as 55.1 by Python and 55.2 by the fold.
///
/// The compensation is what makes this order-INSENSITIVE in practice, which
/// is worth knowing: two callers summing the same samples in different orders
/// will usually now agree, where the fold would not.
pub(crate) fn py_sum(values: &[f64]) -> f64 {
    let mut total = 0.0f64;
    let mut compensation = 0.0f64;
    for &x in values {
        let t = total + x;
        // Whichever addend is larger keeps its bits; the other's lost low bits
        // are what the compensation accumulates.
        if total.abs() >= x.abs() {
            compensation += (total - t) + x;
        } else {
            compensation += (x - t) + total;
        }
        total = t;
    }
    total + compensation
}

/// `round(x, 2)` - to two decimal places, halves to even.
///
/// The session summary's frame-time figures are the only two-place numbers in
/// the project, and one of them - a stutter percentage of `100 * n / total` -
/// lands on an exact half often enough to matter: 7 frames in 32 is 21.875,
/// which is representable exactly, so the tie is real rather than an artefact.
pub(crate) fn two_dp(x: f64) -> f64 {
    format!("{x:.2}").parse().unwrap_or(x)
}

/// `str(x)` for a float - which is not what Rust's `{}` prints.
///
/// The two disagree on the very first case anybody hits: Python renders `5.0`
/// as `"5.0"` and Rust as `"5"`. They also disagree on when to switch to
/// exponent notation, and on how to write the exponent when they do.
///
/// CPython's rule, from `float_repr_style` short mode: take the shortest digit
/// string that round-trips, then use exponent notation when the decimal point
/// would sit at or left of position -4, or right of position 16. Rust's `{:e}`
/// already produces the same shortest digits, so this reads them back off it
/// and re-places the point.
pub(crate) fn py_str(x: f64) -> String {
    if x.is_nan() {
        return "nan".to_string();
    }
    if x.is_infinite() {
        return if x < 0.0 { "-inf" } else { "inf" }.to_string();
    }

    let scientific = format!("{x:e}"); // e.g. "-1.25e-7", always d[.ddd]e<exp>
    let (mantissa, exponent) = scientific.split_once('e').expect("{:e} yields an exponent");
    let exponent: i32 = exponent.parse().expect("{:e} yields an integer exponent");

    let negative = mantissa.starts_with('-');
    let digits: String = mantissa.chars().filter(|c| c.is_ascii_digit()).collect();
    // Where the decimal point falls relative to the start of `digits`. The
    // mantissa always has exactly one digit before its point, so this is the
    // exponent plus one.
    let point = exponent + 1;

    let body = if point <= -4 || point > 16 {
        let mantissa = mantissa.trim_start_matches('-');
        // Python always writes the sign and pads the exponent to two digits.
        let sign = if exponent < 0 { '-' } else { '+' };
        format!("{mantissa}e{sign}{:02}", exponent.abs())
    } else if point <= 0 {
        format!("0.{}{}", "0".repeat(-point as usize), digits)
    } else if (point as usize) < digits.len() {
        let (whole, fraction) = digits.split_at(point as usize);
        format!("{whole}.{fraction}")
    } else {
        let padding = "0".repeat(point as usize - digits.len());
        format!("{digits}{padding}.0")
    };

    if negative {
        format!("-{body}")
    } else {
        body
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Every expected value here is what CPython 3.14 prints for `sum(...)`
    /// of the same list. The fold's answer is given alongside, because on
    /// three of these it is not close.
    #[test]
    fn py_sum_matches_cpython_where_a_fold_does_not() {
        let cases: &[(&str, &[f64], f64, f64)] = &[
            // (name, samples, CPython sum, the fold's answer)
            (
                "a tiny running total, then values that swamp it",
                &[1e-8, 1e8, 1.0, -1e8, 1e-8],
                1.00000002,
                1.0000000249011611,
            ),
            // The compensation term is the whole answer here: the fold loses
            // both 1.0s into 1e100 and gets zero.
            (
                "addends a hundred orders of magnitude apart",
                &[1.0, 1e100, 1.0, -1e100],
                2.0,
                0.0,
            ),
            (
                "largest first, which is the fold's worst order",
                &[1e6, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
                1000001.0,
                1000000.9999999998,
            ),
            (
                "a frame-rate window, where the two agree",
                &[59.9, 60.1, 60.0, 59.95],
                239.95,
                239.95,
            ),
        ];
        for (name, samples, cpython, folded) in cases {
            let fold: f64 = samples.iter().sum();
            assert_eq!(
                py_sum(samples).to_bits(),
                cpython.to_bits(),
                "{name}: py_sum gave {}, CPython gives {cpython}",
                py_sum(samples)
            );
            assert_eq!(fold.to_bits(), folded.to_bits(), "{name}: the fold moved");
        }
    }

    #[test]
    fn py_sum_of_nothing_is_zero() {
        assert_eq!(py_sum(&[]), 0.0);
    }

    #[test]
    fn an_integral_float_keeps_its_point() {
        assert_eq!(py_str(5.0), "5.0");
        assert_eq!(py_str(0.0), "0.0");
        assert_eq!(py_str(-0.0), "-0.0");
        assert_eq!(py_str(-5.0), "-5.0");
    }

    #[test]
    fn the_switch_to_exponent_notation_is_where_python_puts_it() {
        assert_eq!(py_str(1e15), "1000000000000000.0");
        assert_eq!(py_str(1e16), "1e+16");
        assert_eq!(py_str(0.0001), "0.0001");
        assert_eq!(py_str(0.00001), "1e-05");
    }

    #[test]
    fn an_exponent_is_signed_and_two_digits_wide() {
        assert_eq!(py_str(1e-5), "1e-05");
        assert_eq!(py_str(1.5e20), "1.5e+20");
        assert_eq!(py_str(1e100), "1e+100");
    }

    #[test]
    fn ordinary_numbers_read_normally() {
        assert_eq!(py_str(1.5), "1.5");
        assert_eq!(py_str(123.456), "123.456");
        assert_eq!(py_str(0.1), "0.1");
        assert_eq!(py_str(-2.75), "-2.75");
    }

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
