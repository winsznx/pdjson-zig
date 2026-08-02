const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});

    // ReleaseSafe is the default, including for a bare `zig build`.
    //
    // This matters for honesty as much as for speed. The safety claim in
    // CLAIMS.json is that the shipped library keeps bounds and overflow checks
    // enabled at runtime; if a plain `zig build` silently produced Debug, the
    // benchmark would be measuring something nobody ships and the safety claim
    // would be about a binary nobody builds. `--release=fast` still selects
    // ReleaseFast, and `-Doptimize=` still overrides explicitly.
    const optimize: std.builtin.OptimizeMode = b.option(
        std.builtin.OptimizeMode,
        "optimize",
        "Prioritize performance, safety, or binary size",
    ) orelse switch (b.release_mode) {
        .off, .any, .safe => .ReleaseSafe,
        .fast => .ReleaseFast,
        .small => .ReleaseSmall,
    };

    const stack_max = b.option(
        usize,
        "stack-max",
        "Maximum nesting depth (0 = unlimited, matching upstream's default)",
    ) orelse 0;
    const fix_0xff = b.option(
        bool,
        "fix-0xff",
        "Treat buffer bytes as unsigned so 0xFF is not confused with EOF (diverges from upstream; see docs/upstream-bug-0xff.md)",
    ) orelse false;

    const options = b.addOptions();
    options.addOption(usize, "stack_max", stack_max);
    options.addOption(bool, "fix_0xff", fix_0xff);
    const options_mod = options.createModule();

    // ---------------------------------------------------------------- library

    const pdjson_mod = b.addModule("pdjson", .{
        .root_source_file = b.path("src/root.zig"),
        .target = target,
        .optimize = optimize,
        .link_libc = true,
    });
    pdjson_mod.addImport("pdjson_options", options_mod);

    const lib = b.addLibrary(.{
        .name = "pdjson",
        .root_module = pdjson_mod,
        .linkage = .static,
    });
    lib.installHeader(b.path("include/pdjson.h"), "pdjson.h");
    b.installArtifact(lib);

    // ------------------------------------------------------------------ tools

    const tools = [_]struct { name: []const u8, src: []const u8 }{
        .{ .name = "abi_probe_zig", .src = "tools/abi_probe.zig" },
        .{ .name = "transcript_zig", .src = "tools/transcript_zig.zig" },
        .{ .name = "bench_zig", .src = "tools/bench_zig.zig" },
    };

    for (tools) |t| {
        const mod = b.createModule(.{
            .root_source_file = b.path(t.src),
            .target = target,
            .optimize = optimize,
            .link_libc = true,
        });
        mod.addImport("pdjson", pdjson_mod);
        const exe = b.addExecutable(.{ .name = t.name, .root_module = mod });
        b.installArtifact(exe);
    }

    // ------------------------------------------------------------------ tests

    const test_step = b.step("test", "Run the Zig-native test suite");

    const unit_mod = b.createModule(.{
        .root_source_file = b.path("src/root.zig"),
        .target = target,
        .optimize = optimize,
        .link_libc = true,
    });
    unit_mod.addImport("pdjson_options", options_mod);
    const unit_tests = b.addTest(.{ .root_module = unit_mod });
    test_step.dependOn(&b.addRunArtifact(unit_tests).step);

    const port_tests = [_][]const u8{
        "tests/port/behaviour.zig",
        "tests/port/number_torture.zig",
        "tests/port/allocator_failure.zig",
        "tests/port/regressions.zig",
    };
    for (port_tests) |src| {
        const mod = b.createModule(.{
            .root_source_file = b.path(src),
            .target = target,
            .optimize = optimize,
            .link_libc = true,
        });
        mod.addImport("pdjson", pdjson_mod);
        const t = b.addTest(.{ .root_module = mod });
        test_step.dependOn(&b.addRunArtifact(t).step);
    }
}
