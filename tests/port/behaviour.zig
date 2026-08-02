//! Behavioural tests written against the Zig-native API.
//!
//! These are the port's own tests, kept separate from the upstream suite so
//! that "18/18 upstream assertions pass" stays an independent statement. They
//! cover ground the upstream suite does not: positions, depth, context,
//! diagnostics, skip, and the strict/streaming distinction.

const std = @import("std");
const pdjson = @import("pdjson");
const Parser = pdjson.Parser;
const Event = pdjson.Event;
const Error = pdjson.Error;
const testing = std.testing;

fn expectEvents(input: []const u8, expected: []const Event) !void {
    var p = Parser.initBuffer(input);
    defer p.deinit();
    for (expected) |e| try testing.expectEqual(e, try p.next());
}

fn expectError(input: []const u8, message: []const u8) !void {
    var p = Parser.initBuffer(input);
    defer p.deinit();
    while (true) {
        const e = p.next() catch {
            try testing.expectEqualStrings(message, p.errorMessage().?);
            return;
        };
        if (e == .done) break;
    }
    std.debug.print("expected error \"{s}\" for input \"{s}\"\n", .{ message, input });
    return error.NoErrorRaised;
}

// ------------------------------------------------------------------ structure

test "literals" {
    try expectEvents("null", &.{ .null_value, .done });
    try expectEvents("true", &.{ .true_value, .done });
    try expectEvents("false", &.{ .false_value, .done });
}

test "empty containers" {
    try expectEvents("[]", &.{ .array_begin, .array_end, .done });
    try expectEvents("{}", &.{ .object_begin, .object_end, .done });
}

test "nested containers" {
    try expectEvents(
        "{\"a\":[1,{\"b\":null}]}",
        &.{ .object_begin, .string, .array_begin, .number, .object_begin, .string, .null_value, .object_end, .array_end, .object_end, .done },
    );
}

test "object events alternate name and value" {
    var p = Parser.initBuffer("{\"x\":1,\"y\":2}");
    defer p.deinit();
    try testing.expectEqual(Event.object_begin, try p.next());
    var names: [2][]const u8 = undefined;
    for (0..2) |i| {
        try testing.expectEqual(Event.string, try p.next());
        names[i] = try testing.allocator.dupe(u8, p.tokenText());
        try testing.expectEqual(Event.number, try p.next());
    }
    defer for (names) |n| testing.allocator.free(n);
    try testing.expectEqualStrings("x", names[0]);
    try testing.expectEqualStrings("y", names[1]);
    try testing.expectEqual(Event.object_end, try p.next());
}

// --------------------------------------------------------------- diagnostics

test "malformed structure reports the upstream message" {
    try expectError("[1,]", "unexpected byte ']' in value");
    try expectError("[,1]", "unexpected byte ',' in value");
    try expectError("{1:2}", "expected member name or '}'");
    try expectError("{\"a\" 1}", "expected ':' after member name");
    try expectError("{\"a\":1 \"b\":2}", "expected ',' or '}' after member value");
    try expectError("[1, 2, 3", "unexpected end of text");
    try expectError("[1}", "unexpected byte '}'");
    try expectError("@", "unexpected byte '@' in value");
    try expectError("tru", "expected 'e' instead of end of text");
    try expectError("True", "unexpected byte 'T' in value");
}

test "malformed numbers report the upstream message" {
    try expectError("-", "unexpected end of text in number");
    try expectError("-x", "unexpected byte 'x' in number");
    try expectError("[1.]", "expected digit instead of byte ']'");
    try expectError("1.", "expected digit instead of end of text");
    try expectError("1e", "unexpected end of text in number");
    try expectError("[1e+]", "expected digit instead of byte ']'");
}

