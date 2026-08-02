/* Minimal reproducer: json_get_number() reads past the bytes the parser wrote.
 *
 *   cc -std=c99 -I upstream/pdjson \
 *      -o repro tests/upstream-bugs/repro_uninit_number.c upstream/pdjson/pdjson.c
 *   ./repro
 *
 * Cause: read_number() and read_string() push the terminating NUL only when the
 * token completes. If the token fails part way -- input "-" is enough -- the
 * buffer holds the bytes pushed so far and no terminator. json_get_number() is
 *
 *     double json_get_number(json_stream *json)
 *     {
 *         char *p = json->data.string;
 *         return p == NULL ? 0 : strtod(p, NULL);
 *     }
 *
 * so strtod() keeps reading until it finds a NUL somewhere in the uninitialised
 * remainder of the 1 KiB malloc block.
 *
 * Two demonstrations, because the natural one is nondeterministic and therefore
 * unconvincing on its own:
 *
 *   A. With the default allocator, the result depends on whatever happens to be
 *      in the heap. On glibc this makes json_get_number() return different
 *      values for identical input across runs of the same binary.
 *
 *   B. With a custom allocator -- json_set_allocator is documented public API --
 *      that fills fresh blocks with '9', the read is deterministic and the
 *      returned value is visibly composed of bytes the parser never wrote.
 *
 * Scope, stated carefully: the defect demonstrated here is a read of
 * *uninitialised* bytes inside the allocation. Whether strtod also runs past
 * the end of the allocation depends on whether a NUL happens to appear in the
 * remainder; running this reproducer under ASan with only json_get_number()
 * called reports nothing, so an out-of-bounds read is a possible consequence,
 * not a demonstrated one.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pdjson.h"

/* ---- B: an allocator that makes the uninitialised bytes visible ---------- */

static void *filling_malloc(size_t n)
{
    void *p = malloc(n);
    if (p) memset(p, '9', n);   /* a digit, so strtod will happily consume it */
    return p;
}

static void *filling_realloc(void *p, size_t n)
{
    /* Not exercised by these inputs; kept honest anyway. */
    return realloc(p, n);
}

static void filling_free(void *p) { free(p); }

static double
number_after(const char *doc, int use_filling_allocator, size_t *fill_out)
{
    json_stream json[1];
    json_open_buffer(json, doc, strlen(doc));

    if (use_filling_allocator) {
        json_allocator a;
        a.malloc = filling_malloc;
        a.realloc = filling_realloc;
        a.free = filling_free;
        json_set_allocator(json, &a);
    }

    while (1) {
        enum json_type t = json_next(json);
        if (t == JSON_ERROR || t == JSON_DONE) break;
    }

    size_t len = 0;
    const char *s = json_get_string(json, &len);
    if (fill_out) *fill_out = len;

    /* What the parser actually wrote, versus what strtod will walk over. */
    double v = json_get_number(json);
    printf("  input %-6s  string_fill=%zu  bytes=", doc, len);
    for (size_t i = 0; i < len; i++) printf("%02x", (unsigned char)s[i]);
    /* Note: this strlen is the reproducer's own probe, not something the
     * library does. It is here only to show how far past the written region a
     * NUL-terminated read would travel. */
    printf("  probe strlen=%zu", strlen(s));
    if (strlen(s) > len)
        printf("  <- %zu byte(s) past what the parser wrote", strlen(s) - len);
    printf("\n                 json_get_number() = %.17g\n", v);

    json_close(json);
    return v;
}

int
main(void)
{
    printf("A. default allocator (result depends on heap contents)\n");
    number_after("-", 0, NULL);
    number_after("\"12", 0, NULL);

    printf("\nB. allocator that fills fresh blocks with '9'\n");
    printf("   Every byte after the token is one the parser did not write.\n");
    double a = number_after("-", 1, NULL);
    double b = number_after("\"12", 1, NULL);

    printf("\nExpected: 0 and 12 -- strtod should see only \"-\" and \"12\".\n");
    printf("Actual:   %.17g and %.17g\n", a, b);

    if (a != 0.0 || b != 12.0) {
        printf("\nCONFIRMED: json_get_number() consumed uninitialised bytes.\n");
        return 0;
    }
    printf("\nNot reproduced in this build.\n");
    return 1;
}
