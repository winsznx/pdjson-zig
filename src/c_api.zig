//! The exported C surface: one `export fn` per symbol in the pinned pdjson.h,
//! with matching signatures and calling convention.
//!
//! This layer holds no parser logic. Its whole job is translating between the
//! C conventions (nullable raw pointers, `int` sentinels, `char *` strings) and
//! the Zig core in parser.zig, so that the state machine never has to reason
//! about C's representation choices.

const std = @import("std");
const abi = @import("abi.zig");
const parser = @import("parser.zig");

const Stream = abi.Stream;

/// The empty string `json_get_string` returns when nothing has been parsed.
/// A real object with static lifetime, matching the C string literal.
const empty: [1:0]u8 = .{0};

export fn json_open_buffer(json: *Stream, buffer: ?*const anyopaque, size: usize) callconv(.c) void {
    const bytes: ?[*]const u8 = if (buffer) |p| @ptrCast(p) else null;
    parser.openBuffer(json, bytes, size);
}

export fn json_open_string(json: *Stream, string: [*:0]const u8) callconv(.c) void {
    const s = std.mem.span(string);
    parser.openBuffer(json, s.ptr, s.len);
}

export fn json_open_stream(json: *Stream, stream: ?*anyopaque) callconv(.c) void {
    parser.openStream(json, stream);
}

export fn json_open_user(
    json: *Stream,
    get: ?abi.UserIo,
    peek: ?abi.UserIo,
    user: ?*anyopaque,
) callconv(.c) void {
    parser.openUser(json, get, peek, user);
}

export fn json_close(json: *Stream) callconv(.c) void {
    parser.close(json);
}

export fn json_set_allocator(json: *Stream, a: *const abi.Allocator) callconv(.c) void {
    parser.setAllocator(json, a);
}

export fn json_set_streaming(json: *Stream, mode: bool) callconv(.c) void {
    parser.setStreaming(json, mode);
}

export fn json_next(json: *Stream) callconv(.c) abi.Type {
    return parser.nextEvent(json);
}

export fn json_peek(json: *Stream) callconv(.c) abi.Type {
    return parser.peekEvent(json);
}

export fn json_reset(json: *Stream) callconv(.c) void {
    parser.reset(json);
}

export fn json_get_string(json: *Stream, length: ?*usize) callconv(.c) [*:0]const u8 {
    if (parser.getStringPtr(json, length)) |p| {
        // The buffer carries a NUL by construction (see parser.getNumber for
        // why); this hands C the same pointer the original would.
        return @ptrCast(p);
    }
    return &empty;
}

export fn json_get_number(json: *Stream) callconv(.c) f64 {
    return parser.getNumber(json);
}

export fn json_skip(json: *Stream) callconv(.c) abi.Type {
    return parser.skip(json);
}

export fn json_skip_until(json: *Stream, t: abi.Type) callconv(.c) abi.Type {
    return parser.skipUntil(json, t);
}

export fn json_get_lineno(json: *Stream) callconv(.c) usize {
    return json.lineno;
}

export fn json_get_position(json: *Stream) callconv(.c) usize {
    return json.source.position;
}

export fn json_get_depth(json: *Stream) callconv(.c) usize {
    return parser.getDepth(json);
}

export fn json_get_context(json: *Stream, count: ?*usize) callconv(.c) abi.Type {
    return parser.getContext(json, count);
}

export fn json_get_error(json: *Stream) callconv(.c) ?[*:0]const u8 {
    if (!parser.hasError(json)) return null;
    return @ptrCast(&json.errmsg);
}

export fn json_source_get(json: *Stream) callconv(.c) c_int {
    return parser.sourceGet(json);
}

export fn json_source_peek(json: *Stream) callconv(.c) c_int {
    return parser.sourcePeek(json);
}

export fn json_isspace(c: c_int) callconv(.c) bool {
    return parser.isSpace(c);
}

comptime {
    // Force the exports to be emitted even when nothing references them.
    _ = json_open_buffer;
}
