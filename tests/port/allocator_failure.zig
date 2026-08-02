//! Exhaustive allocation-failure schedules.
//!
//! `json_set_allocator` is part of the public API, so allocation failure is a
//! supported scenario, not a hypothetical. This walks every failure point for
//! a set of inputs: for k = 0, 1, 2, ... let the k-th allocation be the first
//! to fail, and require that the parser reports "out of memory" and stays
//! usable rather than crashing, hanging, or reading memory it never obtained.
//!
//! Running under Zig's safety checks means an out-of-bounds read or an integer
//! overflow anywhere in these paths fails the test rather than passing quietly.

const std = @import("std");
const pdjson = @import("pdjson");
const abi = pdjson.abi;
const core = pdjson.parser;
const testing = std.testing;

var budget: i64 = -1;
var live: usize = 0;

extern "c" fn malloc(size: usize) callconv(.c) ?*anyopaque;
extern "c" fn realloc(ptr: ?*anyopaque, size: usize) callconv(.c) ?*anyopaque;
extern "c" fn free(ptr: ?*anyopaque) callconv(.c) void;

fn limMalloc(n: usize) callconv(.c) ?*anyopaque {
    if (budget == 0) return null;
    if (budget > 0) budget -= 1;
    const p = malloc(n);
    if (p != null) live += 1;
    return p;
}

fn limRealloc(p: ?*anyopaque, n: usize) callconv(.c) ?*anyopaque {
    if (budget == 0) return null;
    if (budget > 0) budget -= 1;
    const q = realloc(p, n);
    if (q != null and p == null) live += 1;
    return q;
}

fn limFree(p: ?*anyopaque) callconv(.c) void {
    if (p != null) live -= 1;
    free(p);
}

const INPUTS = [_][]const u8{
    "1",
    "\"abc\"",
    "[]",
    "[1]",
    "[[[[1]]]]",
    "{\"a\":1}",
    "{\"a\":[1,2,{\"b\":\"c\"}]}",
    "[" ++ "[" ** 20 ++ "]" ** 20 ++ "]",
    "\"" ++ "x" ** 2000 ++ "\"",
    "1 2 3",
    "[1,2,3",
    "\"\\uD800\\uDC00\"",
};

test "every allocation failure point is reported, never crashed" {
    for (INPUTS) |input| {
        var k: i64 = 0;
        while (k < 40) : (k += 1) {
            budget = k;
            live = 0;

            var s: abi.Stream = undefined;
            core.openBuffer(&s, input.ptr, input.len);
            core.setAllocator(&s, &.{
                .malloc = limMalloc,
                .realloc = limRealloc,
                .free = limFree,
            });

            var steps: usize = 0;
            while (steps < 200) : (steps += 1) {
                const t = core.nextEvent(&s);
                if (t == .err) break;
                if (t == .done) break;

                // Accessors must stay safe in every state, including right
                // after a failed allocation.
                var count: usize = 0;
                _ = core.getContext(&s, &count);
                _ = core.getDepth(&s);
                _ = core.getStringSlice(&s);
                _ = core.getNumber(&s);
            }

            // Whether or not this schedule happened to hit a failure, closing
            // must release exactly what was obtained.
            core.close(&s);
            try testing.expectEqual(@as(usize, 0), live);
        }
    }
    budget = -1;
}

test "a stream stays inspectable after an allocation failure" {
    budget = 0;
    live = 0;
    var s: abi.Stream = undefined;
    core.openBuffer(&s, "[1,2,3]", 7);
    core.setAllocator(&s, &.{ .malloc = limMalloc, .realloc = limRealloc, .free = limFree });

    try testing.expectEqual(abi.Type.err, core.nextEvent(&s));
    try testing.expectEqualStrings("out of memory", core.getErrorSlice(&s).?);

    // Every accessor, in every order, on a stream whose stack was never
    // allocated. None of these may read unallocated memory.
    var count: usize = 0;
    _ = core.getContext(&s, &count);
    _ = core.getContext(&s, null);
    _ = core.getDepth(&s);
    _ = core.getStringSlice(&s);
    _ = core.getNumber(&s);
    _ = core.sourcePeek(&s);
    core.reset(&s);
    _ = core.getContext(&s, &count);
    try testing.expectEqual(abi.Type.done, core.getContext(&s, &count));

    core.close(&s);
    try testing.expectEqual(@as(usize, 0), live);
    budget = -1;
}

test "the default allocator path still works after a limited one" {
    budget = -1;
    var p = pdjson.Parser.initBuffer("[1,2,3]");
    defer p.deinit();
    try testing.expectEqual(pdjson.Event.array_begin, try p.next());
}
