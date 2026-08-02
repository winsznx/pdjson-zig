//! Shared plumbing for the command-line tools: stdout/stderr writers, whole
//! file reads, and a process allocator. Kept in one place so the tools
//! themselves stay about their actual job.

const std = @import("std");

pub const Io = std.Io;

pub fn io() std.Io {
    // The tools are single threaded; this avoids needing a thread pool.
    return std.Io.Threaded.global_single_threaded.io();
}

pub const Out = struct {
    file_writer: std.Io.File.Writer,

    pub fn w(self: *Out) *std.Io.Writer {
        return &self.file_writer.interface;
    }

    pub fn flush(self: *Out) !void {
        try self.file_writer.interface.flush();
    }
};

pub fn stdout(buffer: []u8) Out {
    return .{ .file_writer = std.Io.File.stdout().writer(io(), buffer) };
}

pub fn stderr(buffer: []u8) Out {
    return .{ .file_writer = std.Io.File.stderr().writer(io(), buffer) };
}

pub fn readFile(gpa: std.mem.Allocator, path: []const u8, limit: usize) ![]u8 {
    return std.Io.Dir.cwd().readFileAlloc(io(), path, gpa, .limited(limit));
}

pub fn readStdin(gpa: std.mem.Allocator) ![]u8 {
    var rbuf: [64 * 1024]u8 = undefined;
    var r = std.Io.File.stdin().reader(io(), &rbuf);
    var acc = std.Io.Writer.Allocating.init(gpa);
    errdefer acc.deinit();
    _ = r.interface.streamRemaining(&acc.writer) catch {};
    return acc.toOwnedSlice();
}

pub fn die(comptime fmt: []const u8, args: anytype) noreturn {
    var buf: [4096]u8 = undefined;
    var e = stderr(&buf);
    e.w().print(fmt ++ "\n", args) catch {};
    e.flush() catch {};
    std.process.exit(2);
}