test "malformed strings report the upstream message" {
    try expectError("\"abc", "unterminated string literal");
    try expectError("\"\\x\"", "invalid escaped byte 'x'");
    try expectError("\"\\", "unterminated string literal in escape");
    try expectError("\"a\nb\"", "unescaped control character in string");
    try expectError("\"\\u12g4\"", "invalid escape Unicode byte 'g'");
    try expectError("\"\\u12\"", "invalid escape Unicode byte '\"'");
    try expectError("\"\\uD800\\u0065\"", "surrogate pair continuation \\u0065 out of range (dc00-dfff)");
    try expectError("\"\\uDC00\"", "dangling surrogate \\udc00");
    try expectError("\"\\uD800x\"", "invalid continuation for surrogate pair 'x', expected '\\'");
    try expectError("\"\\uD800\\x\"", "invalid continuation for surrogate pair 'x', expected 'u'");
}

test "invalid UTF-8 reports the upstream message" {
    try expectError("\"\x80\"", "invalid UTF-8 character");
    try expectError("\"\xc0\x80\"", "invalid UTF-8 character");
    try expectError("\"\xed\xa0\x80\"", "invalid UTF-8 text");
    try expectError("\"\xf5\x80\x80\x80\"", "invalid UTF-8 character");
    try expectError("\"\xf4\x90\x80\x80\"", "invalid UTF-8 text");
}

// ------------------------------------------------------------------- unicode

test "escapes decode to their control characters" {
    var p = Parser.initBuffer("\"\\\"\\\\\\/\\b\\f\\n\\r\\t\"");
    defer p.deinit();
    try testing.expectEqual(Event.string, try p.next());
    try testing.expectEqualSlices(u8, "\"\\/\x08\x0c\n\r\t", p.tokenText());
}

test "\\u escapes re-encode as UTF-8" {
    const cases = [_]struct { []const u8, []const u8 }{
        .{ "\"\\u0041\"", "A" },
        .{ "\"\\u00e9\"", "\xc3\xa9" },
        .{ "\"\\u4e2d\"", "\xe4\xb8\xad" },
        .{ "\"\\uffff\"", "\xef\xbf\xbf" },
        .{ "\"\\uD800\\uDC00\"", "\xf0\x90\x80\x80" },
        .{ "\"\\uDBFF\\uDFFF\"", "\xf4\x8f\xbf\xbf" },
    };
    for (cases) |c| {
        var p = Parser.initBuffer(c[0]);
        defer p.deinit();
        try testing.expectEqual(Event.string, try p.next());
        try testing.expectEqualSlices(u8, c[1], p.tokenText());
    }
}

test "well-formed raw UTF-8 passes through unchanged" {
    const cases = [_][]const u8{ "\xc3\xa9", "\xe4\xb8\xad", "\xf0\x9f\x98\x80", "\xef\xbf\xbf" };
    for (cases) |body| {
        const input = try std.fmt.allocPrint(testing.allocator, "\"{s}\"", .{body});
        defer testing.allocator.free(input);
        var p = Parser.initBuffer(input);
        defer p.deinit();
        try testing.expectEqual(Event.string, try p.next());
        try testing.expectEqualSlices(u8, body, p.tokenText());
    }
}

// ------------------------------------------------------- positions and depth

test "byte position tracks consumed input" {
    var p = Parser.initBuffer("[1, 22, 333]");
    defer p.deinit();
    const expected = [_]usize{ 1, 2, 6, 11, 12 };
    for (expected) |want| {
        _ = try p.next();
        try testing.expectEqual(want, p.position());
    }
}

test "line numbers count newlines actually consumed" {
    // A newline only bumps the counter once the whitespace skipper walks over
    // it, so the count reflects where the parser is, not where the token ends.
    // Values cross-checked against the C oracle.
    var p = Parser.initBuffer("[\n1,\n2\n]");
    defer p.deinit();
    const expected = [_]usize{ 1, 2, 3, 4 };
    for (expected) |want| {
        _ = try p.next();
        try testing.expectEqual(want, p.lineno());
    }
}

