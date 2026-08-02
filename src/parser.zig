//! The pdjson streaming parser, rewritten in Zig.
//!
//! Shape of the port
//! -----------------
//! The original is an event-pull parser: each `json_next()` call consumes just
//! enough input to produce one event, keeping only an explicit container stack
//! and one token buffer. That design is reproduced deliberately — it is what
//! gives the library its bounded memory profile — but the code below is
//! organised around Zig control flow rather than the original's C idioms:
//! optionals and `bool` success values instead of sentinel ints, exhaustive
//! `switch` over a real enum instead of fallthrough chains, explicit wrapping
//! operators where the original relies on unsigned overflow.
//!
//! What is preserved exactly
//! -------------------------
//! Everything a caller can observe through the public header: the event
//! sequence, the bytes in the token buffer, the diagnostic strings, `lineno`,
//! `source.position`, depth and context, the error-latching rule, and the
//! layout of `struct json_stream` itself.
//!
//! Deliberate bug-compatibility
//! ----------------------------
//! `Source.bufferPeek` loads through the platform's `char` signedness, exactly
//! as the original's `buffer_peek` does. On targets where `char` is signed that
//! makes a 0xFF input byte indistinguishable from EOF. That is an upstream
//! defect (docs/upstream-bug-0xff.md), and it is reproduced here rather than
//! silently fixed, because the claim this project makes is behavioural
//! equivalence with the pinned original. `build -Dfix-0xff=true` opts into the
//! corrected behaviour; see DECISIONS.md D-07.

const std = @import("std");
const builtin = @import("builtin");
const abi = @import("abi.zig");
const errmsg = @import("errmsg.zig");
const strtod_mod = @import("strtod.zig");
const options = @import("pdjson_options");

const Stream = abi.Stream;
const Type = abi.Type;
const EOF = abi.EOF;

/// Optional nesting limit. Upstream leaves `PDJSON_STACK_MAX` undefined, so the
/// default here is "no limit" to match. Set `-Dstack-max=N` to enforce one; the
/// diagnostic then matches the original's `#ifdef` branch verbatim.
pub const stack_max: ?usize = if (options.stack_max == 0) null else options.stack_max;

/// When true, the buffer source reports bytes as unsigned, so 0xFF is a byte
/// rather than EOF. Off by default: see the module comment.
pub const fix_0xff: bool = options.fix_0xff;

// ---------------------------------------------------------------------------
// Allocator boundary
//
// `json_allocator` is a plain C interface returning `void *`, so converting its
// results to typed pointers is unavoidable. These three functions are the only
// place in the parser that casts a pointer, which keeps the audit surface for
// scripts/safety-scan.sh to a single, reviewable spot. malloc/realloc are
// specified to return storage suitably aligned for any object type, which is
// what makes the @alignCast on the stack allocation sound.
// ---------------------------------------------------------------------------

fn cMalloc(self: *Stream, size: usize) ?[*]u8 {
    const f = self.alloc.malloc orelse return null;
    const p = f(size) orelse return null;
    return @ptrCast(p); // u8 has alignment 1; no @alignCast needed.
}

fn cRealloc(self: *Stream, ptr: ?*anyopaque, size: usize) ?*anyopaque {
    const f = self.alloc.realloc orelse return null;
    return f(ptr, size);
}

fn cFree(self: *Stream, ptr: ?*anyopaque) void {
    const f = self.alloc.free orelse return;
    f(ptr);
}

// ---------------------------------------------------------------------------
// Diagnostics
// ---------------------------------------------------------------------------

/// Begin recording a diagnostic, or return null if one is already latched.
///
/// The original's `json_error` macro is wrapped in
/// `if (!(json->flags & JSON_FLAG_ERROR))`, so only the *first* error on a
/// stream is ever stored. Later failures set no message and do not overwrite.
fn beginError(self: *Stream) ?*[abi.errmsg_len]u8 {
    if (self.flags & abi.flag_error != 0) return null;
    self.flags |= abi.flag_error;
    return &self.errmsg;
}

