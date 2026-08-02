/* Benchmark harness for the ORIGINAL pdjson.
 *
 * tools/bench_zig.zig is the same program against the Zig library: same
 * workload files, same loop, same counters, same output format. Only the
 * parser differs.
 *
 * Emits every per-iteration sample rather than a pre-cooked average, so
 * scripts/bench.py can report a distribution and so the raw data can be
 * re-analysed without re-running.
 *
 * Usage: bench_c <workload-file> <iterations> [mode] [inner]
 *   mode:  parse (default, events only) | strings (also fetch every token)
 *   inner: parses per recorded sample. CLOCK_MONOTONIC granularity is 1us on
 *          some platforms, so a workload that parses in well under that must
 *          be batched or every sample rounds to 0. Reported so the summariser
 *          can divide it back out.
 */
/* getrusage's ru_maxrss is a BSD extension, so a bare _POSIX_C_SOURCE hides it
 * on Darwin. clock_gettime needs the POSIX level elsewhere. */
#if defined(__APPLE__)
#  define _DARWIN_C_SOURCE
#else
#  define _POSIX_C_SOURCE 200112L
#endif
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/resource.h>
#include "pdjson.h"

static unsigned long alloc_count;
static unsigned long alloc_bytes;

static void *b_malloc(size_t n)
{
    alloc_count++;
    alloc_bytes += n;
    return malloc(n);
}

static void *b_realloc(void *p, size_t n)
{
    alloc_count++;
    alloc_bytes += n;
    return realloc(p, n);
}

static void b_free(void *p) { free(p); }

static long long
now_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000000000LL + ts.tv_nsec;
}

static unsigned long
peak_rss_kb(void)
{
    struct rusage ru;
    getrusage(RUSAGE_SELF, &ru);
#if defined(__APPLE__)
    return (unsigned long)(ru.ru_maxrss / 1024); /* bytes on macOS */
#else
    return (unsigned long)ru.ru_maxrss;          /* kilobytes elsewhere */
#endif
}

/* Returns the number of events seen, which also keeps the loop from being
 * optimised away. */
static unsigned long
run_once(const char *buf, size_t len, int fetch_strings)
{
    json_stream json[1];
    json_allocator a;
    a.malloc = b_malloc;
    a.realloc = b_realloc;
    a.free = b_free;

    json_open_buffer(json, buf, len);
    json_set_allocator(json, &a);
    json_set_streaming(json, 1);

    unsigned long events = 0;
    int first = 1;
    for (;;) {
        enum json_type t = json_next(json);
        events++;
        if (fetch_strings && (t == JSON_STRING || t == JSON_NUMBER)) {
            size_t n = 0;
            const char *s = json_get_string(json, &n);
            events += (unsigned long)(s[0] != 0) + (unsigned long)n;
        }
        if (t == JSON_ERROR) break;
        if (t == JSON_DONE) {
            if (first) break;
            json_reset(json);
            first = 1;
        } else {
            first = 0;
        }
    }
    json_close(json);
    return events;
}

int
main(int argc, char **argv)
{
    if (argc < 3) {
        fprintf(stderr, "usage: %s <workload> <iterations> [parse|strings]\n", argv[0]);
        return 2;
    }
    const char *path = argv[1];
    long iterations = strtol(argv[2], NULL, 10);
    int fetch_strings = argc > 3 && strcmp(argv[3], "strings") == 0;
    long inner = argc > 4 ? strtol(argv[4], NULL, 10) : 1;
    if (inner < 1) inner = 1;

    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "cannot open %s\n", path); return 2; }
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *buf = (char *)malloc((size_t)size ? (size_t)size : 1);
    if (fread(buf, 1, (size_t)size, f) != (size_t)size && size != 0) {
        fprintf(stderr, "short read\n"); return 2;
    }
    fclose(f);

    /* Warm up: touch the input and the allocator paths so the measured
     * iterations are steady-state. Warm-up work is not recorded. */
    long long cold_start = now_ns();
    unsigned long sink = run_once(buf, (size_t)size, fetch_strings);
    long long cold_ns = now_ns() - cold_start;
    for (int i = 0; i < 4; i++) sink += run_once(buf, (size_t)size, fetch_strings);

    alloc_count = 0;
    alloc_bytes = 0;

    long long *samples = (long long *)malloc(sizeof(long long) * (size_t)iterations);
    for (long i = 0; i < iterations; i++) {
        long long t0 = now_ns();
        for (long k = 0; k < inner; k++)
            sink += run_once(buf, (size_t)size, fetch_strings);
        samples[i] = now_ns() - t0;
    }

    printf("{\"impl\":\"c\",\"workload\":\"%s\",\"mode\":\"%s\",\"bytes\":%ld,"
           "\"iterations\":%ld,\"inner\":%ld,\"cold_ns\":%lld,\"alloc_count\":%lu,"
           "\"alloc_bytes\":%lu,\"peak_rss_kb\":%lu,\"checksum\":%lu,\"samples_ns\":[",
           path, fetch_strings ? "strings" : "parse", size, iterations, inner, cold_ns,
           alloc_count / (unsigned long)(iterations * inner),
           alloc_bytes / (unsigned long)(iterations * inner),
           peak_rss_kb(), sink);
    for (long i = 0; i < iterations; i++)
        printf("%s%lld", i ? "," : "", samples[i]);
    printf("]}\n");

    free(samples);
    free(buf);
    return 0;
}
