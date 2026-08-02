/* Reference oracle: drives the ORIGINAL pdjson and emits a deterministic
 * behaviour transcript as NDJSON.
 *
 * This program links upstream/pdjson/pdjson.c. Its counterpart,
 * tools/transcript_zig.zig, drives the Zig library through the same script and
 * must emit byte-identical output. That equality is the project's central
 * proof; everything else is corpus generation and reporting around it.
 *
 * Only values reachable through the public API are recorded. Deliberately
 * excluded:
 *   - pointer and heap addresses
 *   - the bytes of `errmsg` beyond its NUL terminator (snprintf leaves those
 *     untouched, so they are whatever was on the caller's stack)
 *   - json_get_number() when the token buffer holds no NUL within the bytes the
 *     parser actually wrote. strtod() then reads past the written region into
 *     uninitialised heap, which is upstream issue #38 and is not reproducible
 *     between runs on glibc. Recorded as null on both sides in that case.
 *   - timing
 *   - allocation sizes (an implementation detail, covered separately by the
 *     benchmark's allocation counters)
 *
 * Usage:
 *   transcript_c <mode> [file]        transcribe one input (stdin if omitted)
 *   transcript_c --batch <mode> <listfile>
 *                                     transcribe every path listed in
 *                                     <listfile>, one per line, prefixing each
 *                                     with {"input":"<path>"}. Used by the
 *                                     fuzzer so throughput is not dominated by
 *                                     process startup.
 * Modes: next nostream peek skip sep oom:<n>
 */
#define _POSIX_C_SOURCE 200112L
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pdjson.h"

#include "transcript.h"

/* ---------------------------------------------------------------- allocator */

/* Deterministic allocation-failure injection: succeed for the first
 * `alloc_budget` requests, then fail every one after that. Lets the transcript
 * cover the "out of memory" branches without relying on real exhaustion. */
static long alloc_budget = -1; /* -1 = unlimited */

static void *counting_malloc(size_t n)
{
    if (alloc_budget == 0) return NULL;
    if (alloc_budget > 0) alloc_budget--;
    return malloc(n);
}

static void *counting_realloc(void *p, size_t n)
{
    if (alloc_budget == 0) return NULL;
    if (alloc_budget > 0) alloc_budget--;
    return realloc(p, n);
}

static void counting_free(void *p) { free(p); }

/* ------------------------------------------------------------------ emitter */

static void
emit(size_t seq, const char *op, enum json_type event, json_stream *json)
{
    size_t len = 0;
    const char *tok = json_get_string(json, &len);

    /* json_get_number() is strtod() over the token buffer. That is well defined
     * only when a NUL sits inside the bytes the parser wrote; otherwise strtod
     * walks into memory nobody initialised (upstream #38). Ask for the value
     * only when it is defined -- and note the check uses nothing but the public
     * API, so both implementations apply the identical rule. */
    int num_defined = (tok != NULL) && (memchr(tok, 0, len) != NULL);
    double num = num_defined ? json_get_number(json) : 0.0;

    size_t ctxn = 0;
    enum json_type ctx = json_get_context(json, &ctxn);
    const char *err = json_get_error(json);

    printf("{\"seq\":%lu,\"op\":\"%s\",\"event\":\"%s\",\"tok\":\"",
           (unsigned long)seq, op, tr_typename(event));
    tr_put_hex(stdout, tok, len);
    printf("\",\"toklen\":%lu,\"num\":%s%s%s,\"line\":%lu,\"pos\":%lu,"
           "\"depth\":%lu,\"ctx\":\"%s\",\"ctxn\":%lu,\"err\":",
           (unsigned long)len,
           num_defined ? "\"" : "",
           num_defined ? tr_double_bits(num) : "null",
           num_defined ? "\"" : "",
           (unsigned long)json_get_lineno(json),
           (unsigned long)json_get_position(json),
           (unsigned long)json_get_depth(json),
           tr_typename(ctx),
           (unsigned long)ctxn);
    if (err == NULL) {
        printf("null");
    } else {
        printf("\"");
        tr_put_hex(stdout, err, strlen(err));
        printf("\"");
    }
    printf("}\n");
}

/* Records a raw byte read through json_source_get/peek. */
static void
emit_source(size_t seq, const char *op, int c, json_stream *json)
{
    printf("{\"seq\":%lu,\"op\":\"%s\",\"byte\":%d,\"line\":%lu,\"pos\":%lu}\n",
           (unsigned long)seq, op, c,
           (unsigned long)json_get_lineno(json),
           (unsigned long)json_get_position(json));
}

/* ---------------------------------------------------------------- transcribe */

