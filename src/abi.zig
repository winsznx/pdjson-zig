//! Binary-compatible declarations of the public pdjson types.
//!
//! `struct json_stream` is *not* opaque in the original library: `pdjson.h`
//! spells out every field, and the upstream tests declare it by value on the
//! stack (`struct json_stream json[1];`). Any drop-in replacement therefore has
//! to reproduce the layout exactly, not merely the function signatures.
//!
//! These declarations are written independently from the header rather than
//! machine-translated from it. `tools/abi_probe_c.c` asks the C compiler what
//! the pinned header dictates, `tools/abi_probe.zig` asks Zig what these
//! declarations produce, and `scripts/abi-check.sh` fails the build if the two
//! tables disagree on any offset, size, alignment, or enumerator.

const std = @import("std");
const builtin = @import("builtin");

/// C's `EOF`. Not 0xFF-safe by design; see `Source.bufferPeek` and
/// docs/upstream-bug-0xff.md for why that distinction matters here.
pub const EOF: c_int = -1;

/// `enum json_type` from pdjson.h.
///
/// The zero value is not an enumerator in C, but the original code stores
/// `(enum json_type)0` in `json_stream.next` as a "nothing buffered" sentinel,
/// so it is a real inhabitant of the type and is named here.
pub const Type = enum(c_uint) {
    none = 0,
    err = 1,
    done = 2,
    object = 3,
    object_end = 4,
    array = 5,
    array_end = 6,
    string = 7,
    number = 8,
    true_ = 9,
    false_ = 10,
    null_ = 11,
    _,
};

/// `typedef int (*json_user_io)(void *user);`
pub const UserIo = *const fn (user: ?*anyopaque) callconv(.c) c_int;

/// `struct json_allocator`.
pub const Allocator = extern struct {
    malloc: ?*const fn (size: usize) callconv(.c) ?*anyopaque,
    realloc: ?*const fn (ptr: ?*anyopaque, size: usize) callconv(.c) ?*anyopaque,
    free: ?*const fn (ptr: ?*anyopaque) callconv(.c) void,
};

pub const BufferSource = extern struct {
    buffer: ?[*]const u8,
    length: usize,
};

pub const StreamSource = extern struct {
    stream: ?*anyopaque,
};

pub const UserSource = extern struct {
    ptr: ?*anyopaque,
    get: ?UserIo,
    peek: ?UserIo,
};

pub const SourceUnion = extern union {
    stream: StreamSource,
    buffer: BufferSource,
    user: UserSource,
};

/// `struct json_source`.
pub const Source = extern struct {
    get: ?*const fn (source: *Source) callconv(.c) c_int,
    peek: ?*const fn (source: *Source) callconv(.c) c_int,
    position: usize,
    source: SourceUnion,
};

/// `struct json_stack` — private to pdjson.c (the public header only forward
/// declares it), but mirrored exactly so `json_get_context` reports the same
/// counts and so the allocation sizes match for the benchmark comparison.
pub const Stack = extern struct {
    type: Type,
    count: c_long,
};

pub const StringData = extern struct {
    string: ?[*]u8,
    string_fill: usize,
    string_size: usize,
};

/// `struct json_stream`.
pub const Stream = extern struct {
    lineno: usize,
    stack: ?[*]Stack,
    /// `(size_t)-1` when no container is open. Increment/decrement is
    /// deliberately wrapping, matching the C original's use of unsigned
    /// overflow as the "empty stack" sentinel.
    stack_top: usize,
    stack_size: usize,
    next: Type,
    flags: c_uint,
    data: StringData,
    ntokens: usize,
    source: Source,
    alloc: Allocator,
    errmsg: [errmsg_len]u8,
};

pub const errmsg_len = 128;

pub const flag_error: c_uint = 1 << 0;
pub const flag_streaming: c_uint = 1 << 1;

/// `PDJSON_STACK_INC` — the stack grows by this many frames at a time.
pub const stack_inc: usize = 4;

/// Initial capacity of the token buffer, matching `init_string`.
pub const string_initial_size: usize = 1024;

/// The empty-stack sentinel, spelled once.
pub const stack_empty: usize = std.math.maxInt(usize);

comptime {
    // Cheap local guards. The authoritative check is scripts/abi-check.sh,
    // which diffs against the C compiler's view of the pinned header.
    std.debug.assert(@sizeOf(Type) == 4);
    std.debug.assert(@offsetOf(Stream, "lineno") == 0);
    std.debug.assert(@offsetOf(Stream, "errmsg") == @sizeOf(Stream) - errmsg_len);
}