fn errStr(self: *Stream, msg: []const u8) void {
    var b = errmsg.Builder.init(beginError(self) orelse return);
    b.str(msg);
    b.finish();
}

/// `json_error(json, "<pre>%c<post>", c)`
fn errChar(self: *Stream, pre: []const u8, c: c_int, post: []const u8) void {
    var b = errmsg.Builder.init(beginError(self) orelse return);
    b.str(pre);
    b.char(c);
    b.str(post);
    b.finish();
}

fn errHex(self: *Stream, pre: []const u8, v: c_long, width: usize, post: []const u8) void {
    var b = errmsg.Builder.init(beginError(self) orelse return);
    b.str(pre);
    b.hex(v, width);
    b.str(post);
    b.finish();
}

fn outOfMemory(self: *Stream) void {
    errStr(self, "out of memory");
}

// ---------------------------------------------------------------------------
// Source adapters
// ---------------------------------------------------------------------------

extern "c" fn fgetc(stream: ?*anyopaque) c_int;
extern "c" fn ungetc(c: c_int, stream: ?*anyopaque) c_int;

/// Load a byte the way C would through a `const char *`.
///
/// This is the crux of the 0xFF divergence. The original indexes a
/// `const char *`, so on a signed-`char` target the byte 0xFF widens to -1,
/// which is EOF. `c_char` carries the target's actual `char` signedness, so
/// this reproduces the original on every platform rather than hard-coding one.
fn byteAsC(byte: u8) c_int {
    if (fix_0xff) return byte;
    return @as(c_char, @bitCast(byte));
}

pub fn bufferPeek(source: *abi.Source) callconv(.c) c_int {
    const b = source.source.buffer;
    if (source.position < b.length) {
        const ptr = b.buffer orelse return EOF;
        return byteAsC(ptr[source.position]);
    }
    return EOF;
}

pub fn bufferGet(source: *abi.Source) callconv(.c) c_int {
    const peek = source.peek orelse return EOF;
    const c = peek(source);
    if (c != EOF) source.position +%= 1;
    return c;
}

pub fn streamGet(source: *abi.Source) callconv(.c) c_int {
    const c = fgetc(source.source.stream.stream);
    if (c != EOF) source.position +%= 1;
    return c;
}

pub fn streamPeek(source: *abi.Source) callconv(.c) c_int {
    const c = fgetc(source.source.stream.stream);
    _ = ungetc(c, source.source.stream.stream);
    return c;
}

pub fn userGet(source: *abi.Source) callconv(.c) c_int {
    const get = source.source.user.get orelse return EOF;
    const c = get(source.source.user.ptr);
    if (c != EOF) source.position +%= 1;
    return c;
}

pub fn userPeek(source: *abi.Source) callconv(.c) c_int {
    const peek = source.source.user.peek orelse return EOF;
    return peek(source.source.user.ptr);
}

/// A null function pointer means the stream was never opened. The original
/// would dereference it; reporting EOF keeps the port total on input that is
/// undefined behaviour in C, so it cannot diverge on any defined program.
fn srcGet(self: *Stream) c_int {
    const f = self.source.get orelse return EOF;
    return f(&self.source);
}

fn srcPeek(self: *Stream) c_int {
    const f = self.source.peek orelse return EOF;
    return f(&self.source);
}

// ---------------------------------------------------------------------------
// Small helpers mirroring the original's C library usage
// ---------------------------------------------------------------------------

fn isDigit(c: c_int) bool {
    return c >= '0' and c <= '9';
}

pub fn isSpace(c: c_int) bool {
    return c == 0x09 or c == 0x0a or c == 0x0d or c == 0x20;
}

