/* Minimal reproducer: json_get_context() reads an unallocated stack slot after
 * a failed allocation.
 *
 * Uses only the documented public API and the documented custom-allocator hook.
 * Build against the pinned upstream sources:
 *
 *   cc -std=c99 -g -fsanitize=address,undefined -I upstream/pdjson \
 *      -o repro tests/upstream-bugs/repro_oom_stack.c upstream/pdjson/pdjson.c
 *   ./repro
 *
 * Expected (fixed):   json_get_context() reports something well-defined.
 * Actual  (78fe04b):  SEGV, or a heap-buffer-overflow read, at pdjson.c:912.
 *
 * Cause: push() does `json->stack_top++` before it grows the stack. When the
 * realloc fails it reports "out of memory" and returns JSON_ERROR, but leaves
 * stack_top pointing at a slot that was never allocated -- and, on the very
 * first push, leaves it pointing into a NULL stack. json_get_context() then
 * indexes json->stack[json->stack_top] with no bounds or NULL check.
 */
#include <stdio.h>
#include <stdlib.h>
#include "pdjson.h"

static long budget;

static void *lim_malloc(size_t n)  { if (!budget) return NULL; budget--; return malloc(n); }
static void *lim_realloc(void *p, size_t n) { if (!budget) return NULL; budget--; return realloc(p, n); }
static void  lim_free(void *p)     { free(p); }

static void
scenario(const char *name, long allowance, const char *input)
{
    json_stream json[1];
    json_allocator alloc;

    budget = allowance;
    alloc.malloc = lim_malloc;
    alloc.realloc = lim_realloc;
    alloc.free = lim_free;

    json_open_string(json, input);
    json_set_allocator(json, &alloc);

    enum json_type t;
    do {
        t = json_next(json);
    } while (t != JSON_ERROR && t != JSON_DONE);

    printf("%-28s error=%s\n", name, json_get_error(json) ? json_get_error(json) : "(none)");
    fflush(stdout);

    /* Any caller may ask where the parser stopped. This is the crash. */
    size_t count = 0;
    enum json_type ctx = json_get_context(json, &count);
    printf("%-28s depth=%zu context=%d count=%zu\n",
           name, json_get_depth(json), (int)ctx, count);
    fflush(stdout);

    json_close(json);
}

int
main(void)
{
    /* allowance 0: the first stack allocation fails, so json->stack is NULL
     * while stack_top has already been advanced to 0  ->  NULL dereference. */
    scenario("null-stack", 0, "[1]");

    /* allowance 1: the first block of 4 frames is allocated, the 5th push
     * fails, so stack_top == 4 == stack_size  ->  out-of-bounds read. */
    scenario("past-end-of-stack", 1, "[[[[[1]]]]]");

    printf("no crash observed\n");
    return 0;
}