static void
transcribe(const char *mode, const char *buf, size_t len)
{
    alloc_budget = -1;
    if (strncmp(mode, "oom:", 4) == 0)
        alloc_budget = strtol(mode + 4, NULL, 10);

    printf("{\"schema\":\"%s\",\"mode\":\"%s\",\"bytes\":%lu}\n",
           TR_SCHEMA, mode, (unsigned long)len);

    json_stream json[1];
    json_open_buffer(json, buf, len);

    if (alloc_budget >= 0) {
        json_allocator a;
        a.malloc = counting_malloc;
        a.realloc = counting_realloc;
        a.free = counting_free;
        json_set_allocator(json, &a);
    }

    int streaming = strcmp(mode, "nostream") != 0;
    json_set_streaming(json, streaming);

    size_t seq = 0;
    int first = 1;
    enum json_type type = JSON_DONE;

    while (seq < TR_MAX_RECORDS) {
        if (strcmp(mode, "peek") == 0) {
            enum json_type p = json_peek(json);
            emit(seq++, "peek", p, json);
            if (seq >= TR_MAX_RECORDS) break;
        }

        if (strcmp(mode, "skip") == 0) {
            type = json_skip(json);
            emit(seq++, "skip", type, json);
        } else {
            type = json_next(json);
            emit(seq++, "next", type, json);
        }

        if (type == JSON_ERROR) break;

        if (type == JSON_DONE) {
            if (!streaming) break;

            if (strcmp(mode, "sep") == 0) {
                /* The separator-validation pattern from upstream's README. */
                int c = '\0';
                while (json_isspace(c = json_source_peek(json))) {
                    emit_source(seq++, "peek_byte", c, json);
                    c = json_source_get(json);
                    emit_source(seq++, "get_byte", c, json);
                    if (c == '\n') break;
                    if (seq >= TR_MAX_RECORDS) break;
                }
            }

            if (first) break;
            json_reset(json);
            emit(seq++, "reset", JSON_DONE, json);
            first = 1;
        } else {
            first = 0;
        }
    }

    if (seq >= TR_MAX_RECORDS)
        printf("{\"truncated\":true}\n");
    else
        printf("{\"end\":true,\"records\":%lu}\n", (unsigned long)seq);

    json_close(json);
}

/* --------------------------------------------------------------------- main */

static int
run_batch(const char *mode, const char *listfile)
{
    FILE *lf = fopen(listfile, "r");
    if (!lf) {
        fprintf(stderr, "cannot read %s\n", listfile);
        return 2;
    }
    char line[4096];
    while (fgets(line, sizeof line, lf)) {
        size_t n = strlen(line);
        while (n && (line[n - 1] == '\n' || line[n - 1] == '\r')) line[--n] = '\0';
        if (n == 0) continue;

        char *buf = NULL;
        size_t len = 0;
        if (tr_read_file(line, &buf, &len) != 0) {
            fprintf(stderr, "cannot read %s\n", line);
            fclose(lf);
            return 2;
        }
        printf("{\"input\":\"%s\"}\n", line);
        transcribe(mode, buf, len);
        free(buf);
    }
    fclose(lf);
    return 0;
}

/* Pack format: a stream of records, each "<decimal length>\n" followed by
 * exactly that many raw bytes. Lets the fuzzer hand over thousands of cases in
 * one file, so neither process startup nor filesystem churn dominates. The
 * pack for a round is archived verbatim as the reproduction artifact. */
static int
run_pack(const char *mode, const char *packfile)
{
    char *all = NULL;
    size_t total = 0;
    if (tr_read_file(packfile, &all, &total) != 0) {
        fprintf(stderr, "cannot read %s\n", packfile);
        return 2;
    }

    size_t off = 0, index = 0;
    while (off < total) {
        size_t n = 0;
        int saw_digit = 0;
        while (off < total && all[off] >= '0' && all[off] <= '9') {
            n = n * 10 + (size_t)(all[off] - '0');
            off++;
            saw_digit = 1;
        }
        if (!saw_digit || off >= total || all[off] != '\n') break;
        off++;
        if (off + n > total) break;

        printf("{\"input\":\"pack:%lu\"}\n", (unsigned long)index++);
        transcribe(mode, all + off, n);
        off += n;
    }

    free(all);
    return 0;
}

int
main(int argc, char **argv)
{
    if (argc > 3 && strcmp(argv[1], "--batch") == 0)
        return run_batch(argv[2], argv[3]);
    if (argc > 3 && strcmp(argv[1], "--pack") == 0)
        return run_pack(argv[2], argv[3]);

    const char *mode = argc > 1 ? argv[1] : "next";
    char *buf = NULL;
    size_t len = 0;

    if (argc > 2) {
        if (tr_read_file(argv[2], &buf, &len) != 0) {
            fprintf(stderr, "cannot read %s\n", argv[2]);
            return 2;
        }
    } else if (tr_read_stdin(&buf, &len) != 0) {
        fprintf(stderr, "cannot read stdin\n");
        return 2;
    }

    transcribe(mode, buf, len);
    free(buf);
    return 0;
}
