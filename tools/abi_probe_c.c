/* Emits the ABI layout that the ORIGINAL pinned pdjson.h dictates on this
 * platform/compiler. The Zig library emits the same table from its own
 * independent declarations; scripts/abi-check.sh diffs the two.
 *
 * This file includes only the pinned public header. It never includes or links
 * pdjson.c. */
#include <stdio.h>
#include <stddef.h>
#include "pdjson.h"

#define FIELD(st, f) \
    printf("  {\"struct\": \"%s\", \"field\": \"%s\", \"offset\": %zu, \"size\": %zu},\n", \
           #st, #f, offsetof(struct st, f), sizeof(((struct st *)0)->f))

#define ENUMV(e) printf("  {\"enum\": \"json_type\", \"name\": \"%s\", \"value\": %d},\n", #e, (int)e)

int
main(void)
{
    printf("{\n");
    printf("  \"schema\": \"pdjson-zig/abi-layout@1\",\n");
    printf("  \"producer\": \"c\",\n");
    printf("  \"pointer_size\": %zu,\n", sizeof(void *));
    printf("  \"size_t_size\": %zu,\n", sizeof(size_t));
    printf("  \"enum_json_type_size\": %zu,\n", sizeof(enum json_type));
    printf("  \"enum_json_type_signed\": %d,\n", (int)((enum json_type)-1 < (enum json_type)1));
    printf("  \"sizeof_json_stream\": %zu,\n", sizeof(struct json_stream));
    printf("  \"alignof_json_stream\": %zu,\n", (size_t)_Alignof(struct json_stream));
    printf("  \"sizeof_json_source\": %zu,\n", sizeof(struct json_source));
    printf("  \"alignof_json_source\": %zu,\n", (size_t)_Alignof(struct json_source));
    printf("  \"sizeof_json_allocator\": %zu,\n", sizeof(struct json_allocator));
    printf("  \"alignof_json_allocator\": %zu,\n", (size_t)_Alignof(struct json_allocator));
    printf("  \"layout\": [\n");

    FIELD(json_stream, lineno);
    FIELD(json_stream, stack);
    FIELD(json_stream, stack_top);
    FIELD(json_stream, stack_size);
    FIELD(json_stream, next);
    FIELD(json_stream, flags);
    FIELD(json_stream, data);
    FIELD(json_stream, data.string);
    FIELD(json_stream, data.string_fill);
    FIELD(json_stream, data.string_size);
    FIELD(json_stream, ntokens);
    FIELD(json_stream, source);
    FIELD(json_stream, alloc);
    FIELD(json_stream, errmsg);

    FIELD(json_source, get);
    FIELD(json_source, peek);
    FIELD(json_source, position);
    FIELD(json_source, source);
    FIELD(json_source, source.stream.stream);
    FIELD(json_source, source.buffer.buffer);
    FIELD(json_source, source.buffer.length);
    FIELD(json_source, source.user.ptr);
    FIELD(json_source, source.user.get);
    FIELD(json_source, source.user.peek);

    FIELD(json_allocator, malloc);
    FIELD(json_allocator, realloc);
    FIELD(json_allocator, free);

    printf("  {\"end\": true}\n");
    printf("  ],\n");
    printf("  \"enums\": [\n");
    ENUMV(JSON_ERROR);
    ENUMV(JSON_DONE);
    ENUMV(JSON_OBJECT);
    ENUMV(JSON_OBJECT_END);
    ENUMV(JSON_ARRAY);
    ENUMV(JSON_ARRAY_END);
    ENUMV(JSON_STRING);
    ENUMV(JSON_NUMBER);
    ENUMV(JSON_TRUE);
    ENUMV(JSON_FALSE);
    ENUMV(JSON_NULL);
    printf("  {\"end\": true}\n");
    printf("  ]\n");
    printf("}\n");
    return 0;
}
