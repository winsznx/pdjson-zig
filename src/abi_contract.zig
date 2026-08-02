//! Asserts `abi.zig` against the pinned C header, at compile time.
//!
//! `src/abi.zig` is written by hand, deliberately: transliterating the header
//! would make the ABI match true by construction and prove nothing. The cost of
//! that choice is that the two can drift.
//!
//! `scripts/abi-check.sh` and `scripts/abi-cross-check.sh` both catch a drift,
//! but both are external — a consumer who runs `zig build` and links the
//! archive never executes either. So the layout the C compiler reads out of the
//! pinned header is generated into `abi_generated.zig`, and every offset and
//! every field size in it is checked here. A drift now fails the build that
//! produces the library, and names the field that moved.
//!
//! This is not a replacement for the scripts. It compares against a *recorded*
//! layout, which the scripts re-derive from the header itself; only they can
//! catch the generated file being stale, which is why
//! `scripts/abi-generate.sh --check` runs in the verification pipeline.

const std = @import("std");
const abi = @import("abi.zig");
const gen = @import("abi_generated.zig");

/// True when the build target is in the ABI class the contract was generated
/// for. Off-class targets (32-bit, say) are covered by
/// `scripts/abi-cross-check.sh` instead; `zig build diagnose` reports which.
pub const asserted = gen.pointer_size == @sizeOf(*anyopaque) and
    gen.size_t_size == @sizeOf(usize);

pub const field_count = gen.fields.len;
pub const enumerator_count = gen.enumerators.len;

fn Named(comptime name: []const u8) type {
    if (std.mem.eql(u8, name, "json_stream")) return abi.Stream;
    if (std.mem.eql(u8, name, "json_source")) return abi.Source;
    if (std.mem.eql(u8, name, "json_allocator")) return abi.Allocator;
    @compileError("abi_generated.zig names a struct with no Zig counterpart: " ++ name);
}

/// Every arm of an `extern union` starts at offset 0, and `@offsetOf` rejects
/// unions outright, so the union contributes nothing to a path's offset.
fn memberOffset(comptime T: type, comptime name: []const u8) usize {
    return switch (@typeInfo(T)) {
        .@"union" => 0,
        else => @offsetOf(T, name),
    };
}

fn pathOffset(comptime T: type, comptime path: []const u8) usize {
    const dot = std.mem.indexOfScalar(u8, path, '.') orelse return memberOffset(T, path);
    const head = path[0..dot];
    return memberOffset(T, head) + pathOffset(@FieldType(T, head), path[dot + 1 ..]);
}

fn PathType(comptime T: type, comptime path: []const u8) type {
    const dot = std.mem.indexOfScalar(u8, path, '.') orelse return @FieldType(T, path);
    return PathType(@FieldType(T, path[0..dot]), path[dot + 1 ..]);
}

fn enumValue(comptime name: []const u8) c_int {
    const tag: abi.Type = if (std.mem.eql(u8, name, "JSON_ERROR")) .err else if (std.mem.eql(u8, name, "JSON_DONE")) .done else if (std.mem.eql(u8, name, "JSON_OBJECT")) .object else if (std.mem.eql(u8, name, "JSON_OBJECT_END")) .object_end else if (std.mem.eql(u8, name, "JSON_ARRAY")) .array else if (std.mem.eql(u8, name, "JSON_ARRAY_END")) .array_end else if (std.mem.eql(u8, name, "JSON_STRING")) .string else if (std.mem.eql(u8, name, "JSON_NUMBER")) .number else if (std.mem.eql(u8, name, "JSON_TRUE")) .true_ else if (std.mem.eql(u8, name, "JSON_FALSE")) .false_ else if (std.mem.eql(u8, name, "JSON_NULL")) .null_ else @compileError("abi_generated.zig names an enumerator with no Zig counterpart: " ++ name);
    return @intFromEnum(tag);
}

fn expect(comptime what: []const u8, comptime want: usize, comptime got: usize) void {
    if (want != got) @compileError(std.fmt.comptimePrint(
        "C ABI drift: {s} is {d} in the pinned pdjson.h but {d} in src/abi.zig. " ++
            "Fix src/abi.zig, or if the header itself changed, re-run scripts/abi-generate.sh.",
        .{ what, want, got },
    ));
}

comptime {
    if (asserted) {
        // Resolving 27 dotted paths and 11 enumerator names by string
        // comparison costs more comptime branches than the default budget.
        @setEvalBranchQuota(20_000);

        expect("sizeof(struct json_stream)", gen.sizeof_json_stream, @sizeOf(abi.Stream));
        expect("alignof(struct json_stream)", gen.alignof_json_stream, @alignOf(abi.Stream));
        expect("sizeof(struct json_source)", gen.sizeof_json_source, @sizeOf(abi.Source));
        expect("alignof(struct json_source)", gen.alignof_json_source, @alignOf(abi.Source));
        expect("sizeof(struct json_allocator)", gen.sizeof_json_allocator, @sizeOf(abi.Allocator));
        expect("alignof(struct json_allocator)", gen.alignof_json_allocator, @alignOf(abi.Allocator));
        expect("sizeof(enum json_type)", gen.sizeof_enum_json_type, @sizeOf(abi.Type));

        const tag_signed = @typeInfo(@typeInfo(abi.Type).@"enum".tag_type).int.signedness == .signed;
        if (gen.enum_json_type_is_signed != tag_signed) @compileError(
            "C ABI drift: enum json_type signedness differs between the pinned header and src/abi.zig",
        );

        // Offsets alone are not sufficient. A field that shrinks at the end of a
        // struct can have the loss absorbed by padding, leaving sizeof and every
        // offset unchanged; that exact case slipped past an earlier version of
        // the cross-target check. Sizes are asserted for the same reason here.
        for (gen.fields) |f| {
            const T = Named(f.@"struct");
            expect("offsetof(struct " ++ f.@"struct" ++ ", " ++ f.path ++ ")", f.offset, pathOffset(T, f.path));
            expect("sizeof(struct " ++ f.@"struct" ++ "." ++ f.path ++ ")", f.size, @sizeOf(PathType(T, f.path)));
        }

        for (gen.enumerators) |e| {
            const got = enumValue(e.name);
            if (e.value != got) @compileError(std.fmt.comptimePrint(
                "C ABI drift: {s} is {d} in the pinned pdjson.h but {d} in src/abi.zig",
                .{ e.name, e.value, got },
            ));
        }
    }
}

test "the generated contract covers the whole public layout" {
    // Guards against the generator silently emitting a shorter table: a
    // contract that asserts nothing would pass just as quietly as one that
    // asserts everything.
    try std.testing.expect(field_count >= 27);
    try std.testing.expect(enumerator_count == 11);
    try std.testing.expect(asserted);
}

test "path resolution walks nested members and union arms" {
    // The resolver is comptime-only by construction; these calls are forced to
    // comptime for the same reason the contract itself is.
    try std.testing.expectEqual(
        @offsetOf(abi.Stream, "data"),
        comptime pathOffset(abi.Stream, "data.string"),
    );
    try std.testing.expectEqual(
        @offsetOf(abi.Source, "source") + @offsetOf(abi.BufferSource, "length"),
        comptime pathOffset(abi.Source, "source.buffer.length"),
    );
    try std.testing.expectEqual(usize, comptime PathType(abi.Source, "source.buffer.length"));

    // A union arm contributes no offset, so all three arms start together.
    try std.testing.expectEqual(
        comptime pathOffset(abi.Source, "source.stream.stream"),
        comptime pathOffset(abi.Source, "source.user.ptr"),
    );
}