test "depth and context track the container stack" {
    var p = Parser.initBuffer("{\"a\":[1]}");
    defer p.deinit();
    try testing.expectEqual(pdjson.Context.top_level, p.context());
    _ = try p.next(); // {
    try testing.expectEqual(@as(usize, 1), p.depth());
    try testing.expectEqual(pdjson.Context{ .object = 0 }, p.context());
    _ = try p.next(); // "a"
    try testing.expectEqual(pdjson.Context{ .object = 1 }, p.context());
    _ = try p.next(); // [
    try testing.expectEqual(@as(usize, 2), p.depth());
    try testing.expectEqual(pdjson.Context{ .array = 0 }, p.context());
    _ = try p.next(); // 1
    try testing.expectEqual(pdjson.Context{ .array = 1 }, p.context());
    _ = try p.next(); // ]
    try testing.expectEqual(@as(usize, 1), p.depth());
    _ = try p.next(); // }
    try testing.expectEqual(@as(usize, 0), p.depth());
    try testing.expectEqual(pdjson.Context.top_level, p.context());
}

// ----------------------------------------------------------------- streaming

test "streaming mode reads consecutive values after reset" {
    var p = Parser.initBuffer("{\"a\":1}[2]\"three\"");
    defer p.deinit();
    const shapes = [_]Event{ .object_begin, .array_begin, .string };
    for (shapes) |first| {
        try testing.expectEqual(first, try p.next());
        while (true) {
            const e = try p.next();
            if (e == .done) break;
        }
        p.reset();
    }
    try testing.expectEqual(Event.done, try p.next());
}

test "an empty stream is a single done event" {
    try expectEvents(" \n\t ", &.{.done});
    try expectEvents("", &.{.done});
}

test "strict mode rejects anything after the first value" {
    var p = Parser.initBuffer("[] []");
    defer p.deinit();
    p.setStreaming(false);
    try testing.expectEqual(Event.array_begin, try p.next());
    try testing.expectEqual(Event.array_end, try p.next());
    try testing.expectError(Error.Malformed, p.next());
    try testing.expectEqualStrings("expected end of text instead of byte '['", p.errorMessage().?);
}

test "strict mode accepts trailing whitespace" {
    var p = Parser.initBuffer("[]  \n\t ");
    defer p.deinit();
    p.setStreaming(false);
    try testing.expectEqual(Event.array_begin, try p.next());
    try testing.expectEqual(Event.array_end, try p.next());
    try testing.expectEqual(Event.done, try p.next());
}

// ---------------------------------------------------------------------- skip

test "skip walks past whole values at any depth" {
    var p = Parser.initBuffer("[{\"a\":[1,2,{\"b\":3}]},7]");
    defer p.deinit();
    try testing.expectEqual(Event.array_begin, try p.next());
    try testing.expectEqual(Event.object_begin, try p.skip());
    try testing.expectEqual(Event.number, try p.next());
    try testing.expectEqualStrings("7", p.tokenText());
}

test "skip over a scalar consumes exactly one event" {
    var p = Parser.initBuffer("[1,2]");
    defer p.deinit();
    try testing.expectEqual(Event.array_begin, try p.next());
    try testing.expectEqual(Event.number, try p.skip());
    try testing.expectEqual(Event.number, try p.next());
    try testing.expectEqualStrings("2", p.tokenText());
}

// -------------------------------------------------------------- token buffer

test "token buffer grows past its initial 1 KiB" {
    for ([_]usize{ 1000, 1023, 1024, 1025, 5000, 40000 }) |n| {
        const body = try testing.allocator.alloc(u8, n);
        defer testing.allocator.free(body);
        @memset(body, 'a');
        const input = try std.fmt.allocPrint(testing.allocator, "\"{s}\"", .{body});
        defer testing.allocator.free(input);

        var p = Parser.initBuffer(input);
        defer p.deinit();
        try testing.expectEqual(Event.string, try p.next());
        try testing.expectEqual(n, p.tokenText().len);
    }
}

test "the token buffer is reused across events" {
    var p = Parser.initBuffer("[\"first\",\"second\"]");
    defer p.deinit();
    _ = try p.next();
    _ = try p.next();
    try testing.expectEqualStrings("first", p.tokenText());
    _ = try p.next();
    try testing.expectEqualStrings("second", p.tokenText());
}
