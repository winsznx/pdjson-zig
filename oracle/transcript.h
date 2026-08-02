/* Shared formatting for the C oracle. The Zig transcriber reimplements the
 * same encoding; scripts/oracle-determinism.sh proves the C side is
 * reproducible and the differential harness proves the two agree. */
#ifndef PDJSON_ZIG_TRANSCRIPT_H
#define PDJSON_ZIG_TRANSCRIPT_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Bump on any change to the record shape. Both implementations assert on it. */
#define TR_SCHEMA "pdjson-zig/transcript@2"

/* Upper bound on emitted records, so a pathological input cannot hang the
 * harness. Reaching it emits {"truncated":true} rather than pretending the
 * run completed. */
#define TR_MAX_RECORDS 200000

static const char *
tr_typename(enum json_type t)
{
    switch (t) {
    case JSON_ERROR:      return "ERROR";
    case JSON_DONE:       return "DONE";
    case JSON_OBJECT:     return "OBJECT";
    case JSON_OBJECT_END: return "OBJECT_END";
    case JSON_ARRAY:      return "ARRAY";
    case JSON_ARRAY_END:  return "ARRAY_END";
    case JSON_STRING:     return "STRING";
    case JSON_NUMBER:     return "NUMBER";
    case JSON_TRUE:       return "TRUE";
    case JSON_FALSE:      return "FALSE";
    case JSON_NULL:       return "NULL";
    }
    return "NONE"; /* (enum json_type)0, the "nothing buffered" sentinel */
}

static void
tr_put_hex(FILE *out, const char *bytes, size_t n)
{
    static const char digits[] = "0123456789abcdef";
    for (size_t i = 0; i < n; i++) {
        unsigned char b = (unsigned char)bytes[i];
        fputc(digits[b >> 4], out);
        fputc(digits[b & 0x0f], out);
    }
}

/* IEEE-754 bit pattern, so the comparison is exact rather than decimal-rounded
 * and so NaN payloads and signed zero stay visible. */
static const char *
tr_double_bits(double d)
{
    static char out[19];
    unsigned char raw[sizeof(double)];
    unsigned long long bits = 0;
    memcpy(raw, &d, sizeof d);
    /* Assemble little-endian-independently: read the value as an integer of
     * the same width via memcpy, which is well defined. */
    memcpy(&bits, raw, sizeof bits < sizeof raw ? sizeof bits : sizeof raw);
    snprintf(out, sizeof out, "%016llx", bits);
    return out;
}

static int
tr_read_stdin(char **buf, size_t *len)
{
    size_t cap = 1 << 16, n = 0;
    char *p = (char *)malloc(cap);
    if (!p) return -1;
    for (;;) {
        if (n == cap) {
            cap *= 2;
            char *q = (char *)realloc(p, cap);
            if (!q) { free(p); return -1; }
            p = q;
        }
        size_t got = fread(p + n, 1, cap - n, stdin);
        n += got;
        if (got == 0) break;
    }
    *buf = p;
    *len = n;
    return 0;
}

static int
tr_read_file(const char *path, char **buf, size_t *len)
{
    FILE *f = fopen(path, "rb");
    if (!f) return -1;
    size_t cap = 1 << 16, n = 0;
    char *p = (char *)malloc(cap);
    if (!p) { fclose(f); return -1; }
    for (;;) {
        if (n == cap) {
            cap *= 2;
            char *q = (char *)realloc(p, cap);
            if (!q) { free(p); fclose(f); return -1; }
            p = q;
        }
        size_t got = fread(p + n, 1, cap - n, f);
        n += got;
        if (got == 0) break;
    }
    fclose(f);
    *buf = p;
    *len = n;
    return 0;
}

#endif