/// `strchr(set, c) != NULL`.
///
/// Reproduces the detail that C's `strchr` also matches the terminating NUL,
/// so `strchr(".eE", 0)` is non-null. The original relies on this by accident
/// when a number is followed by an embedded NUL byte; the resulting control
/// flow happens to reach the same event, and this keeps it that way.
fn strchrFound(set: []const u8, c: c_int) bool {
    const ch: u8 = @truncate(@as(c_uint, @bitCast(c)));
    if (ch == 0) return true;
    return std.mem.indexOfScalar(u8, set, ch) != null;
}

fn asByte(c: c_int) u8 {
    return @truncate(@as(c_uint, @bitCast(c)));
}

// ---------------------------------------------------------------------------
// Stack and token buffer
// ---------------------------------------------------------------------------

fn push(self: *Stream, t: Type) Type {
    // Wrapping is intentional: stack_top is SIZE_MAX when empty.
    self.stack_top +%= 1;

    if (stack_max) |max| {
        if (self.stack_top > max) {
            errStr(self, "maximum depth of nesting reached");
            return .err;
        }
    }

    if (self.stack_top >= self.stack_size) {
        const frames = self.stack_size + abi.stack_inc;
        const size = std.math.mul(usize, frames, @sizeOf(abi.Stack)) catch {
            // A request this large cannot be satisfied; the original would
            // wrap and hand realloc a bogus size. Reporting OOM is the same
            // observable event without the undefined behaviour.
            outOfMemory(self);
            return .err;
        };
        const p = cRealloc(self, @ptrCast(self.stack), size) orelse {
            outOfMemory(self);
            return .err;
        };
        self.stack_size += abi.stack_inc;
        self.stack = @ptrCast(@alignCast(p));
    }

    const stack = self.stack orelse {
        outOfMemory(self);
        return .err;
    };
    stack[self.stack_top] = .{ .type = t, .count = 0 };
    return t;
}

/// `c` is assumed not to be EOF, matching the original's note.
fn pop(self: *Stream, c: c_int, expected: Type) Type {
    const stack = self.stack orelse {
        errChar(self, "unexpected byte '", c, "'");
        return .err;
    };
    if (stack[self.stack_top].type != expected) {
        errChar(self, "unexpected byte '", c, "'");
        return .err;
    }
    self.stack_top -%= 1;
    return if (expected == .array) .array_end else .object_end;
}

fn pushchar(self: *Stream, c: c_int) bool {
    if (self.data.string_fill == self.data.string_size) {
        const size = std.math.mul(usize, self.data.string_size, 2) catch {
            outOfMemory(self);
            return false;
        };
        const p = cRealloc(self, @ptrCast(self.data.string), size) orelse {
            outOfMemory(self);
            return false;
        };
        self.data.string_size = size;
        self.data.string = @ptrCast(p);
    }
    const s = self.data.string orelse {
        outOfMemory(self);
        return false;
    };
    s[self.data.string_fill] = asByte(c);
    self.data.string_fill += 1;
    return true;
}

fn initString(self: *Stream) bool {
    self.data.string_fill = 0;
    if (self.data.string == null) {
        self.data.string_size = abi.string_initial_size;
        self.data.string = cMalloc(self, self.data.string_size) orelse {
            outOfMemory(self);
            return false;
        };
    }
    const s = self.data.string orelse {
        outOfMemory(self);
        return false;
    };
    s[0] = 0;
    return true;
}

// ---------------------------------------------------------------------------
// Unicode
// ---------------------------------------------------------------------------

fn encodeUtf8(self: *Stream, cp: c_ulong) bool {
    if (cp < 0x80) {
        return pushchar(self, @intCast(cp));
    } else if (cp < 0x800) {
        return pushchar(self, @intCast(cp >> 6 & 0x1F | 0xC0)) and
            pushchar(self, @intCast(cp >> 0 & 0x3F | 0x80));
    } else if (cp < 0x10000) {
        if (cp >= 0xd800 and cp <= 0xdfff) {
            errHex(self, "invalid codepoint ", @bitCast(cp), 6, "");
            return false;
        }
        return pushchar(self, @intCast(cp >> 12 & 0x0F | 0xE0)) and
            pushchar(self, @intCast(cp >> 6 & 0x3F | 0x80)) and
            pushchar(self, @intCast(cp >> 0 & 0x3F | 0x80));
    } else if (cp < 0x110000) {
        return pushchar(self, @intCast(cp >> 18 & 0x07 | 0xF0)) and
            pushchar(self, @intCast(cp >> 12 & 0x3F | 0x80)) and
            pushchar(self, @intCast(cp >> 6 & 0x3F | 0x80)) and
            pushchar(self, @intCast(cp >> 0 & 0x3F | 0x80));
    } else {
        errHex(self, "unable to encode ", @bitCast(cp), 6, " as UTF-8");
        return false;
    }
}

