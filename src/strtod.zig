//! A `strtod`-compatible decimal conversion, implemented in Zig.
//!
//! `json_get_number()` is defined in the original as `strtod(json->data.string,
//! NULL)`, applied to whatever currently sits in the token buffer. That buffer
//! usually holds a well-formed JSON number, but the API lets a caller invoke it
//! after a JSON_STRING event too, so the full C grammar is observable: leading
//! whitespace, `inf`/`nan`, hex floats, and the "longest valid prefix, else
//! return 0" rule.
//!
//! Two reasons not to just call libc's `strtod`:
//!
//!  1. libc's `strtod` honours `LC_NUMERIC`. Under e.g. `sv_SE.UTF-8` it stops
//!     at the '.' and reads `123.45` as `123`. That is upstream issue #27,
//!     still open. Implementing the conversion here makes the port
//!     locale-independent by construction.
//!  2. It keeps the parser free of libc for the buffer and user-callback
//!     sources.
//!
//! The digits-to-double step delegates to `std.fmt.parseFloat`, which is
//! correctly rounded; this module owns only the grammar and the prefix rule.
//! `tests/differential` compares the result bit-for-bit against C `strtod`
//! over the whole corpus, and tests/port/number_torture.zig pins the edges.

const std = @import("std");

/// C locale `isspace`.
fn isSpace(c: u8) bool {
    return switch (c) {
        ' ', '\t', '\n', 0x0b, 0x0c, '\r' => true,
        else => false,
    };
}

fn isDigit(c: u8) bool {
    return c >= '0' and c <= '9';
}

fn isHexDigit(c: u8) bool {
    return isDigit(c) or (c | 0x20) >= 'a' and (c | 0x20) <= 'f';
}

fn eqlIgnoreCase(haystack: []const u8, needle: []const u8) bool {
    if (haystack.len < needle.len) return false;
    for (haystack[0..needle.len], needle) |a, b| {
        if (a | 0x20 != b | 0x20) return false;
    }
    return true;
}

pub const Result = struct {
    value: f64,
    /// Bytes consumed, i.e. what C would report through `endptr`. Zero means
    /// "no conversion could be performed" and `value` is 0.
    consumed: usize,
};

/// Convert the longest valid numeric prefix of `s`, following C's `strtod`.
pub fn strtod(s: []const u8) Result {
    var i: usize = 0;
    while (i < s.len and isSpace(s[i])) i += 1;

    var negative = false;
    if (i < s.len and (s[i] == '+' or s[i] == '-')) {
        negative = s[i] == '-';
        i += 1;
    }

    const rest = s[i..];

    if (eqlIgnoreCase(rest, "infinity")) {
        return .{ .value = if (negative) -std.math.inf(f64) else std.math.inf(f64), .consumed = i + 8 };
    }
    if (eqlIgnoreCase(rest, "inf")) {
        return .{ .value = if (negative) -std.math.inf(f64) else std.math.inf(f64), .consumed = i + 3 };
    }
    if (eqlIgnoreCase(rest, "nan")) {
        var n: usize = i + 3;
        var payload: u64 = 0;
        // Optional n-char-sequence: nan(alnum_and_underscore*)
        if (n < s.len and s[n] == '(') {
            var j = n + 1;
            while (j < s.len and (std.ascii.isAlphanumeric(s[j]) or s[j] == '_')) j += 1;
            if (j < s.len and s[j] == ')') {
                payload = nanPayload(s[n + 1 .. j]);
                n = j + 1;
            }
        }
        // A quiet NaN with the payload in the low mantissa bits, matching how
        // glibc and Apple libc interpret the n-char-sequence. The exact
        // encoding is implementation-defined (C99 7.20.1.3p4); see the note on
        // `nanPayload` for the one case this deliberately does not chase.
        const pattern: u64 = 0x7ff8000000000000 | (payload & 0x7ffffffffffff) |
            (@as(u64, @intFromBool(negative)) << 63);
        return .{ .value = @bitCast(pattern), .consumed = n };
    }

    if (eqlIgnoreCase(rest, "0x")) {
        if (scanHex(s, i)) |end| return finish(s, i, end, negative);
        // "0x" with no hex digits: the longest valid subject is just the "0".
        return finish(s, i, i + 1, negative);
    }

    if (scanDecimal(s, i)) |end| return finish(s, i, end, negative);
    return .{ .value = 0, .consumed = 0 };
}

/// Interpret the n-char-sequence of `nan(...)` the way glibc and Apple libc do:
/// as a base-0 integer (0x = hex, leading 0 = octal, otherwise decimal) placed
/// in the mantissa. A sequence that is not a valid integer yields 0, which is a
/// plain quiet NaN.
///
/// Sequences that overflow 64 bits are the one case not reproduced: libc
/// implementations disagree there, and C99 7.20.1.3p4 leaves the meaning of the
/// sequence implementation-defined, so there is no "correct" answer to match.
/// See DECISIONS.md D-09. It is unreachable from JSON number syntax; it can
/// only be observed by calling `json_get_number()` on a *string* token whose
/// text begins "nan(".
fn nanPayload(seq: []const u8) u64 {
    if (seq.len == 0) return 0;
    var base: u8 = 10;
    var i: usize = 0;
    if (seq.len >= 2 and seq[0] == '0' and (seq[1] | 0x20) == 'x') {
        base = 16;
        i = 2;
    } else if (seq[0] == '0') {
        base = 8;
        i = 1;
    }
    if (i >= seq.len) return 0;

    var acc: u64 = 0;
    while (i < seq.len) : (i += 1) {
        const d: u8 = switch (seq[i]) {
            '0'...'9' => seq[i] - '0',
            'a'...'f' => seq[i] - 'a' + 10,
            'A'...'F' => seq[i] - 'A' + 10,
            else => return 0,
        };
        if (d >= base) return 0;
        acc = std.math.mul(u64, acc, base) catch return 0;
        acc = std.math.add(u64, acc, d) catch return 0;
    }
    return acc;
}

