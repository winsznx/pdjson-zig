//! Regression tests for every finding this project produced.
//!
//! Each test names the finding it locks down. Two came out of differential
//! testing against the pinned original and are documented as upstream defects;
//! the rest are edge cases where the port had to reproduce a subtle behaviour
//! exactly rather than the obvious one.

const std = @import("std");
const pdjson = @import("pdjson");
const abi = pdjson.abi;
const core = pdjson.parser;
const testing = std.testing;

// ---------------------------------------------------------------------------
// Finding 1: json_get_context() reads a stack slot push() never allocated.
// docs/upstream-bug-oom-stack.md. The original segfaults (empty stack) or
// reads out of bounds (stack full). The port must stay memory-safe and
// deterministic under the same allocation schedule.
// ---------------------------------------------------------------------------

var budget: i64 = -1;

extern "c" fn malloc(size: usize) callconv(.c) ?*anyopaque;
extern "c" fn realloc(ptr: ?*anyopaque, size: usize) callconv(.c) ?*anyopaque;
extern "c" fn free(ptr: ?*anyopaque) callconv(.c) void;

fn limMalloc(n: usize) callconv(.c) ?*anyopaque {
    if (budget == 0) return null;
    if (budget > 0) budget -= 1;
    return malloc(n);
}
fn limRealloc(p: ?*anyopaque, n: usize) callconv(.c) ?*anyopaque {
    if (budget == 0) return null;
    if (budget > 0) budget -= 1;
    return realloc(p, n);
}
fn limFree(p: ?*anyopaque) callconv(.c) void {
    free(p);
}

fn openLimited(s: *abi.Stream, input: []const u8, allowance: i64) void {
    budget = allowance;
    core.openBuffer(s, input.ptr, input.len);
    core.setAllocator(s, &.{ .malloc = limMalloc, .realloc = limRealloc, .free = limFree });
}

test "regression: context query after a failed first stack allocation" {
    var s: abi.Stream = undefined;
    openLimited(&s, "[1]", 0);
    defer core.close(&s);

    try testing.expectEqual(abi.Type.err, core.nextEvent(&s));
    try testing.expectEqualStrings("out of memory", core.getErrorSlice(&s).?);

    // The original dereferences a null stack here. The port reports "no
    // container" instead of reading memory it never allocated.
    var count: usize = 12345;
    try testing.expectEqual(abi.Type.done, core.getContext(&s, &count));
    // depth still reflects the advanced stack_top, matching the original's
    // observable json_get_depth().
    try testing.expectEqual(@as(usize, 1), core.getDepth(&s));
}

test "regression: context query after a stack growth failure mid-nesting" {
    var s: abi.Stream = undefined;
    // One allocation succeeds (4 frames), the 5th push fails.
    openLimited(&s, "[[[[[1]]]]]", 1);
    defer core.close(&s);

    for (0..4) |_| try testing.expectEqual(abi.Type.array, core.nextEvent(&s));
    try testing.expectEqual(abi.Type.err, core.nextEvent(&s));

    // The original reads stack[4] out of the 4-element allocation.
    var count: usize = 999;
    try testing.expectEqual(abi.Type.done, core.getContext(&s, &count));
    try testing.expectEqual(@as(usize, 5), core.getDepth(&s));
}

test "regression: token buffer allocation failure is reported, not crashed" {
    var s: abi.Stream = undefined;
    openLimited(&s, "\"abc\"", 0);
    defer core.close(&s);
    try testing.expectEqual(abi.Type.err, core.nextEvent(&s));
    try testing.expectEqualStrings("out of memory", core.getErrorSlice(&s).?);
}

// ---------------------------------------------------------------------------
// Finding 2: byte 0xFF is indistinguishable from EOF in the buffer source.
// docs/upstream-bug-0xff.md. The port reproduces this deliberately so the
// equivalence claim holds; -Dfix-0xff=true opts out.
// ---------------------------------------------------------------------------