fn hexchar(c: c_int) ?c_long {
    return switch (c) {
        '0'...'9' => c - '0',
        'a'...'f' => c - 'a' + 10,
        'A'...'F' => c - 'A' + 10,
        else => null,
    };
}

fn readUnicodeCp(self: *Stream) ?c_long {
    var cp: c_long = 0;
    for (0..4) |_| {
        const c = srcGet(self);
        if (c == EOF) {
            errStr(self, "unterminated string literal in Unicode");
            return null;
        }
        const hc = hexchar(c) orelse {
            errChar(self, "invalid escape Unicode byte '", c, "'");
            return null;
        };
        cp = cp * 16 + hc;
    }
    return cp;
}

fn readUnicode(self: *Stream) bool {
    var cp = readUnicodeCp(self) orelse return false;

    if (cp >= 0xd800 and cp <= 0xdbff) {
        // High half of a surrogate pair; the low half must follow immediately.
        const h = cp;

        var c = srcGet(self);
        if (c == EOF) {
            errStr(self, "unterminated string literal in Unicode");
            return false;
        } else if (c != '\\') {
            errChar(self, "invalid continuation for surrogate pair '", c, "', expected '\\'");
            return false;
        }

        c = srcGet(self);
        if (c == EOF) {
            errStr(self, "unterminated string literal in Unicode");
            return false;
        } else if (c != 'u') {
            errChar(self, "invalid continuation for surrogate pair '", c, "', expected 'u'");
            return false;
        }

        const l = readUnicodeCp(self) orelse return false;
        if (l < 0xdc00 or l > 0xdfff) {
            errHex(self, "surrogate pair continuation \\u", l, 4, " out of range (dc00-dfff)");
            return false;
        }

        cp = ((h - 0xd800) * 0x400) + ((l - 0xdc00) + 0x10000);
    } else if (cp >= 0xdc00 and cp <= 0xdfff) {
        errHex(self, "dangling surrogate \\u", cp, 4, "");
        return false;
    }

    return encodeUtf8(self, @bitCast(cp));
}

fn readEscaped(self: *Stream) bool {
    const c = srcGet(self);
    if (c == EOF) {
        errStr(self, "unterminated string literal in escape");
        return false;
    }
    if (c == 'u') return readUnicode(self);

    const decoded: c_int = switch (c) {
        '\\' => '\\',
        'b' => 0x08,
        'f' => 0x0c,
        'n' => 0x0a,
        'r' => 0x0d,
        't' => 0x09,
        '/' => '/',
        '"' => '"',
        else => {
            errChar(self, "invalid escaped byte '", c, "'");
            return false;
        },
    };
    return pushchar(self, decoded);
}

fn charNeedsEscaping(c: c_int) bool {
    return c >= 0 and (c < 0x20 or c == 0x22 or c == 0x5c);
}

fn utf8SeqLength(byte: u8) usize {
    return switch (byte) {
        0x00...0x7F => 1,
        // Continuation byte in leading position.
        0x80...0xBF => 0,
        // Overlong encoding of an ASCII byte.
        0xC0, 0xC1 => 0,
        0xC2...0xDF => 2,
        0xE0...0xEF => 3,
        0xF0...0xF4 => 4,
        // Start of a restricted 4-, 5- or 6-byte sequence, or invalid.
        0xF5...0xFF => 0,
    };
}

