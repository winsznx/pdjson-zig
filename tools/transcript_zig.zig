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

const schema = "pdjson-zig/transcript@2";

// A get/peek pair over a byte array for json_open_user, matching what fgetc
// does: a byte is an unsigned char widened to int, EOF is -1. Mirrors
// oracle/transcript_c.c's user_src exactly.
const UserSrc = struct {
    buf: []const u8,
    pos: usize = 0,

    var instance: UserSrc = .{ .buf = &.{} };

    fn get(p: ?*anyopaque) callconv(.c) c_int {
        _ = p;
        const u = &instance;
        if (u.pos >= u.buf.len) return -1;
        defer u.pos += 1;
        return u.buf[u.pos];
    }

    fn peek(p: ?*anyopaque) callconv(.c) c_int {
        _ = p;
        const u = &instance;
        if (u.pos >= u.buf.len) return -1;
        return u.buf[u.pos];
    }
};

extern "c" fn tmpfile() ?*anyopaque;
extern "c" fn fwrite(ptr: [*]const u8, size: usize, n: usize, stream: ?*anyopaque) usize;
extern "c" fn rewind(stream: ?*anyopaque) void;
extern "c" fn fclose(stream: ?*anyopaque) c_int;
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

        // Same rule as the C oracle, derived from the public API only: the
        // number is well defined exactly when a NUL sits inside the bytes the
        // parser wrote. Outside that, the original reads uninitialised heap
        // (upstream #38) and there is nothing meaningful to compare.
        const num_defined = std.mem.indexOfScalar(u8, tok, 0) != null;
        const num: u64 = @bitCast(core.getNumber(s));

        var ctxn: usize = 0;
        const ctx = core.getContext(s, &ctxn);

        try self.out.print("{{\"seq\":{d},\"op\":\"{s}\",\"event\":\"{s}\",\"tok\":\"", .{ seq, op, typeName(event) });
        try putHex(self.out, tok);
        try self.out.print("\",\"toklen\":{d},\"num\":", .{len});
        if (num_defined) {
            try self.out.print("\"{x:0>16}\"", .{num});
        } else {
            try self.out.writeAll("null");
        }
        try self.out.print(",\"line\":{d},\"pos\":{d}," ++
            "\"depth\":{d},\"ctx\":\"{s}\",\"ctxn\":{d},\"err\":", .{
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

    var buf: [1 << 16]u8 = undefined;
    var stdout = app.stdout(&buf);
    const e = Emitter{ .out = stdout.w() };

    if (std.mem.eql(u8, app.arg(0, ""), "--batch")) {
        const mode = app.arg(1, "next");
        const listfile = app.argOpt(2) orelse app.die("--batch needs a list file", .{});
        const list = app.readFile(listfile, 1 << 28) catch
            app.die("cannot read {s}", .{listfile});
        defer app.gpa.free(list);

        var it = std.mem.tokenizeAny(u8, list, "\r\n");
        while (it.next()) |path| {
            const data = app.readFile(path, 1 << 30) catch
                app.die("cannot read {s}", .{path});
            defer app.gpa.free(data);
            try e.out.print("{{\"input\":\"{s}\"}}\n", .{path});
            try transcribe(e, mode, data);
        }
        try stdout.flush();
        return;
    }

    if (std.mem.eql(u8, app.arg(0, ""), "--pack")) {
        const mode = app.arg(1, "next");
        const packfile = app.argOpt(2) orelse app.die("--pack needs a pack file", .{});
        const all = app.readFile(packfile, 1 << 30) catch
            app.die("cannot read {s}", .{packfile});
        defer app.gpa.free(all);

        var off: usize = 0;
        var index: usize = 0;
        while (off < all.len) {
            var n: usize = 0;
            var saw_digit = false;
            while (off < all.len and all[off] >= '0' and all[off] <= '9') : (off += 1) {
                n = n * 10 + (all[off] - '0');
                saw_digit = true;
            }
            if (!saw_digit or off >= all.len or all[off] != '\n') break;
            off += 1;
            if (off + n > all.len) break;

            try e.out.print("{{\"input\":\"pack:{d}\"}}\n", .{index});
            index += 1;
            try transcribe(e, mode, all[off .. off + n]);
            off += n;
        }
        try stdout.flush();
        return;
    }

    const mode = app.arg(0, "next");
    const input = app.readInput(app.argOpt(1), 1 << 30) catch
        app.die("cannot read input", .{});
    defer app.gpa.free(input);

    try transcribe(e, mode, input);
    try stdout.flush();
}

fn transcribe(e: Emitter, full_mode: []const u8, input: []const u8) !void {
    // Split an optional "<source>:" prefix off the mode, same as the C oracle.
    var source: []const u8 = "buffer";
    var mode = full_mode;
    if (std.mem.startsWith(u8, mode, "stream:")) {
        source = "stream";
        mode = mode["stream:".len..];
    } else if (std.mem.startsWith(u8, mode, "user:")) {
        source = "user";
        mode = mode["user:".len..];
    } else if (std.mem.startsWith(u8, mode, "string:")) {
        source = "string";
        mode = mode["string:".len..];
    }

    alloc_budget = -1;
    if (std.mem.startsWith(u8, mode, "oom:")) {
        alloc_budget = std.fmt.parseInt(i64, mode[4..], 10) catch -1;
    }

    try e.out.print("{{\"schema\":\"{s}\",\"mode\":\"{s}\",\"bytes\":{d}}}\n", .{ schema, full_mode, input.len });

    var stream: abi.Stream = undefined;
    const s = &stream;
    var fp: ?*anyopaque = null;
    var owned: ?[:0]u8 = null;

    if (std.mem.eql(u8, source, "stream")) {
        fp = tmpfile();
        if (fp == null) {
            try e.out.writeAll("{\"error\":\"tmpfile failed\"}\n");
            return;
        }
        if (input.len != 0) _ = fwrite(input.ptr, 1, input.len, fp);
        rewind(fp);
        core.openStream(s, fp);
    } else if (std.mem.eql(u8, source, "user")) {
        UserSrc.instance = .{ .buf = input };
        core.openUser(s, UserSrc.get, UserSrc.peek, null);
    } else if (std.mem.eql(u8, source, "string")) {
        // json_open_string derives the length with strlen, so a fixture with an
        // embedded NUL is deliberately truncated. Both implementations must
        // truncate identically, which is the point of covering it.
        const z = std.heap.page_allocator.allocSentinel(u8, input.len, 0) catch {
            try e.out.writeAll("{\"error\":\"oom\"}\n");
            return;
        };
        @memcpy(z, input);
        owned = z;
        core.openBufferString(s, z.ptr);
    } else {
        core.openBuffer(s, input.ptr, input.len);
    }

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

        var op_name: []const u8 = "next";
        var t: abi.Type = undefined;
        if (std.mem.startsWith(u8, mode, "skipuntil:")) {
            // json_skip_until consumes whole values until it reaches one of the
            // requested type; the record carries the parser state it leaves
            // behind, not just the return value.
            const target: abi.Type = @enumFromInt(
                std.fmt.parseInt(u32, mode["skipuntil:".len..], 10) catch 0,
            );
            t = core.skipUntil(s, target);
            op_name = "skipuntil";
        } else if (std.mem.eql(u8, mode, "skip")) {
            t = core.skip(s);
            op_name = "skip";
        } else {
            t = core.nextEvent(s);
        }
        try e.record(seq, op_name, t, s);
        seq += 1;

        // "after-end" keeps calling json_next past the terminal event, without a
        // reset. It is the only way to observe two documented properties: the
        // error flag latches, and DONE is idempotent. Every other mode stops at
        // the first terminal event, which is why the state-machine coverage
        // analysis reported both transitions as never reached.
        if (std.mem.eql(u8, mode, "after-end") and (t == .err or t == .done)) {
            var extra: usize = 0;
            while (extra < 2 and seq < max_records) : (extra += 1) {
                t = core.nextEvent(s);
                try e.record(seq, "next", t, s);
                seq += 1;
            }
            break;
        }

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
    if (fp != null) _ = fclose(fp);
    if (owned) |z| std.heap.page_allocator.free(z);
}
