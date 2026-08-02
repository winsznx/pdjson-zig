//! Benchmark harness for the Zig library.
//!
//! Deliberately mirrors oracle/bench_c.c line for line in structure: same
//! workload files, same parse loop, same counting allocator, same warm-up
//! policy, same output shape. If the two harnesses differed in how much work
//! they do, the numbers would be meaningless.

const std = @import("std");
const cli = @import("cli.zig");
const pdjson = @import("pdjson");
const abi = pdjson.abi;
const core = pdjson.parser;

var alloc_count: u64 = 0;
var alloc_bytes: u64 = 0;

extern "c" fn malloc(size: usize) callconv(.c) ?*anyopaque;
extern "c" fn realloc(ptr: ?*anyopaque, size: usize) callconv(.c) ?*anyopaque;
extern "c" fn free(ptr: ?*anyopaque) callconv(.c) void;

fn bMalloc(n: usize) callconv(.c) ?*anyopaque {
    alloc_count += 1;
    alloc_bytes += n;
    return malloc(n);
}

fn bRealloc(p: ?*anyopaque, n: usize) callconv(.c) ?*anyopaque {
    alloc_count += 1;
    alloc_bytes += n;
    return realloc(p, n);
}

fn bFree(p: ?*anyopaque) callconv(.c) void {
    free(p);
}

/// The same clock source the C harness uses (CLOCK_MONOTONIC via libc), so the
/// two sets of samples are directly comparable rather than merely similar.
fn nowNs() i128 {
    var ts: std.c.timespec = undefined;
    _ = std.c.clock_gettime(.MONOTONIC, &ts);
    return @as(i128, ts.sec) * std.time.ns_per_s + ts.nsec;
}

fn peakRssKb() u64 {
    const ru = std.posix.getrusage(std.posix.rusage.SELF);
    const raw: u64 = @intCast(ru.maxrss);
    // maxrss is bytes on Darwin, kilobytes elsewhere -- same convention as the
    // C harness so the two numbers are comparable.
    return if (@import("builtin").os.tag == .macos) raw / 1024 else raw;
}

fn runOnce(buf: []const u8, fetch_strings: bool) u64 {
    var s: abi.Stream = undefined;
    core.openBuffer(&s, buf.ptr, buf.len);
    core.setAllocator(&s, &.{ .malloc = bMalloc, .realloc = bRealloc, .free = bFree });
    core.setStreaming(&s, true);

    var events: u64 = 0;
    var first = true;
    while (true) {
        const t = core.nextEvent(&s);
        events += 1;
        if (fetch_strings and (t == .string or t == .number)) {
            var n: usize = 0;
            _ = core.getStringPtr(&s, &n);
            const tok = core.getStringSlice(&s);
            events += @intFromBool(tok.len > 0 and tok[0] != 0);
            events += n;
        }
        if (t == .err) break;
        if (t == .done) {
            if (first) break;
            core.reset(&s);
            first = true;
        } else {
            first = false;
        }
    }
    core.close(&s);
    return events;
}

pub fn main(p: std.process.Init) !void {
    const app = try cli.App.init(p);

    const path = app.argOpt(0) orelse app.die("usage: bench_zig <workload> <iterations> [parse|strings]", .{});
    const iterations = std.fmt.parseInt(usize, app.arg(1, "100"), 10) catch
        app.die("bad iteration count", .{});
    const fetch_strings = std.mem.eql(u8, app.arg(2, "parse"), "strings");
    // Parses per recorded sample; see the note in oracle/bench_c.c.
    const inner = @max(1, std.fmt.parseInt(usize, app.arg(3, "1"), 10) catch 1);

    const buf = app.readFile(path, 1 << 30) catch app.die("cannot open {s}", .{path});
    defer app.gpa.free(buf);

    const cold_start = nowNs();
    var sink = runOnce(buf, fetch_strings);
    const cold_ns = nowNs() - cold_start;
    for (0..4) |_| sink += runOnce(buf, fetch_strings);

    alloc_count = 0;
    alloc_bytes = 0;

    const samples = try app.gpa.alloc(i128, iterations);
    defer app.gpa.free(samples);
    for (samples) |*sample| {
        const t0 = nowNs();
        for (0..inner) |_| sink += runOnce(buf, fetch_strings);
        sample.* = nowNs() - t0;
    }

    var out_buf: [1 << 20]u8 = undefined;
    var stdout = app.stdout(&out_buf);
    const out = stdout.w();

    try out.print(
        "{{\"impl\":\"zig\",\"workload\":\"{s}\",\"mode\":\"{s}\",\"bytes\":{d}," ++
            "\"iterations\":{d},\"inner\":{d},\"cold_ns\":{d},\"alloc_count\":{d}," ++
            "\"alloc_bytes\":{d},\"peak_rss_kb\":{d},\"checksum\":{d},\"samples_ns\":[",
        .{
            path,
            if (fetch_strings) "strings" else "parse",
            buf.len,
            iterations,
            inner,
            cold_ns,
            alloc_count / (iterations * inner),
            alloc_bytes / (iterations * inner),
            peakRssKb(),
            sink,
        },
    );
    for (samples, 0..) |sample, i| {
        if (i != 0) try out.writeByte(',');
        try out.print("{d}", .{sample});
    }
    try out.writeAll("]}\n");
    try stdout.flush();
}