fn isLegalUtf8(bytes: []const u8) bool {
    if (bytes.len == 0 or bytes.len > 4) return false;

    if (bytes.len >= 4) {
        const a = bytes[3];
        if (a < 0x80 or a > 0xBF) return false;
    }
    if (bytes.len >= 3) {
        const a = bytes[2];
        if (a < 0x80 or a > 0xBF) return false;
    }
    if (bytes.len >= 2) {
        const a = bytes[1];
        switch (bytes[0]) {
            0xE0 => if (a < 0xA0 or a > 0xBF) return false,
            0xED => if (a < 0x80 or a > 0x9F) return false,
            0xF0 => if (a < 0x90 or a > 0xBF) return false,
            0xF4 => if (a < 0x80 or a > 0x8F) return false,
            else => if (a < 0x80 or a > 0xBF) return false,
        }
    }
    if (bytes[0] >= 0x80 and bytes[0] < 0xC2) return false;
    return bytes[0] <= 0xF4;
}

fn readUtf8(self: *Stream, next_char: c_int) bool {
    const count = utf8SeqLength(asByte(next_char));
    if (count == 0) {
        errStr(self, "invalid UTF-8 character");
        return false;
    }

    // The original does not check for EOF while filling this buffer: a short
    // read stores (char)EOF == 0xFF, which is_legal_utf8 then rejects. Reading
    // the same way keeps the resulting diagnostic and position identical.
    var buffer: [4]u8 = @splat(0);
    buffer[0] = asByte(next_char);
    for (1..count) |i| buffer[i] = asByte(srcGet(self));

    if (!isLegalUtf8(buffer[0..count])) {
        errStr(self, "invalid UTF-8 text");
        return false;
    }

    for (buffer[0..count]) |b| {
        if (!pushchar(self, b)) return false;
    }
    return true;
}

// ---------------------------------------------------------------------------
// Token readers
// ---------------------------------------------------------------------------

fn readString(self: *Stream) Type {
    if (!initString(self)) return .err;
    while (true) {
        const c = srcGet(self);
        if (c == EOF) {
            errStr(self, "unterminated string literal");
            return .err;
        } else if (c == '"') {
            return if (pushchar(self, 0)) .string else .err;
        } else if (c == '\\') {
            if (!readEscaped(self)) return .err;
        } else if (@as(c_uint, @bitCast(c)) >= 0x80) {
            if (!readUtf8(self, c)) return .err;
        } else {
            if (charNeedsEscaping(c)) {
                errStr(self, "unescaped control character in string");
                return .err;
            }
            if (!pushchar(self, c)) return .err;
        }
    }
}

fn readDigits(self: *Stream) bool {
    var nread: usize = 0;
    while (true) {
        const c = srcPeek(self);
        if (!isDigit(c)) {
            if (nread == 0) {
                if (c != EOF) {
                    errChar(self, "expected digit instead of byte '", c, "'");
                } else {
                    errStr(self, "expected digit instead of end of text");
                }
                return false;
            }
            return true;
        }
        if (!pushchar(self, srcGet(self))) return false;
        nread += 1;
    }
}

fn errInNumber(self: *Stream, c: c_int) void {
    if (c != EOF) {
        errChar(self, "unexpected byte '", c, "' in number");
    } else {
        errStr(self, "unexpected end of text in number");
    }
}

