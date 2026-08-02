//! Pins `json_get_number` against C's `strtod` bit for bit.
//!
//! The port implements its own `strtod` (src/strtod.zig) rather than calling
//! libc's, to be locale-independent -- upstream issue #27 is that libc's
//! honours LC_NUMERIC and misreads "123.45" under e.g. sv_SE. Owning the
//! conversion means owning its correctness, so this test uses libc `strtod` as
//! an oracle over a wide set of lexemes and compares the raw f64 bit patterns.
//!
//! Bit comparison rather than `==` is deliberate: it distinguishes -0.0 from
//! 0.0 and does not silently pass NaN against NaN.

const std = @import("std");
const pdjson = @import("pdjson");

extern "c" fn strtod(nptr: [*:0]const u8, endptr: ?*?[*:0]const u8) f64;

fn bits(v: f64) u64 {
    return @bitCast(v);
}

fn expectMatchesC(text: [:0]const u8) !void {
    const mine = pdjson.strtod.strtod(text);
    var end: ?[*:0]const u8 = null;
    const theirs = strtod(text.ptr, &end);
    const consumed = @intFromPtr(end.?) - @intFromPtr(text.ptr);

    if (bits(mine.value) != bits(theirs) or mine.consumed != consumed) {
        std.debug.print(
            "strtod mismatch for \"{s}\":\n  zig  value=0x{x:0>16} consumed={d}\n  libc value=0x{x:0>16} consumed={d}\n",
            .{ text, bits(mine.value), mine.consumed, bits(theirs), consumed },
        );
        return error.StrtodMismatch;
    }
}

test "fixed lexemes match libc strtod exactly" {
    const cases = [_][:0]const u8{
        "0",                              "-0",                               "1",
        "-1",                             "1024",                             "0.1",
        "0.5",                            "1.5",                              "-1.5",
        "1e3",                            "1E3",                              "1e+3",
        "1e-3",                           "1e308",                            "1e309",
        "1e-308",                         "1e-324",                           "1e-999",
        "1e999",                          "-1e999",                           "5.",
        ".5",                             ".",                                "-",
        "+1",                             "",                                 "x",
        "4.9406564584124654e-324",        "2.2250738585072011e-308",          "1.7976931348623157e308",
        "1.7976931348623159e308",         "9223372036854775807",              "18446744073709551616",
        "123456789012345678901234567890", "0.000000000000000000000000000001", "1e",
        "1e+",                            "1e-",                              "0x",
        "0x1p3",                          "0X1P-3",                           "0x1.8p1",
        "inf",                            "INF",                              "infinity",
        "Infinity",                       "infinit",                          "nan",
        "NAN",                            "nan(123)",                         "nan()",
        "nan(0x10)",                      "nan(010)",                         "nan(abc)",
        "nan(zz)",                        "-nan(5)",                          "  \t42",
        "42abc",                          "--1",                              "00",
        "007",                            "1.2.3",                            "1,5",
    };
    for (cases) |c| try expectMatchesC(c);
}

test "long digit strings match libc strtod" {
    var buf: [600]u8 = undefined;
    for ([_]usize{ 17, 18, 19, 20, 40, 100, 300, 500 }) |n| {
        buf[0] = '1';
        @memset(buf[1 .. n + 1], '0');
        buf[n + 1] = 0;
        try expectMatchesC(buf[0 .. n + 1 :0]);

        buf[0] = '0';
        buf[1] = '.';
        @memset(buf[2 .. n + 2], '3');
        buf[n + 2] = 0;
        try expectMatchesC(buf[0 .. n + 2 :0]);
    }
}

test "exponent sweep across the whole double range" {
    var buf: [64]u8 = undefined;
    var e: i32 = -340;
    while (e <= 320) : (e += 1) {
        const s = try std.fmt.bufPrintZ(&buf, "1e{d}", .{e});
        try expectMatchesC(s);
        const s2 = try std.fmt.bufPrintZ(&buf, "9.999999999999999e{d}", .{e});
        try expectMatchesC(s2);
    }
}

test "powers of two and their neighbours" {
    var buf: [64]u8 = undefined;
    var p: u32 = 0;
    while (p < 63) : (p += 1) {
        const v = @as(u64, 1) << @intCast(p);
        for ([_]i64{ -1, 0, 1 }) |d| {
            const s = try std.fmt.bufPrintZ(&buf, "{d}", .{@as(i64, @intCast(v)) + d});
            try expectMatchesC(s);
        }
    }
}

test "randomised lexemes match libc strtod" {
    var prng = std.Random.DefaultPrng.init(0xBEEF);
    const rand = prng.random();
    const alphabet = "0123456789.eE+-";
    var buf: [40]u8 = undefined;

    for (0..20000) |_| {
        const n = rand.intRangeAtMost(usize, 1, buf.len - 1);
        for (buf[0..n]) |*b| b.* = alphabet[rand.uintLessThan(usize, alphabet.len)];
        buf[n] = 0;
        try expectMatchesC(buf[0..n :0]);
    }
}

test "nan payloads that overflow 64 bits are implementation-defined" {
    // C99 7.20.1.3p4 leaves the meaning of the n-char-sequence
    // implementation-defined, and libcs disagree once it overflows. The port
    // returns a plain quiet NaN there rather than guessing. Documented in
    // DECISIONS.md D-09; unreachable from JSON number syntax.
    const r = pdjson.strtod.strtod("nan(99999999999999999999)");
    try std.testing.expect(std.math.isNan(r.value));
    try std.testing.expectEqual(@as(usize, 25), r.consumed);
}

test "json_get_number over parsed number tokens" {
    const cases = [_]struct { []const u8, f64 }{
        .{ "1024", 1024 },
        .{ "-1", -1 },
        .{ "0.5", 0.5 },
        .{ "1e3", 1000 },
        .{ "-0", -0.0 },
        .{ "1e999", std.math.inf(f64) },
    };
    for (cases) |c| {
        var p = pdjson.Parser.initBuffer(c[0]);
        defer p.deinit();
        try std.testing.expectEqual(pdjson.Event.number, try p.next());
        try std.testing.expectEqual(bits(c[1]), bits(p.number()));
    }
}

test "json_get_number on a string token follows strtod prefix rules" {
    // The API allows it, so the behaviour is observable and must be pinned.
    var p = pdjson.Parser.initBuffer("\"  12.5abc\"");
    defer p.deinit();
    try std.testing.expectEqual(pdjson.Event.string, try p.next());
    try std.testing.expectEqual(@as(f64, 12.5), p.number());
}
