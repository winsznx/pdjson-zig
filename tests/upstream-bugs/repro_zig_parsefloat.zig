//! Standalone reproducer: `std.fmt.parseFloat` truncates instead of rounding
//! hex floats whose significand needs more than 53 bits.
//!
//!     zig run tests/upstream-bugs/repro_zig_parsefloat.zig
//!
//! Uses only the standard library, so it can be handed to the Zig project
//! without any of this repository.
//!
//! Reproduces identically on Zig 0.16.0 and 0.17.0-dev.1516+8a4b5424d.
//!
//! The expected values are not taken from any implementation. They are derived
//! from the IEEE-754 definition and confirmed two independent ways: by libc
//! `strtod`, and by the exact-integer reference in
//! scripts/hexfloat_oracle.py, which decides the rounding by comparing
//! arbitrary-precision integers with no floating point in the decision path.
//!
//! Worked example for the first case. `0xfffffffffffffffffp0` is 2^68 - 1.
//! binary64 has a 53-bit significand, so around 2^68 the representable
//! neighbours are 2^68 - 2^15 and 2^68. The value sits 2^15 - 1 above the lower
//! neighbour and 1 below the upper, so the nearest is 2^68 exactly.

const std = @import("std");

const Case = struct {
    literal: []const u8,
    expect: u64,
    why: []const u8,
};

const cases = [_]Case{
    .{
        .literal = "0xfffffffffffffffffp0",
        .expect = 0x4430000000000000,
        .why = "2^68 - 1; nearest binary64 is 2^68",
    },
    .{
        .literal = "0x123456789abcdef01p0",
        .expect = 0x43f23456789abcdf,
        .why = "68-bit significand; discarded bits are above half",
    },
    .{
        .literal = "0x634922337286237e3p0",
        .expect = 0x4418d2488cdca189,
        .why = "the case that surfaced this, found by differential fuzzing",
    },
    // Controls: these are handled correctly, and are here so a run that
    // reported nothing could not be mistaken for the bug being absent.
    .{
        .literal = "0x1.fffffffffffff8p0",
        .expect = 0x4000000000000000,
        .why = "control: ties-to-even carry out of the significand",
    },
    .{
        .literal = "0x1p-1074",
        .expect = 0x0000000000000001,
        .why = "control: smallest subnormal",
    },
    .{
        .literal = "0x1fffffffffffffffp0",
        .expect = 0x43c0000000000000,
        .why = "control: 61-bit significand, rounds up correctly",
    },
};

pub fn main() void {
    var mismatches: usize = 0;

    std.debug.print("zig {s}\n\n", .{@import("builtin").zig_version_string});
    std.debug.print("{s:<24} {s:<18} {s:<18} {s}\n",
        .{ "literal", "parseFloat", "expected", "" });

    for (cases) |c| {
        const v = std.fmt.parseFloat(f64, c.literal) catch {
            std.debug.print("{s:<24} parse error\n", .{c.literal});
            mismatches += 1;
            continue;
        };
        const got: u64 = @bitCast(v);
        const ok = got == c.expect;
        if (!ok) mismatches += 1;
        std.debug.print("{s:<24} {x:0>16}   {x:0>16}   {s}  ({s})\n", .{
            c.literal, got, c.expect, if (ok) "ok      " else "MISMATCH", c.why,
        });
    }

    std.debug.print("\n{d} of {d} cases mismatch\n", .{ mismatches, cases.len });
    if (mismatches > 0) {
        std.debug.print(
            "\nAll mismatches have significands needing more than 53 bits, i.e. the\n" ++
                "fallback path taken when the mantissa exceeds the fast-path digit limit.\n" ++
                "The discarded bits appear to be truncated rather than rounded.\n",
            .{},
        );
    }
}