/// The original expresses the leading `-` case by recursing once into itself.
/// Flattened here into a single pass; the recursive call's body is exactly the
/// integer-part handling plus the shared fraction/exponent tail below.
fn readNumber(self: *Stream, first: c_int) Type {
    if (!pushchar(self, first)) return .err;

    var c = first;
    if (c == '-') {
        c = srcGet(self);
        if (!isDigit(c)) {
            errInNumber(self, c);
            return .err;
        }
        if (!pushchar(self, c)) return .err;
    }

    if (strchrFound("123456789", c)) {
        if (isDigit(srcPeek(self))) {
            if (!readDigits(self)) return .err;
        }
    }

    // Up to the decimal point or exponent has been read.
    c = srcPeek(self);
    if (!strchrFound(".eE", c)) {
        return if (pushchar(self, 0)) .number else .err;
    }

    if (c == '.') {
        _ = srcGet(self);
        if (!pushchar(self, c)) return .err;
        if (!readDigits(self)) return .err;
    }

    c = srcPeek(self);
    if (c == 'e' or c == 'E') {
        _ = srcGet(self);
        if (!pushchar(self, c)) return .err;
        c = srcPeek(self);
        if (c == '+' or c == '-') {
            _ = srcGet(self);
            if (!pushchar(self, c)) return .err;
            if (!readDigits(self)) return .err;
        } else if (isDigit(c)) {
            if (!readDigits(self)) return .err;
        } else {
            errInNumber(self, c);
            return .err;
        }
    }

    return if (pushchar(self, 0)) .number else .err;
}

fn isMatch(self: *Stream, pattern: []const u8, t: Type) Type {
    for (pattern) |p| {
        const c = srcGet(self);
        if (c != p) {
            if (beginError(self)) |buf| {
                var b = errmsg.Builder.init(buf);
                b.str("expected '");
                b.char(p);
                if (c != EOF) {
                    b.str("' instead of byte '");
                    b.char(c);
                    b.str("'");
                } else {
                    b.str("' instead of end of text");
                }
                b.finish();
            }
            return .err;
        }
    }
    return t;
}

/// The next non-whitespace byte, counting newlines.
fn nextChar(self: *Stream) c_int {
    while (true) {
        const c = srcGet(self);
        if (!isSpace(c)) return c;
        if (c == '\n') self.lineno +%= 1;
    }
}

fn readValue(self: *Stream, c: c_int) Type {
    self.ntokens +%= 1;
    return switch (c) {
        EOF => {
            errStr(self, "unexpected end of text");
            return .err;
        },
        '{' => push(self, .object),
        '[' => push(self, .array),
        '"' => readString(self),
        'n' => isMatch(self, "ull", .null_),
        'f' => isMatch(self, "alse", .false_),
        't' => isMatch(self, "rue", .true_),
        '0'...'9', '-' => blk: {
            if (!initString(self)) break :blk .err;
            break :blk readNumber(self, c);
        },
        else => {
            errChar(self, "unexpected byte '", c, "' in value");
            return .err;
        },
    };
}

// ---------------------------------------------------------------------------
// Public operations
// ---------------------------------------------------------------------------

pub fn init(self: *Stream) void {
    self.lineno = 1;
    self.flags = abi.flag_streaming;
    self.errmsg[0] = 0;
    self.ntokens = 0;
    self.next = .none;

    self.stack = null;
    self.stack_top = abi.stack_empty;
    self.stack_size = 0;

    self.data.string = null;
    self.data.string_size = 0;
    self.data.string_fill = 0;
    self.source.position = 0;

    self.alloc = .{
        .malloc = defaultMalloc,
        .realloc = defaultRealloc,
        .free = defaultFree,
    };
}

extern "c" fn malloc(size: usize) callconv(.c) ?*anyopaque;
extern "c" fn realloc(ptr: ?*anyopaque, size: usize) callconv(.c) ?*anyopaque;
extern "c" fn free(ptr: ?*anyopaque) callconv(.c) void;

fn defaultMalloc(size: usize) callconv(.c) ?*anyopaque {
    return malloc(size);
}
fn defaultRealloc(ptr: ?*anyopaque, size: usize) callconv(.c) ?*anyopaque {
    return realloc(ptr, size);
}
fn defaultFree(ptr: ?*anyopaque) callconv(.c) void {
    free(ptr);
}

