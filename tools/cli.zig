//! Shared plumbing for the command-line tools: argument access, stdout/stderr
//! writers, and whole-file reads. Kept in one place so each tool stays about
//! its actual job.
//!
//! Every tool takes `std.process.Init` as its `main` parameter, which is where
//! Zig 0.16 hands over the `Io` implementation, allocators, and argv.

const std = @import("std");

pub const App = struct {
    io: std.Io,
    gpa: std.mem.Allocator,
    arena: std.mem.Allocator,
    args: []const [:0]const u8,

    pub fn init(p: std.process.Init) !App {
        const arena = p.arena.allocator();
        return .{
            .io = p.io,
            .gpa = p.gpa,
            .arena = arena,
            .args = try p.minimal.args.toSlice(arena),
        };
    }

    /// Positional argument `n` (0 = first after the program name), or `fallback`.
    pub fn arg(self: App, n: usize, fallback: []const u8) []const u8 {
        return if (self.args.len > n + 1) self.args[n + 1] else fallback;
    }

    pub fn argOpt(self: App, n: usize) ?[]const u8 {
        return if (self.args.len > n + 1) self.args[n + 1] else null;
    }

    pub fn stdout(self: App, buffer: []u8) Out {
        return .{ .file_writer = std.Io.File.stdout().writer(self.io, buffer) };
    }

    pub fn stderr(self: App, buffer: []u8) Out {
        return .{ .file_writer = std.Io.File.stderr().writer(self.io, buffer) };
    }

    pub fn readFile(self: App, path: []const u8, limit: usize) ![]u8 {
        return std.Io.Dir.cwd().readFileAlloc(self.io, path, self.gpa, .limited(limit));
    }

    pub fn readStdin(self: App) ![]u8 {
        var rbuf: [64 * 1024]u8 = undefined;
        var r = std.Io.File.stdin().reader(self.io, &rbuf);
        var acc = std.Io.Writer.Allocating.init(self.gpa);
        errdefer acc.deinit();
        _ = r.interface.streamRemaining(&acc.writer) catch {};
        return acc.toOwnedSlice();
    }

    /// Read a named file, or stdin when the name is absent.
    pub fn readInput(self: App, path: ?[]const u8, limit: usize) ![]u8 {
        if (path) |p| return self.readFile(p, limit);
        return self.readStdin();
    }

    pub fn die(self: App, comptime fmt: []const u8, args: anytype) noreturn {
        var buf: [4096]u8 = undefined;
        var e = self.stderr(&buf);
        e.w().print(fmt ++ "\n", args) catch {};
        e.flush() catch {};
        std.process.exit(2);
    }
};

pub const Out = struct {
    file_writer: std.Io.File.Writer,

    pub fn w(self: *Out) *std.Io.Writer {
        return &self.file_writer.interface;
    }

    pub fn flush(self: *Out) !void {
        try self.file_writer.interface.flush();
    }
};
