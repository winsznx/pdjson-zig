//! Byte-exact reconstruction of pdjson's diagnostic strings.
//!
//! The original builds every message with
//! `snprintf(json->errmsg, sizeof(json->errmsg), fmt, ...)`, and `errmsg` is
//! part of the public struct, so the bytes are observable. Rather than
//! interpret printf format strings at runtime, each call site here composes its
//! message from three primitives that reproduce the exact conversions the
//! original uses: `%s`, `%c`, and `%0Nlx`.
//!
//! Two details matter for equivalence and are easy to get wrong:
//!
//!  * `%c` takes an `int` and converts it to `unsigned char`. A byte of 0xE9
//!    lands in the message as the raw byte 0xE9, not as an escape or a
//!    replacement character.
//!  * `%c` with argument 0 writes an actual NUL. Since `json_get_error()`
//!    returns a `char *`, the message a caller sees is then truncated at that
//!    point. Input "\x00" really does yield the visible message
//!    "unexpected byte '". This module reproduces that rather than sanitising
//!    it away.

const std = @import("std");
const abi = @import("abi.zig");

/// A bounded writer over `json_stream.errmsg` with snprintf truncation
/// semantics: at most `len - 1` payload bytes, always NUL terminated.
pub const Builder = struct {
    buf: *[abi.errmsg_len]u8,
    fill: usize = 0,

    pub fn init(buf: *[abi.errmsg_len]u8) Builder {
        return .{ .buf = buf };
    }

    fn putByte(self: *Builder, b: u8) void {
        if (self.fill + 1 >= self.buf.len) return; // reserve the terminator
        self.buf[self.fill] = b;
        self.fill += 1;
    }

    /// printf `%s`.
    pub fn str(self: *Builder, s: []const u8) void {
        for (s) |b| self.putByte(b);
    }

    /// printf `%c`: the int argument is converted to `unsigned char`.
    pub fn char(self: *Builder, c: c_int) void {
        self.putByte(@truncate(@as(c_uint, @bitCast(c))));
    }

    /// printf `%0<width>lx`: lowercase hex, zero padded to at least `width`.
    pub fn hex(self: *Builder, value: c_long, width: usize) void {
        var digits: [2 * @sizeOf(c_long)]u8 = undefined;
        const unsigned: c_ulong = @bitCast(value);
        var n: usize = 0;
        var v = unsigned;
        if (v == 0) {
            digits[0] = '0';
            n = 1;
        } else {
            while (v != 0) : (v /= 16) {
                digits[n] = "0123456789abcdef"[@intCast(v % 16)];
                n += 1;
            }
        }
        var pad = width;
        while (pad > n) : (pad -= 1) self.putByte('0');
        while (n > 0) {
            n -= 1;
            self.putByte(digits[n]);
        }
    }

    pub fn finish(self: *Builder) void {
        self.buf[self.fill] = 0;
    }
};

test "printf %c converts through unsigned char" {
    var buf: [abi.errmsg_len]u8 = undefined;
    var b = Builder.init(&buf);
    b.str("byte '");
    b.char(0xE9);
    b.str("'");
    b.finish();
    try std.testing.expectEqualSlices(u8, "byte '\xE9'", buf[0..b.fill]);
}

test "printf %c with 0 embeds a NUL that truncates the visible message" {
    var buf: [abi.errmsg_len]u8 = undefined;
    var b = Builder.init(&buf);
    b.str("unexpected byte '");
    b.char(0);
    b.str("' in value");
    b.finish();
    // The full byte string is written...
    try std.testing.expectEqual(@as(usize, 28), b.fill);
    // ...but C string semantics stop at the NUL.
    try std.testing.expectEqualSlices(u8, "unexpected byte '", std.mem.sliceTo(&buf, 0));
}

test "printf %0Nlx zero pads" {
    var buf: [abi.errmsg_len]u8 = undefined;
    var b = Builder.init(&buf);
    b.hex(0xd800, 4);
    b.str(" ");
    b.hex(0x1f, 6);
    b.str(" ");
    b.hex(0x10ffff, 6);
    b.finish();
    try std.testing.expectEqualSlices(u8, "d800 00001f 10ffff", buf[0..b.fill]);
}

test "snprintf truncation keeps the buffer NUL terminated" {
    var buf: [abi.errmsg_len]u8 = undefined;
    var b = Builder.init(&buf);
    for (0..500) |_| b.str("x");
    b.finish();
    try std.testing.expectEqual(@as(usize, abi.errmsg_len - 1), b.fill);
    try std.testing.expectEqual(@as(u8, 0), buf[abi.errmsg_len - 1]);
}