pub fn nextEvent(self: *Stream) Type {
    if (self.flags & abi.flag_error != 0) return .err;

    if (self.next != .none) {
        const buffered = self.next;
        self.next = .none;
        return buffered;
    }

    if (self.ntokens > 0 and self.stack_top == abi.stack_empty) {
        // In streaming mode trailing whitespace is deliberately left in the
        // source so a caller can inspect the separator between values with
        // json_source_get/peek; it is then skipped as leading whitespace of
        // the next value.
        if (self.flags & abi.flag_streaming == 0) {
            var c: c_int = undefined;
            while (true) {
                c = srcPeek(self);
                if (isSpace(c)) c = srcGet(self);
                if (!isSpace(c)) break;
            }
            if (c != EOF) {
                errChar(self, "expected end of text instead of byte '", c, "'");
                return .err;
            }
        }
        return .done;
    }

    const c = nextChar(self);

    if (self.stack_top == abi.stack_empty) {
        if (c == EOF and self.flags & abi.flag_streaming != 0) return .done;
        return readValue(self, c);
    }

    const stack = self.stack orelse {
        errStr(self, "invalid parser state");
        return .err;
    };
    const frame = &stack[self.stack_top];

    switch (frame.type) {
        .array => {
            if (frame.count == 0) {
                if (c == ']') return pop(self, c, .array);
                frame.count +%= 1;
                return readValue(self, c);
            } else if (c == ',') {
                frame.count +%= 1;
                return readValue(self, nextChar(self));
            } else if (c == ']') {
                return pop(self, c, .array);
            } else {
                if (c != EOF) {
                    errChar(self, "unexpected byte '", c, "'");
                } else {
                    errStr(self, "unexpected end of text");
                }
                return .err;
            }
        },
        .object => {
            if (frame.count == 0) {
                if (c == '}') return pop(self, c, .object);

                // No member name/value pairs yet.
                const value = readValue(self, c);
                if (value != .string) {
                    if (value != .err) errStr(self, "expected member name or '}'");
                    return .err;
                }
                frame.count +%= 1;
                return value;
            } else if (@rem(frame.count, 2) == 0) {
                // Expecting a comma followed by a member name.
                if (c != ',' and c != '}') {
                    errStr(self, "expected ',' or '}' after member value");
                    return .err;
                } else if (c == '}') {
                    return pop(self, c, .object);
                } else {
                    const value = readValue(self, nextChar(self));
                    if (value != .string) {
                        if (value != .err) errStr(self, "expected member name");
                        return .err;
                    }
                    frame.count +%= 1;
                    return value;
                }
            } else {
                // Expecting a colon followed by a value.
                if (c != ':') {
                    errStr(self, "expected ':' after member name");
                    return .err;
                }
                frame.count +%= 1;
                return readValue(self, nextChar(self));
            }
        },
        else => {
            errStr(self, "invalid parser state");
            return .err;
        },
    }
}

pub fn peekEvent(self: *Stream) Type {
    if (self.next != .none) return self.next;
    self.next = nextEvent(self);
    return self.next;
}

/// Note what this does *not* touch: `next` keeps any peeked event, and the
/// token buffer keeps its contents. Both match the original, and both are
/// observable — see tests/port/reset_semantics.zig.
pub fn reset(self: *Stream) void {
    self.stack_top = abi.stack_empty;
    self.ntokens = 0;
    self.flags &= ~abi.flag_error;
    self.errmsg[0] = 0;
}

pub fn skip(self: *Stream) Type {
    const t = nextEvent(self);
    var cnt_arr: usize = 0;
    var cnt_obj: usize = 0;

    var current = t;
    while (true) {
        if (current == .err or current == .done) return current;

        if (current == .array) {
            cnt_arr += 1;
        } else if (current == .array_end and cnt_arr > 0) {
            cnt_arr -= 1;
        } else if (current == .object) {
            cnt_obj += 1;
        } else if (current == .object_end and cnt_obj > 0) {
            cnt_obj -= 1;
        }

        if (cnt_arr == 0 and cnt_obj == 0) break;
        current = nextEvent(self);
    }

    return t;
}

