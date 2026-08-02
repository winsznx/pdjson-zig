//! Zig-side transcriber: drives the Zig library through the same script as
//! oracle/transcript_c.c and emits byte-identical NDJSON.
//!
//! Keeping the two emitters as independent implementations (rather than one
//! shared formatter linked into both) is deliberate: a shared formatter could
//! mask a divergence by normalising it away on both sides.

const std = @import("std");
const cli = @import("cli.zig");
const pdjson = @import("pdjson");
const abi = pdjson.abi;
const core = pdjson.parser;

const schema = "pdjson-zig/transcript@1";
const max_records = 200000;

fn typeName(t: abi.Type) []const u8 {
    return switch (t) {
        .err => "ERROR",
        .done => "DONE",
        .object => "OBJECT",
        .object_end => "OBJECT_END",
        .array => "ARRAY",
        .array_end => "ARRAY_END",
        .string => "STRING",
        .number => "NUMBER",
        .true_ => "TRUE",
        .false_ => "FALSE",
        .null_ => "NULL",
        else => "NONE",
    };
}

fn putHex(out: *std.Io.Writer, bytes: []const u8) !void {
    const digits = "0123456789abcdef";
    for (bytes) |b| {
        try out.writeByte(digits[b >> 4]);
        try out.writeByte(digits[b & 0x0f]);
    }
}

// ---------------------------------------------------------------- allocator

/// Mirrors the oracle's failure injection: the first `budget` requests succeed,
/// everything after fails. -1 disables injection.
var alloc_budget: i64 = -1;

extern "c" fn malloc(size: usize) callconv(.c) ?*anyopaque;
extern "c" fn realloc(ptr: ?*anyopaque, size: usize) callconv(.c) ?*anyopaque;
extern "c" fn free(ptr: ?*anyopaque) callconv(.c) void;

fn countingMalloc(n: usize) callconv(.c) ?*anyopaque {
    if (alloc_budget == 0) return null;
    if (alloc_budget > 0) alloc_budget -= 1;
    return malloc(n);
}

fn countingRealloc(p: ?*anyopaque, n: usize) callconv(.c) ?*anyopaque {
    if (alloc_budget == 0) return null;
    if (alloc_budget > 0) alloc_budget -= 1;
    return realloc(p, n);
}

fn countingFree(p: ?*anyopaque) callconv(.c) void {
    free(p);
}

// --------------------------------------------------------------------- main

const Emitter = struct {
    out: *std.Io.Writer,

    fn record(self: Emitter, seq: usize, op: []const u8, event: abi.Type, s: *abi.Stream) !void {
        var len: usize = 0;
        _ = core.getStringPtr(s, &len);
        const tok = core.getStringSlice(s);
        const num: u64 = @bitCast(core.getNumber(s));
        var ctxn: usize = 0;
        const ctx = core.getContext(s, &ctxn);

        try self.out.print("{{\"seq\":{d},\"op\":\"{s}\",\"event\":\"{s}\",\"tok\":\"", .{ seq, op, typeName(event) });
        try putHex(self.out, tok);
        try self.out.print("\",\"toklen\":{d},\"num\":\"{x:0>16}\",\"line\":{d},\"pos\":{d}," ++
            "\"depth\":{d},\"ctx\":\"{s}\",\"ctxn\":{d},\"err\":", .{
            len,
            num,
            s.lineno,
            s.source.position,
            core.getDepth(s),
            typeName(ctx),
            ctxn,
        });
        if (core.getErrorSlice(s)) |err| {
            try self.out.writeByte('"');
            try putHex(self.out, err);
            try self.out.writeByte('"');
        } else {
            try self.out.writeAll("null");
        }
        try self.out.writeAll("}\n");
    }

    fn sourceByte(self: Emitter, seq: usize, op: []const u8, c: c_int, s: *abi.Stream) !void {
        try self.out.print(
            "{{\"seq\":{d},\"op\":\"{s}\",\"byte\":{d},\"line\":{d},\"pos\":{d}}}\n",
            .{ seq, op, c, s.lineno, s.source.position },
        );
    }
};

pub fn main(p: std.process.Init) !void {
    const app = try cli.App.init(p);

    const mode = app.arg(0, "next");
    const input = app.readInput(app.argOpt(1), 1 << 30) catch
        app.die("cannot read input", .{});
    defer app.gpa.free(input);

    if (std.mem.startsWith(u8, mode, "oom:")) {
        alloc_budget = std.fmt.parseInt(i64, mode[4..], 10) catch -1;
    }

    var buf: [1 << 16]u8 = undefined;
    var stdout = app.stdout(&buf);
    const e = Emitter{ .out = stdout.w() };

    try e.out.print("{{\"schema\":\"{s}\",\"mode\":\"{s}\",\"bytes\":{d}}}\n", .{ schema, mode, input.len });

    var stream: abi.Stream = undefined;
    const s = &stream;
    core.openBuffer(s, input.ptr, input.len);

    if (alloc_budget >= 0) {
        core.setAllocator(s, &.{
            .malloc = countingMalloc,
            .realloc = countingRealloc,
            .free = countingFree,
        });
    }

    const streaming = !std.mem.eql(u8, mode, "nostream");
    core.setStreaming(s, streaming);

    var seq: usize = 0;
    var first = true;

    while (seq < max_records) {
        if (std.mem.eql(u8, mode, "peek")) {
            const peeked = core.peekEvent(s);
            try e.record(seq, "peek", peeked, s);
            seq += 1;
            if (seq >= max_records) break;
        }

        const t = if (std.mem.eql(u8, mode, "skip")) core.skip(s) else core.nextEvent(s);
        try e.record(seq, if (std.mem.eql(u8, mode, "skip")) "skip" else "next", t, s);
        seq += 1;

        if (t == .err) break;

        if (t == .done) {
            if (!streaming) break;

            if (std.mem.eql(u8, mode, "sep")) {
                while (true) {
                    var c = core.sourcePeek(s);
                    if (!core.isSpace(c)) break;
                    try e.sourceByte(seq, "peek_byte", c, s);
                    seq += 1;
                    c = core.sourceGet(s);
                    try e.sourceByte(seq, "get_byte", c, s);
                    seq += 1;
                    if (c == '\n') break;
                    if (seq >= max_records) break;
                }
            }

            if (first) break;
            core.reset(s);
            try e.record(seq, "reset", .done, s);
            seq += 1;
            first = true;
        } else {
            first = false;
        }
    }

    if (seq >= max_records) {
        try e.out.writeAll("{\"truncated\":true}\n");
    } else {
        try e.out.print("{{\"end\":true,\"records\":{d}}}\n", .{seq});
    }

    core.close(s);
    try stdout.flush();
}