/// Returns the end index of a valid hex-float body starting at `start`
/// (which points at "0x"), or null if there are no hex digits.
fn scanHex(s: []const u8, start: usize) ?usize {
    var i = start + 2;
    var digits: usize = 0;
    while (i < s.len and isHexDigit(s[i])) : (i += 1) digits += 1;
    if (i < s.len and s[i] == '.') {
        i += 1;
        while (i < s.len and isHexDigit(s[i])) : (i += 1) digits += 1;
    }
    if (digits == 0) return null;

    // A binary exponent is only part of the subject when it has digits.
    if (i < s.len and (s[i] | 0x20) == 'p') {
        var j = i + 1;
        if (j < s.len and (s[j] == '+' or s[j] == '-')) j += 1;
        if (j < s.len and isDigit(s[j])) {
            while (j < s.len and isDigit(s[j])) j += 1;
            i = j;
        }
    }
    return i;
}

/// Returns the end index of a valid decimal body starting at `start`, or null
/// if no digits are present.
fn scanDecimal(s: []const u8, start: usize) ?usize {
    var i = start;
    var digits: usize = 0;
    while (i < s.len and isDigit(s[i])) : (i += 1) digits += 1;
    if (i < s.len and s[i] == '.') {
        i += 1;
        while (i < s.len and isDigit(s[i])) : (i += 1) digits += 1;
    }
    if (digits == 0) return null;

    if (i < s.len and (s[i] | 0x20) == 'e') {
        var j = i + 1;
        if (j < s.len and (s[j] == '+' or s[j] == '-')) j += 1;
        if (j < s.len and isDigit(s[j])) {
            while (j < s.len and isDigit(s[j])) j += 1;
            i = j;
        }
    }
    return i;
}

fn finish(s: []const u8, body_start: usize, end: usize, negative: bool) Result {
    // Only characters this module already validated reach parseFloat, so the
    // only way it can fail is a body we would not have produced.
    const magnitude = std.fmt.parseFloat(f64, s[body_start..end]) catch 0;
    return .{ .value = if (negative) -magnitude else magnitude, .consumed = end };
}

/// The `json_get_number()` shape: value only, 0 when no conversion happened.
pub fn value(s: []const u8) f64 {
    return strtod(s).value;
}

const expectEqual = std.testing.expectEqual;

fn bits(v: f64) u64 {
    return @bitCast(v);
}

test "plain json numbers" {
    try expectEqual(@as(f64, 1024), value("1024"));
    try expectEqual(@as(f64, -1), value("-1"));
    try expectEqual(bits(-0.0), bits(value("-0")));
    try expectEqual(@as(f64, 0.1), value("0.1"));
    try expectEqual(@as(f64, 1e308), value("1e308"));
}

test "overflow and underflow follow strtod" {
    try expectEqual(std.math.inf(f64), value("1e999"));
    try expectEqual(-std.math.inf(f64), value("-1e999"));
    try expectEqual(@as(f64, 0), value("1e-999"));
    try expectEqual(bits(4.9406564584124654e-324), bits(value("4.9406564584124654e-324")));
}

test "no conversion yields zero" {
    try expectEqual(@as(f64, 0), value(""));
    try expectEqual(@as(f64, 0), value("hello"));
    try expectEqual(@as(f64, 0), value("."));
    try expectEqual(@as(f64, 0), value("+"));
    try expectEqual(@as(usize, 0), strtod("hello").consumed);
}

test "longest valid prefix" {
    try expectEqual(@as(f64, 1), value("1e"));
    try expectEqual(@as(usize, 1), strtod("1e").consumed);
    try expectEqual(@as(f64, 1), value("1e+"));
    try expectEqual(@as(f64, 12), value("12abc"));
    try expectEqual(@as(usize, 2), strtod("12abc").consumed);
    try expectEqual(@as(f64, 0), value("0x"));
    try expectEqual(@as(usize, 1), strtod("0x").consumed);
    try expectEqual(@as(f64, 5), value("5."));
    try expectEqual(@as(f64, 0.5), value(".5"));
}

test "leading whitespace is skipped like strtod" {
    try expectEqual(@as(f64, 42), value("  \t\n42"));
    try expectEqual(@as(usize, 6), strtod("  \t\n42").consumed);
}

test "inf, nan and hex floats" {
    try expectEqual(std.math.inf(f64), value("inf"));
    try expectEqual(std.math.inf(f64), value("INFINITY"));
    try expectEqual(@as(usize, 8), strtod("infinity").consumed);
    try expectEqual(@as(usize, 3), strtod("infinite").consumed);
    try std.testing.expect(std.math.isNan(value("nan")));
    try expectEqual(@as(usize, 8), strtod("nan(1_2)").consumed);
    try expectEqual(@as(f64, 8), value("0x1p3"));
}

test "locale independence: a comma never acts as a decimal separator" {
    try expectEqual(@as(f64, 123), value("123,45"));
    try expectEqual(@as(usize, 3), strtod("123,45").consumed);
}
