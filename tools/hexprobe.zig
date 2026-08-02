//! Report the port's f64 bit pattern for each literal on stdin.
//!
//! Exists so scripts/hexfloat_oracle.py can check the implementation against an
//! independent integer reference. Reads one literal per line, writes one
//! 16-digit hex bit pattern per line, in order.
//!
//! Deliberately thin: it calls the same `strtod.value` the C API uses, so what
//! is measured is the shipped conversion and not a reimplementation of it.

const std = @import("std");
const cli = @import("cli.zig");
const pdjson = @import("pdjson");

pub fn main(p: std.process.Init) !void {
    const app = try cli.App.init(p);

    const input = app.readStdin() catch app.die("cannot read stdin", .{});
    defer app.gpa.free(input);

    var buf: [1 << 16]u8 = undefined;
    var stdout = app.stdout(&buf);
    const out = stdout.w();

    // With --std, also report what std.fmt.parseFloat returns for the same
    // literal, so the oracle can judge the standard library independently of
    // this project's implementation.
    const also_std = std.mem.eql(u8, app.arg(0, ""), "--std");

    var it = std.mem.splitScalar(u8, input, '\n');
    while (it.next()) |raw| {
        const line = std.mem.trim(u8, raw, " \t\r");
        if (line.len == 0) continue;
        const bits: u64 = @bitCast(pdjson.strtod.value(line));
        if (also_std) {
            const parsed = std.fmt.parseFloat(f64, line) catch {
                try out.print("{x:0>16} error\n", .{bits});
                continue;
            };
            try out.print("{x:0>16} {x:0>16}\n", .{ bits, @as(u64, @bitCast(parsed)) });
        } else {
            try out.print("{x:0>16}\n", .{bits});
        }
    }
    try stdout.flush();
}
