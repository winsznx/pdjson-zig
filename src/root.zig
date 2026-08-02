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

pub const abi = @import("abi.zig");
pub const parser = @import("parser.zig");
pub const strtod = @import("strtod.zig");
pub const errmsg = @import("errmsg.zig");

pub const Parser = @import("api.zig").Parser;
pub const Event = @import("api.zig").Event;
pub const Context = @import("api.zig").Context;

comptime {
    // Emit the C ABI surface as part of the library.
    _ = @import("c_api.zig");
}

test {
    @import("std").testing.refAllDecls(@This());
    _ = @import("api.zig");
}
