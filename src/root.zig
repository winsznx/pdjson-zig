//! pdjson-zig: a standalone Zig rewrite of skeeto/pdjson.
//!
//! Two entry points:
//!
//!  * `Parser` (below, from api.zig) — the Zig-native interface. Slices,
//!    optionals, error unions, defer-friendly lifetime.
//!  * The exported `json_*` C symbols in c_api.zig — a drop-in replacement for
//!    the original library, binary compatible with the pinned pdjson.h.
//!
//! Both drive the same state machine in parser.zig. No code path calls into the
//! original C library.

const std = @import("std");

pub const abi = @import("abi.zig");
pub const parser = @import("parser.zig");
pub const strtod = @import("strtod.zig");
pub const errmsg = @import("errmsg.zig");

pub const Parser = @import("api.zig").Parser;
pub const Event = @import("api.zig").Event;
pub const Context = @import("api.zig").Context;
pub const Error = @import("api.zig").Error;

/// A minimal panic handler.
///
/// The default pulls in std's unwinder, DWARF reader and symbol tables, which
/// took the static archive to 4.6 MB and dragged std.debug, std.sort and
/// std.Io into a library whose whole job is to parse bytes. Aborting is the
/// right behaviour for a C-consumable library anyway: there is no Zig caller to
/// catch anything.
///
/// This is a backstop, not an error path. The parser reports malformed input
/// through JSON_ERROR and reports allocation failure through "out of memory";
/// reaching here would mean a bug in the port, not bad input. The
/// no-panic-on-untrusted-input property is covered by
/// tests/port/regressions.zig, which drives 20,000 random byte strings through
/// the parser.
extern "c" fn write(fd: c_int, buf: [*]const u8, n: usize) isize;
extern "c" fn abort() noreturn;

pub const panic = std.debug.FullPanic(struct {
    fn handler(msg: []const u8, _: ?usize) noreturn {
        @branchHint(.cold);
        _ = write(2, "pdjson-zig: panic: ", 19);
        _ = write(2, msg.ptr, msg.len);
        _ = write(2, "\n", 1);
        abort();
    }
}.handler);

comptime {
    // Emit the C ABI surface as part of the library.
    _ = @import("c_api.zig");
}

test {
    @import("std").testing.refAllDecls(@This());
    _ = @import("api.zig");
}