test "regression: 0xFF matches the original's buffer-source behaviour" {
    var p = pdjson.Parser.initBuffer("\"\xff\"");
    defer p.deinit();
    try testing.expectError(pdjson.Error.Malformed, p.next());

    if (core.fix_0xff) {
        // Corrected build: the byte is a byte.
        try testing.expectEqualStrings("invalid UTF-8 character", p.errorMessage().?);
    } else if (@typeInfo(c_char).int.signedness == .signed) {
        // Bug-compatible build on a signed-char target: 0xFF reads as EOF, so
        // the string looks unterminated and position never advances past it.
        try testing.expectEqualStrings("unterminated string literal", p.errorMessage().?);
        try testing.expectEqual(@as(usize, 1), p.position());
    } else {
        try testing.expectEqualStrings("invalid UTF-8 character", p.errorMessage().?);
    }
}

test "regression: 0xFE is never confused with EOF" {
    var p = pdjson.Parser.initBuffer("\"\xfe\"");
    defer p.deinit();
    try testing.expectError(pdjson.Error.Malformed, p.next());
    try testing.expectEqualStrings("invalid UTF-8 character", p.errorMessage().?);
    try testing.expectEqual(@as(usize, 2), p.position());
}

// ---------------------------------------------------------------------------
// Behaviours that are easy to "fix" by accident while porting.
// ---------------------------------------------------------------------------

test "an embedded NUL in a diagnostic truncates what a C caller sees" {
    // read_value's default branch formats the offending byte with %c. For a
    // NUL byte that writes an actual NUL into errmsg, so json_get_error()
    // returns a message that stops early. Reproduced, not sanitised.
    var p = pdjson.Parser.initBuffer("\x00");
    defer p.deinit();
    try testing.expectError(pdjson.Error.Malformed, p.next());
    try testing.expectEqualStrings("unexpected byte '", p.errorMessage().?);
}

test "a high byte appears raw in a diagnostic" {
    var p = pdjson.Parser.initBuffer("\xfe");
    defer p.deinit();
    try testing.expectError(pdjson.Error.Malformed, p.next());
    try testing.expectEqualStrings("unexpected byte '\xfe' in value", p.errorMessage().?);
}

test "only the first error is latched" {
    var s: abi.Stream = undefined;
    core.openBuffer(&s, "[@", 2);
    defer core.close(&s);
    try testing.expectEqual(abi.Type.array, core.nextEvent(&s));
    try testing.expectEqual(abi.Type.err, core.nextEvent(&s));
    const first = core.getErrorSlice(&s).?;
    try testing.expectEqualStrings("unexpected byte '@' in value", first);
    // Further calls return ERROR without replacing the message.
    try testing.expectEqual(abi.Type.err, core.nextEvent(&s));
    try testing.expectEqualStrings("unexpected byte '@' in value", core.getErrorSlice(&s).?);
}

test "reset clears the error but keeps a peeked event and the token buffer" {
    var s: abi.Stream = undefined;
    core.openBuffer(&s, "1 @", 3);
    defer core.close(&s);

    try testing.expectEqual(abi.Type.number, core.nextEvent(&s));
    try testing.expectEqual(abi.Type.done, core.peekEvent(&s));

    core.reset(&s);
    // The token buffer is deliberately untouched by reset.
    try testing.expectEqualSlices(u8, "1\x00", core.getStringSlice(&s));
    // ...and so is the buffered peek.
    try testing.expectEqual(abi.Type.done, core.nextEvent(&s));
}

test "peek advances position, matching upstream issue #15" {
    var p = pdjson.Parser.initBuffer("  1024");
    defer p.deinit();
    try testing.expectEqual(@as(usize, 0), p.position());
    _ = try p.peek();
    // peek() calls next() underneath, so position moved. Documented upstream
    // as surprising; preserved here because callers may depend on it.
    try testing.expectEqual(@as(usize, 6), p.position());
    try testing.expectEqualStrings("1024", p.tokenText());
}

