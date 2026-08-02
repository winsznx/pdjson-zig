//! `zig build diagnose` — report the target-dependent decisions this build made.
//!
//! Two of them are invisible in the source and change observable behaviour:
//!
//!  * whether the target's C `char` is signed, which decides whether the byte
//!    0xFF is indistinguishable from EOF (upstream issue #37), and
//!  * whether the compile-time ABI contract in src/abi_contract.zig is active
//!    for this target or deferred to scripts/abi-cross-check.sh.
//!
//! This is a *report*, not an assertion. `@compileLog` would have been the
//! shorter way to surface the same facts and is the wrong tool: it fails the
//! compilation it reports on, so a build that wanted to print a diagnostic
//! could not also produce a library. A build step that runs an executable
//! prints and exits 0.

const std = @import("std");
const cli = @import("cli.zig");
const pdjson = @import("pdjson");
const contract = pdjson.abi_contract;
const builtin = @import("builtin");

const char_is_signed = @typeInfo(c_char).int.signedness == .signed;

pub fn main(p: std.process.Init) !void {
    const app = try cli.App.init(p);
    var buf: [8192]u8 = undefined;
    var stdout = app.stdout(&buf);
    const out = stdout.w();

    const json = for (app.args[1..]) |a| {
        if (std.mem.eql(u8, a, "--json")) break true;
    } else false;

    // What the library will actually do with the byte 0xFF read from a buffer.
    // Derived by asking the parser, not by restating the rule.
    var bytes = [_]u8{0xFF};
    var source: pdjson.abi.Source = .{
        .get = pdjson.parser.bufferGet,
        .peek = pdjson.parser.bufferPeek,
        .position = 0,
        .source = .{ .buffer = .{ .buffer = &bytes, .length = 1 } },
    };
    const observed_0xff = pdjson.parser.bufferPeek(&source);
    const collides_with_eof = observed_0xff == pdjson.abi.EOF;

    const mode: []const u8 = if (pdjson.parser.fix_0xff)
        "unsigned (0xFF is a distinct byte; diverges from upstream on purpose)"
    else
        "as-C (0xFF widens through c_char, reproducing upstream)";

    if (json) {
        try out.print(
            \\{{
            \\  "schema": "pdjson-zig/diagnose@1",
            \\  "target": "{s}-{s}-{s}",
            \\  "optimize": "{s}",
            \\  "c_char_is_signed": {},
            \\  "byte_0xff_peeks_as": {d},
            \\  "byte_0xff_collides_with_eof": {},
            \\  "fix_0xff_option": {},
            \\  "fix_0xff_build_flag": "-Dfix-0xff={s}",
            \\  "stack_max_option": {?d},
            \\  "abi_contract_asserted": {},
            \\  "abi_contract_fields": {d},
            \\  "abi_contract_enumerators": {d},
            \\  "sizeof_json_stream": {d}
            \\}}
            \\
        , .{
            @tagName(builtin.cpu.arch), @tagName(builtin.os.tag),   @tagName(builtin.abi),
            @tagName(builtin.mode),     char_is_signed,             observed_0xff,
            collides_with_eof,          pdjson.parser.fix_0xff,     if (pdjson.parser.fix_0xff) "true" else "false",
            pdjson.parser.stack_max,    contract.asserted,          contract.field_count,
            contract.enumerator_count,  @sizeOf(pdjson.abi.Stream),
        });
        try stdout.flush();
        return;
    }

    try out.print("pdjson-zig build diagnostics\n\n", .{});
    try out.print("  target                {s}-{s}-{s}\n", .{
        @tagName(builtin.cpu.arch), @tagName(builtin.os.tag), @tagName(builtin.abi),
    });
    try out.print("  optimize              {s}\n\n", .{@tagName(builtin.mode)});

    try out.print("0xFF / EOF compatibility (upstream issue #37)\n", .{});
    try out.print("  C `char` on target    {s}\n", .{if (char_is_signed) "signed" else "unsigned"});
    try out.print("  mode selected         {s}\n", .{mode});
    try out.print("  build option used     -Dfix-0xff={s}{s}\n", .{
        if (pdjson.parser.fix_0xff) "true" else "false",
        if (pdjson.parser.fix_0xff) "" else "  (default)",
    });
    try out.print("  peek of byte 0xFF     {d}\n", .{observed_0xff});
    if (collides_with_eof) {
        try out.print(
            \\  effect                0xFF is indistinguishable from EOF, exactly as the
            \\                        original library behaves on this target. A document
            \\                        containing 0xFF truncates rather than erroring.
            \\                        Build with -Dfix-0xff=true to separate them, at the
            \\                        cost of deliberately diverging from upstream.
            \\
        , .{});
    } else if (pdjson.parser.fix_0xff) {
        try out.print(
            \\  effect                0xFF is a distinct byte. This DIVERGES from the
            \\                        original on signed-`char` targets and is why the
            \\                        differential harness never runs with this option.
            \\
        , .{});
    } else {
        try out.print(
            \\  effect                `char` is unsigned here, so 0xFF never reached EOF
            \\                        on this target to begin with; upstream behaves the
            \\                        same way and there is nothing to reconcile.
            \\
        , .{});
    }

    try out.print("\nCompile-time C ABI contract (src/abi_contract.zig)\n", .{});
    if (contract.asserted) {
        try out.print(
            "  status                active: {d} field offsets and sizes, {d} enumerators,\n" ++
                "                        and 7 struct size/alignment values were asserted\n" ++
                "                        against the pinned header while building this binary\n",
            .{ contract.field_count, contract.enumerator_count },
        );
    } else {
        try out.print(
            \\  status                deferred: this target is not in the ABI class the
            \\                        contract was generated for, so it asserts nothing
            \\                        here. scripts/abi-cross-check.sh covers this target
            \\                        by compiling _Static_assert against the pinned
            \\                        header for it.
            \\
        , .{});
    }
    try out.print("  sizeof(json_stream)   {d}\n", .{@sizeOf(pdjson.abi.Stream)});

    try out.print("\nOther build options\n", .{});
    if (pdjson.parser.stack_max) |max| {
        try out.print("  -Dstack-max           {d}  (nesting deeper than this errors)\n", .{max});
    } else {
        try out.print("  -Dstack-max           0  (unlimited, matching upstream)\n", .{});
    }

    try stdout.flush();
}
