//! Emits the same ABI table as tools/abi_probe_c.c, computed from the Zig
//! declarations in src/abi.zig. scripts/abi-check.sh diffs the two outputs.

const std = @import("std");
const cli = @import("cli.zig");
const abi = @import("pdjson").abi;

fn field(out: anytype, comptime st: []const u8, comptime name: []const u8, offset: usize, size: usize) !void {
    try out.print(
        "  {{\"struct\": \"{s}\", \"field\": \"{s}\", \"offset\": {d}, \"size\": {d}}},\n",
        .{ st, name, offset, size },
    );
}

pub fn main() !void {
    var buf: [1 << 16]u8 = undefined;
    var stdout = cli.stdout(&buf);
    const out = stdout.w();

    try out.print("{{\n", .{});
    try out.print("  \"schema\": \"pdjson-zig/abi-layout@1\",\n", .{});
    try out.print("  \"producer\": \"zig\",\n", .{});
    try out.print("  \"pointer_size\": {d},\n", .{@sizeOf(*anyopaque)});
    try out.print("  \"size_t_size\": {d},\n", .{@sizeOf(usize)});
    try out.print("  \"enum_json_type_size\": {d},\n", .{@sizeOf(abi.Type)});
    try out.print("  \"enum_json_type_signed\": {d},\n", .{
        @as(u8, if (@typeInfo(@typeInfo(abi.Type).@"enum".tag_type).int.signedness == .signed) 1 else 0),
    });
    try out.print("  \"sizeof_json_stream\": {d},\n", .{@sizeOf(abi.Stream)});
    try out.print("  \"alignof_json_stream\": {d},\n", .{@alignOf(abi.Stream)});
    try out.print("  \"sizeof_json_source\": {d},\n", .{@sizeOf(abi.Source)});
    try out.print("  \"alignof_json_source\": {d},\n", .{@alignOf(abi.Source)});
    try out.print("  \"sizeof_json_allocator\": {d},\n", .{@sizeOf(abi.Allocator)});
    try out.print("  \"alignof_json_allocator\": {d},\n", .{@alignOf(abi.Allocator)});
    try out.print("  \"layout\": [\n", .{});

    const S = abi.Stream;
    const data_off = @offsetOf(S, "data");
    const src_off = @offsetOf(S, "source");

    try field(out, "json_stream", "lineno", @offsetOf(S, "lineno"), @sizeOf(usize));
    try field(out, "json_stream", "stack", @offsetOf(S, "stack"), @sizeOf(?[*]abi.Stack));
    try field(out, "json_stream", "stack_top", @offsetOf(S, "stack_top"), @sizeOf(usize));
    try field(out, "json_stream", "stack_size", @offsetOf(S, "stack_size"), @sizeOf(usize));
    try field(out, "json_stream", "next", @offsetOf(S, "next"), @sizeOf(abi.Type));
    try field(out, "json_stream", "flags", @offsetOf(S, "flags"), @sizeOf(c_uint));
    try field(out, "json_stream", "data", data_off, @sizeOf(abi.StringData));
    try field(out, "json_stream", "data.string", data_off + @offsetOf(abi.StringData, "string"), @sizeOf(?[*]u8));
    try field(out, "json_stream", "data.string_fill", data_off + @offsetOf(abi.StringData, "string_fill"), @sizeOf(usize));
    try field(out, "json_stream", "data.string_size", data_off + @offsetOf(abi.StringData, "string_size"), @sizeOf(usize));
    try field(out, "json_stream", "ntokens", @offsetOf(S, "ntokens"), @sizeOf(usize));
    try field(out, "json_stream", "source", src_off, @sizeOf(abi.Source));
    try field(out, "json_stream", "alloc", @offsetOf(S, "alloc"), @sizeOf(abi.Allocator));
    try field(out, "json_stream", "errmsg", @offsetOf(S, "errmsg"), abi.errmsg_len);

    const Src = abi.Source;
    const u_off = @offsetOf(Src, "source");
    try field(out, "json_source", "get", @offsetOf(Src, "get"), @sizeOf(*anyopaque));
    try field(out, "json_source", "peek", @offsetOf(Src, "peek"), @sizeOf(*anyopaque));
    try field(out, "json_source", "position", @offsetOf(Src, "position"), @sizeOf(usize));
    try field(out, "json_source", "source", u_off, @sizeOf(abi.SourceUnion));
    try field(out, "json_source", "source.stream.stream", u_off + @offsetOf(abi.StreamSource, "stream"), @sizeOf(*anyopaque));
    try field(out, "json_source", "source.buffer.buffer", u_off + @offsetOf(abi.BufferSource, "buffer"), @sizeOf(*anyopaque));
    try field(out, "json_source", "source.buffer.length", u_off + @offsetOf(abi.BufferSource, "length"), @sizeOf(usize));
    try field(out, "json_source", "source.user.ptr", u_off + @offsetOf(abi.UserSource, "ptr"), @sizeOf(*anyopaque));
    try field(out, "json_source", "source.user.get", u_off + @offsetOf(abi.UserSource, "get"), @sizeOf(*anyopaque));
    try field(out, "json_source", "source.user.peek", u_off + @offsetOf(abi.UserSource, "peek"), @sizeOf(*anyopaque));

    const A = abi.Allocator;
    try field(out, "json_allocator", "malloc", @offsetOf(A, "malloc"), @sizeOf(*anyopaque));
    try field(out, "json_allocator", "realloc", @offsetOf(A, "realloc"), @sizeOf(*anyopaque));
    try field(out, "json_allocator", "free", @offsetOf(A, "free"), @sizeOf(*anyopaque));

    try out.print("  {{\"end\": true}}\n", .{});
    try out.print("  ],\n", .{});
    try out.print("  \"enums\": [\n", .{});

    const names = [_]struct { []const u8, abi.Type }{
        .{ "JSON_ERROR", .err },
        .{ "JSON_DONE", .done },
        .{ "JSON_OBJECT", .object },
        .{ "JSON_OBJECT_END", .object_end },
        .{ "JSON_ARRAY", .array },
        .{ "JSON_ARRAY_END", .array_end },
        .{ "JSON_STRING", .string },
        .{ "JSON_NUMBER", .number },
        .{ "JSON_TRUE", .true_ },
        .{ "JSON_FALSE", .false_ },
        .{ "JSON_NULL", .null_ },
    };
    for (names) |n| {
        try out.print(
            "  {{\"enum\": \"json_type\", \"name\": \"{s}\", \"value\": {d}}},\n",
            .{ n[0], @intFromEnum(n[1]) },
        );
    }
    try out.print("  {{\"end\": true}}\n", .{});
    try out.print("  ]\n", .{});
    try out.print("}}\n", .{});
    try stdout.flush();
}