test "line counting only advances on newlines actually consumed" {
    var p = pdjson.Parser.initBuffer("\n\n\n1");
    defer p.deinit();
    try testing.expectEqual(pdjson.Event.number, try p.next());
    try testing.expectEqual(@as(usize, 4), p.lineno());
}

test "strings may contain NUL, so token length is not strlen" {
    var p = pdjson.Parser.initBuffer("\"a\\u0000b\"");
    defer p.deinit();
    try testing.expectEqual(pdjson.Event.string, try p.next());
    try testing.expectEqual(@as(usize, 4), p.token().len); // a, NUL, b, terminator
    try testing.expectEqualSlices(u8, "a\x00b", p.tokenText());
}

test "number tokens keep the raw lexeme for precision" {
    var p = pdjson.Parser.initBuffer("123456789012345678901234567890");
    defer p.deinit();
    try testing.expectEqual(pdjson.Event.number, try p.next());
    try testing.expectEqualStrings("123456789012345678901234567890", p.tokenText());
}

test "deep nesting does not recurse in the parser" {
    // 100k levels would blow a recursive descent parser's stack. The event
    // loop keeps its container stack on the heap, so this only costs memory.
    const depth = 100_000;
    const buf = std.testing.allocator.alloc(u8, depth) catch unreachable;
    defer std.testing.allocator.free(buf);
    @memset(buf, '[');

    var p = pdjson.Parser.initBuffer(buf);
    defer p.deinit();
    for (0..depth) |_| try testing.expectEqual(pdjson.Event.array_begin, try p.next());
    try testing.expectEqual(@as(usize, depth), p.depth());
    try testing.expectError(pdjson.Error.Malformed, p.next());
}

// ---------------------------------------------------------------------------
// Finding 3: hex-float rounding in json_get_number.
// Found by the published fuzz session at ~30 million cases. Not an upstream
// bug -- a defect in this port, and the reason the fuzzer exists.
// ---------------------------------------------------------------------------

test "regression: json_get_number rounds hex floats the way strtod does" {
    // The token buffer is reused across events and is not cleared, so after a
    // number token an unterminated string starting "0x" leaves json_get_number
    // looking at "0x" followed by the previous token's bytes. That is faithful
    // to the original; what was wrong was the rounding of the resulting
    // 19-hex-digit float.
    const input = "97634922337286237e3\"0x";
    var s: abi.Stream = undefined;
    core.openBuffer(&s, input.ptr, input.len);
    defer core.close(&s);

    try testing.expectEqual(abi.Type.number, core.skip(&s));
    try testing.expectEqual(abi.Type.done, core.skip(&s));
    core.reset(&s);
    try testing.expectEqual(abi.Type.err, core.skip(&s));

    // libc strtod("0x634922337286237e3") == 0x4418d2488cdca189.
    // std.fmt.parseFloat truncated to ...188.
    try testing.expectEqual(
        @as(u64, 0x4418d2488cdca189),
        @as(u64, @bitCast(core.getNumber(&s))),
    );
}

test "regression: hex floats round correctly across the subnormal boundary" {
    // Found by the randomised hex-float test added alongside the fix: an
    // earlier version worked in significand bits and returned 0 here, where
    // the value is above half the smallest subnormal and must round up to it.
    try testing.expectEqual(
        @as(u64, 1),
        @as(u64, @bitCast(pdjson.strtod.value("0xaBfA4fP-1098"))),
    );
}

test "the parser never panics on arbitrary bytes" {
    var prng = std.Random.DefaultPrng.init(0x5EED);
    const rand = prng.random();
    var buf: [128]u8 = undefined;

    for (0..20000) |_| {
        const n = rand.uintLessThan(usize, buf.len);
        rand.bytes(buf[0..n]);
        var p = pdjson.Parser.initBuffer(buf[0..n]);
        defer p.deinit();
        for (0..64) |_| {
            const e = p.next() catch break;
            if (e == .done) break;
        }
    }
}