pub fn skipUntil(self: *Stream, t: Type) Type {
    while (true) {
        const s = skip(self);
        if (s == .err or s == .done) return s;
        if (s == t) break;
    }
    return t;
}

/// The raw token buffer pointer, or null when nothing has been read yet.
/// `json_get_string` substitutes the empty string in that case.
pub fn getStringPtr(self: *Stream, length: ?*usize) ?[*]const u8 {
    if (length) |l| l.* = self.data.string_fill;
    return self.data.string;
}

/// Zig-native view of the current token: exact bytes, embedded NULs included.
pub fn getStringSlice(self: *Stream) []const u8 {
    const s = self.data.string orelse return "";
    return s[0..self.data.string_fill];
}

pub fn getNumber(self: *Stream) f64 {
    const s = self.data.string orelse return 0;
    // The buffer is NUL terminated by construction: init_string writes [0] = 0
    // and both token readers push a terminator before returning. The scan is
    // still bounded by the allocation so a corrupted struct cannot walk off
    // the end -- strictly safer than the original's unbounded strtod.
    const limit = self.data.string_size;
    var n: usize = 0;
    while (n < limit and s[n] != 0) n += 1;
    return strtod_mod.value(s[0..n]);
}

pub fn hasError(self: *Stream) bool {
    return self.flags & abi.flag_error != 0;
}

/// The latched diagnostic as a slice, stopping at the NUL the way a C caller
/// would see it. Note that a `%c` conversion of byte 0 can place that NUL
/// mid-message; see errmsg.zig.
pub fn getErrorSlice(self: *Stream) ?[]const u8 {
    if (!hasError(self)) return null;
    return std.mem.sliceTo(&self.errmsg, 0);
}

/// The full `errmsg` field including any bytes after an embedded NUL. Used by
/// the transcript oracle so the differential comparison covers what C callers
/// cannot see through `char *` but which still lives in the public struct.
pub fn getErrorRaw(self: *Stream) ?[]const u8 {
    if (!hasError(self)) return null;
    return &self.errmsg;
}

pub fn getDepth(self: *Stream) usize {
    return self.stack_top +% 1;
}

pub fn getContext(self: *Stream, count: ?*usize) Type {
    if (self.stack_top == abi.stack_empty) return .done;
    const stack = self.stack orelse return .done;
    if (count) |c| {
        const widened: isize = stack[self.stack_top].count;
        c.* = @bitCast(widened);
    }
    return stack[self.stack_top].type;
}

pub fn sourceGet(self: *Stream) c_int {
    const c = srcGet(self);
    if (c == '\n') self.lineno +%= 1;
    return c;
}

pub fn sourcePeek(self: *Stream) c_int {
    return srcPeek(self);
}

pub fn openBuffer(self: *Stream, buffer: ?[*]const u8, size: usize) void {
    init(self);
    self.source.get = bufferGet;
    self.source.peek = bufferPeek;
    self.source.source = .{ .buffer = .{ .buffer = buffer, .length = size } };
}

pub fn openStream(self: *Stream, file: ?*anyopaque) void {
    init(self);
    self.source.get = streamGet;
    self.source.peek = streamPeek;
    self.source.source = .{ .stream = .{ .stream = file } };
}

pub fn openUser(self: *Stream, get: ?abi.UserIo, peek: ?abi.UserIo, user: ?*anyopaque) void {
    init(self);
    self.source.get = userGet;
    self.source.peek = userPeek;
    self.source.source = .{ .user = .{ .ptr = user, .get = get, .peek = peek } };
}

pub fn setAllocator(self: *Stream, a: *const abi.Allocator) void {
    self.alloc = a.*;
}

pub fn setStreaming(self: *Stream, streaming: bool) void {
    if (streaming) {
        self.flags |= abi.flag_streaming;
    } else {
        self.flags &= ~abi.flag_streaming;
    }
}

pub fn close(self: *Stream) void {
    cFree(self, @ptrCast(self.stack));
    cFree(self, @ptrCast(self.data.string));
}
