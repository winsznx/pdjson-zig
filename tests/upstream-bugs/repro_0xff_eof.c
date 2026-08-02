/* Minimal reproducer: a 0xFF input byte is treated as end-of-input by the
 * memory-buffer source, but not by the FILE* source.
 *
 *   cc -std=c99 -I upstream/pdjson \
 *      -o repro tests/upstream-bugs/repro_0xff_eof.c upstream/pdjson/pdjson.c
 *   ./repro
 *
 * Cause: buffer_peek() returns
 *     source->source.buffer.buffer[source->position]
 * where `buffer` is a `const char *`. On every platform where `char` is signed
 * (x86-64 Linux/macOS/Windows, Apple arm64) the byte 0xFF widens to -1, which
 * is EOF. stream_get()/fgetc() correctly yields 0..255, so the same bytes
 * parse differently depending only on which json_open_* was used.
 *
 * Three observable consequences:
 *   1. Different diagnostic for identical input across two documented sources.
 *   2. json_get_position() stops advancing, because buffer_get() only
 *      increments position when the byte is not EOF.
 *   3. json_source_get() -- the documented way to inspect separators between
 *      streamed values -- can never consume the byte, so a caller loop that
 *      scans forward for a delimiter makes no progress.
 *
 * 0xFF is never valid UTF-8, so this does not make the parser accept bad
 * input; it makes it misreport where and why the input was rejected, and it
 * makes one documented API unable to advance.
 */
#include <stdio.h>
#include <string.h>
#include "pdjson.h"

static const char INPUT[] = { '"', (char)0xFF, '"' };

int
main(void)
{
    printf("char is %s on this build\n", ((char)-1 < 0) ? "signed" : "unsigned");

    {
        json_stream json[1];
        json_open_buffer(json, INPUT, sizeof INPUT);
        json_set_streaming(json, 0);
        enum json_type t = json_next(json);
        printf("buffer source : event=%d position=%zu error=%s\n",
               (int)t, json_get_position(json),
               json_get_error(json) ? json_get_error(json) : "(none)");
        json_close(json);
    }

    {
        FILE *f = tmpfile();
        fwrite(INPUT, 1, sizeof INPUT, f);
        rewind(f);
        json_stream json[1];
        json_open_stream(json, f);
        json_set_streaming(json, 0);
        enum json_type t = json_next(json);
        printf("FILE*  source : event=%d position=%zu error=%s\n",
               (int)t, json_get_position(json),
               json_get_error(json) ? json_get_error(json) : "(none)");
        json_close(json);
        fclose(f);
    }

    {
        /* json_source_get() cannot advance past the byte. */
        static const char run[] = { (char)0xFF, (char)0xFF, (char)0xFF };
        json_stream json[1];
        json_open_buffer(json, run, sizeof run);
        printf("json_source_get x5 :");
        for (int i = 0; i < 5; i++)
            printf(" %d(pos=%zu)", json_source_get(json), json_get_position(json));
        printf("   <- expected the three 0xFF bytes to be consumed\n");
        json_close(json);
    }

    return 0;
}
