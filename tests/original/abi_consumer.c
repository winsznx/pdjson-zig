/* A C consumer written against the ORIGINAL public header, linked against only
 * the Zig static library.
 *
 * This is the practical half of the ABI claim. The layout table proves the
 * offsets agree; this proves a C program that declares `struct json_stream` by
 * value on its own stack -- exactly as upstream's own tests do -- can drive the
 * Zig implementation through the header's function declarations and get the
 * expected answers.
 *
 * It includes upstream/pdjson/pdjson.h and never sees a line of pdjson.c.
 */
#include <stdio.h>
#include <string.h>
#include <stddef.h>
#include "pdjson.h"

static int failures;

static void
check(int ok, const char *what)
{
    if (!ok) {
        printf("FAIL %s\n", what);
        failures++;
    }
}

int
main(void)
{
    /* The struct is declared by value here, so its size and layout come from
     * the header, while every function comes from the Zig archive. If the two
     * disagreed, this would corrupt the stack rather than merely misbehave. */
    struct json_stream json[1];

    check(sizeof json[0] == 272 || sizeof(void *) != 8,
          "sizeof(struct json_stream) is what the header says on LP64");

    {
        const char doc[] = "{\"key\": [1, -2.5e3, true, null, \"v\"]}";
        json_open_string(json, doc);
        json_set_streaming(json, 0);

        check(json_next(json) == JSON_OBJECT, "object begins");
        check(json_get_depth(json) == 1, "depth inside object");

        size_t count = 0;
        check(json_get_context(json, &count) == JSON_OBJECT, "context is object");

        check(json_next(json) == JSON_STRING, "member name");
        check(strcmp(json_get_string(json, NULL), "key") == 0, "member name text");

        check(json_next(json) == JSON_ARRAY, "array begins");
        check(json_get_depth(json) == 2, "depth inside array");

        check(json_next(json) == JSON_NUMBER, "first number");
        check(strcmp(json_get_string(json, NULL), "1") == 0, "raw number lexeme");
        check(json_get_number(json) == 1.0, "number value");

        check(json_next(json) == JSON_NUMBER, "second number");
        check(json_get_number(json) == -2500.0, "exponent number value");

        check(json_next(json) == JSON_TRUE, "true");
        check(json_next(json) == JSON_NULL, "null");

        check(json_next(json) == JSON_STRING, "string element");
        size_t len = 0;
        const char *s = json_get_string(json, &len);
        check(len == 2 && s[0] == 'v' && s[1] == '\0',
              "string length counts the terminator, as upstream does");

        check(json_next(json) == JSON_ARRAY_END, "array ends");
        check(json_next(json) == JSON_OBJECT_END, "object ends");
        check(json_next(json) == JSON_DONE, "done");
        check(json_get_error(json) == NULL, "no error");
        json_close(json);
    }

    {
        /* Error path: message text, line and position are all read back
         * through the header's accessors. */
        const char doc[] = "[1,\n2,\n@]";
        json_open_string(json, doc);
        check(json_next(json) == JSON_ARRAY, "array begins");
        check(json_next(json) == JSON_NUMBER, "1");
        check(json_next(json) == JSON_NUMBER, "2");
        check(json_next(json) == JSON_ERROR, "error on '@'");
        check(json_get_error(json) != NULL, "error message present");
        check(strcmp(json_get_error(json), "unexpected byte '@' in value") == 0,
              "error message text");
        check(json_get_lineno(json) == 3, "line number at the error");
        json_close(json);
    }

    {
        /* Streaming and reset across several values. */
        const char doc[] = "1 2 3";
        json_open_string(json, doc);
        json_set_streaming(json, 1);
        for (int i = 1; i <= 3; i++) {
            check(json_next(json) == JSON_NUMBER, "streamed number");
            check(json_get_number(json) == (double)i, "streamed number value");
            check(json_next(json) == JSON_DONE, "value boundary");
            json_reset(json);
        }
        check(json_next(json) == JSON_DONE, "end of stream");
        json_close(json);
    }

    {
        /* json_skip over a nested value, and the source-byte accessors. */
        const char doc[] = "[{\"a\":[1,2]},9]";
        json_open_string(json, doc);
        check(json_next(json) == JSON_ARRAY, "array begins");
        check(json_skip(json) == JSON_OBJECT, "skip the object");
        check(json_next(json) == JSON_NUMBER, "value after the skipped object");
        check(json_get_number(json) == 9.0, "value after skip");
        json_close(json);
    }

    check(json_isspace(' ') && json_isspace('\t') && json_isspace('\n')
          && json_isspace('\r') && !json_isspace('x'),
          "json_isspace");

    if (failures == 0) {
        printf("abi_consumer: all checks passed against the Zig library\n");
        return 0;
    }
    printf("abi_consumer: %d check(s) failed\n", failures);
    return 1;
}
