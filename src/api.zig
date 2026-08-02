//! The Zig-native face of the parser.
//!
//! Same state machine and same observable behaviour as the C surface, but
//! expressed the way a Zig caller would want it: a real error set instead of a
//! sentinel event, slices instead of pointer+length, and no manual `close()`
//! bookkeeping beyond `defer`.

const std = @import("std");
const abi = @import("abi.zig");
const core = @import("parser.zig");

/// Events a caller can receive. `.err` is absent by construction — failures
/// come back as `error.Malformed` with the message available from `errorMessage`.
pub const Event = enum {
    done,
    object_begin,
    object_end,
    array_begin,
    array_end,
    string,
    number,
    true_value,
    false_value,
    null_value,

    fn from(t: abi.Type) ?Event {
        return switch (t) {
            .done => .done,
            .object => .object_begin,
            .object_end => .object_end,
            .array => .array_begin,
            .array_end => .array_end,
            .string => .string,
            .number => .number,
            .true_ => .true_value,
            .false_ => .false_value,
            .null_ => .null_value,
            else => null,
        };
    }
};

pub const Context = union(enum) {
    /// Not inside a container.
    top_level,
    /// Inside an array; `count` is how many events this level has produced.
    array: usize,
    /// Inside an object; an odd `count` means the last string was a member name.
    object: usize,
};

pub const Error = error{
    /// The input is not well-formed. `errorMessage()` explains where and why.
    Malformed,
    /// The configured allocator refused a request.
    OutOfMemory,
};

/// A streaming JSON parser over a byte slice.
///
/// The struct is the C-layout `json_stream`, so a `*Parser` can be handed
/// straight to C code expecting a `json_stream *`.
pub const Parser = struct {
    stream: abi.Stream,

    /// Parse `input`. The slice must outlive the parser; nothing is copied
    /// except the current token.
    pub fn initBuffer(input: []const u8) Parser {
        var p: Parser = .{ .stream = undefined };
        core.openBuffer(&p.stream, input.ptr, input.len);
        return p;
    }

    /// Release the token buffer and container stack.
    pub fn deinit(self: *Parser) void {
        core.close(&self.stream);
    }

    /// When false (the default is true), any trailing non-whitespace byte after
    /// the first value is an error instead of the start of the next value.
    pub fn setStreaming(self: *Parser, mode: bool) void {
        core.setStreaming(&self.stream, mode);
    }

    /// Install a custom allocator, matching `json_set_allocator`.
    pub fn setAllocator(self: *Parser, a: abi.Allocator) void {
        core.setAllocator(&self.stream, &a);
    }

    pub fn next(self: *Parser) Error!Event {
        return self.classify(core.nextEvent(&self.stream));
    }

    /// Look at the next event without consuming it.
    ///
    /// Note this really does advance the underlying source: `position()` and
    /// `token()` reflect the peeked event afterwards. That is upstream's
    /// documented-by-issue behaviour (skeeto/pdjson#15) and is preserved.
    pub fn peek(self: *Parser) Error!Event {
        return self.classify(core.peekEvent(&self.stream));
    }

    fn classify(self: *Parser, t: abi.Type) Error!Event {
        if (Event.from(t)) |e| return e;
        if (self.isOutOfMemory()) return Error.OutOfMemory;
        return Error.Malformed;
    }

    fn isOutOfMemory(self: *Parser) bool {
        const msg = core.getErrorSlice(&self.stream) orelse return false;
        return std.mem.eql(u8, msg, "out of memory");
    }

    /// Clear the error state and container stack so the next value in a stream
    /// can be read. Line and byte position carry over, as does any peeked event.
    pub fn reset(self: *Parser) void {
        core.reset(&self.stream);
    }

    /// Consume the current value entirely, including nested containers.
    pub fn skip(self: *Parser) Error!Event {
        return self.classify(core.skip(&self.stream));
    }

    /// Skip whole values until one of `target` is reached.
    ///
    /// Returns `.done` if the stream completes first, and the parser is left
    /// positioned just after whatever it stopped on, so it stays usable.
    pub fn skipUntil(self: *Parser, target: Event) Error!Event {
        const t: abi.Type = switch (target) {
            .done => .done,
            .object_begin => .object,
            .object_end => .object_end,
            .array_begin => .array,
            .array_end => .array_end,
            .string => .string,
            .number => .number,
            .true_value => .true_,
            .false_value => .false_,
            .null_value => .null_,
        };
        return self.classify(core.skipUntil(&self.stream, t));
    }

    /// The bytes of the most recent string or number token.
    ///
    /// Strings are decoded UTF-8 and may contain interior NULs, which is why
    /// this is a slice rather than a sentinel pointer. Numbers are the raw
    /// lexeme as it appeared in the input, useful when `f64` would lose
    /// precision. Both include the trailing NUL the C API relies on, so the
    /// slice is `len - 1` payload bytes... except that upstream counts the NUL
    /// in `string_fill`; `token()` returns exactly what `json_get_string`
    /// reports, NUL included, for parity.
    pub fn token(self: *Parser) []const u8 {
        return core.getStringSlice(&self.stream);
    }

    /// The current token with upstream's trailing NUL removed.
    pub fn tokenText(self: *Parser) []const u8 {
        const raw = self.token();
        if (raw.len > 0 and raw[raw.len - 1] == 0) return raw[0 .. raw.len - 1];
        return raw;
    }

    /// The current number token as an `f64`, matching `json_get_number`.
    pub fn number(self: *Parser) f64 {
        return core.getNumber(&self.stream);
    }

    /// The latched diagnostic, or null if no error is pending.
    pub fn errorMessage(self: *Parser) ?[]const u8 {
        return core.getErrorSlice(&self.stream);
    }

    pub fn lineno(self: *Parser) usize {
        return self.stream.lineno;
    }

    pub fn position(self: *Parser) usize {
        return self.stream.source.position;
    }

    pub fn depth(self: *Parser) usize {
        return core.getDepth(&self.stream);
    }

    pub fn context(self: *Parser) Context {
        var count: usize = 0;
        return switch (core.getContext(&self.stream, &count)) {
            .array => .{ .array = count },
            .object => .{ .object = count },
            else => .top_level,
        };
    }
};

