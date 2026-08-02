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
 *   - timing
 *   - allocation sizes (an implementation detail, covered separately by the
 *     benchmark's allocation counters)
 *
 * Usage: transcript_c <mode> [file]      (reads stdin when file is omitted)
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
    double num = json_get_number(json);
    size_t ctxn = 0;
    enum json_type ctx = json_get_context(json, &ctxn);
    const char *err = json_get_error(json);

    printf("{\"seq\":%lu,\"op\":\"%s\",\"event\":\"%s\",\"tok\":\"",
           (unsigned long)seq, op, tr_typename(event));
    tr_put_hex(stdout, tok, len);
    printf("\",\"toklen\":%lu,\"num\":\"%s\",\"line\":%lu,\"pos\":%lu,"
           "\"depth\":%lu,\"ctx\":\"%s\",\"ctxn\":%lu,\"err\":",
           (unsigned long)len,
           tr_double_bits(num),
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

/* --------------------------------------------------------------------- main */

int
main(int argc, char **argv)
{
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
    free(buf);
    return 0;
}