const testing = std.testing;

test "scalar values" {
    var p = Parser.initBuffer("  1024\n");
    defer p.deinit();
    try testing.expectEqual(Event.number, try p.next());
    try testing.expectEqualStrings("1024", p.tokenText());
    try testing.expectEqual(@as(f64, 1024), p.number());
    try testing.expectEqual(Event.done, try p.next());
}

test "object walk with context" {
    var p = Parser.initBuffer("{\"abc\": -1}");
    defer p.deinit();
    try testing.expectEqual(Event.object_begin, try p.next());
    try testing.expectEqual(@as(usize, 1), p.depth());
    try testing.expectEqual(Event.string, try p.next());
    try testing.expectEqualStrings("abc", p.tokenText());
    try testing.expectEqual(Context{ .object = 1 }, p.context());
    try testing.expectEqual(Event.number, try p.next());
    try testing.expectEqualStrings("-1", p.tokenText());
    try testing.expectEqual(Event.object_end, try p.next());
    try testing.expectEqual(@as(usize, 0), p.depth());
    try testing.expectEqual(Event.done, try p.next());
}

test "malformed input reports a message, not a panic" {
    var p = Parser.initBuffer("[1, 2, 3");
    defer p.deinit();
    try testing.expectEqual(Event.array_begin, try p.next());
    for (0..3) |_| try testing.expectEqual(Event.number, try p.next());
    try testing.expectError(Error.Malformed, p.next());
    try testing.expectEqualStrings("unexpected end of text", p.errorMessage().?);
}

test "streaming multiple values with reset" {
    var p = Parser.initBuffer("1 10 100");
    defer p.deinit();
    var seen: [3]f64 = undefined;
    for (0..3) |i| {
        try testing.expectEqual(Event.number, try p.next());
        seen[i] = p.number();
        try testing.expectEqual(Event.done, try p.next());
        p.reset();
    }
    try testing.expectEqualSlices(f64, &.{ 1, 10, 100 }, &seen);
    try testing.expectEqual(Event.done, try p.next());
}

test "strings keep interior NUL bytes" {
    var p = Parser.initBuffer("\"a\\u0000b\"");
    defer p.deinit();
    try testing.expectEqual(Event.string, try p.next());
    try testing.expectEqualSlices(u8, "a\x00b", p.tokenText());
}

test "surrogate pair decodes to UTF-8" {
    var p = Parser.initBuffer("\"\\uD800\\uDC00\"");
    defer p.deinit();
    try testing.expectEqual(Event.string, try p.next());
    try testing.expectEqualSlices(u8, "\xf0\x90\x80\x80", p.tokenText());
}

test "non-streaming mode rejects trailing data" {
    var p = Parser.initBuffer("1 2");
    defer p.deinit();
    p.setStreaming(false);
    try testing.expectEqual(Event.number, try p.next());
    try testing.expectError(Error.Malformed, p.next());
    try testing.expectEqualStrings("expected end of text instead of byte '2'", p.errorMessage().?);
}

test "skip consumes a whole nested value" {
    var p = Parser.initBuffer("[[1,2,{\"a\":[3]}],9]");
    defer p.deinit();
    try testing.expectEqual(Event.array_begin, try p.next());
    try testing.expectEqual(Event.array_begin, try p.skip());
    try testing.expectEqual(Event.number, try p.next());
    try testing.expectEqualStrings("9", p.tokenText());
    try testing.expectEqual(Event.array_end, try p.next());
}
