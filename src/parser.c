#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../include/shell.h"

/*
 * Input format (tokens are separated by whitespace):
 *
 *   cmd args [< in] [> out] | cmd args ... [&]
 *
 * - Operators: |  <  >  &
 * - "&" may only appear as the last token.
 * - A token that is exactly "$?" is replaced by the last exit status.
 * - No quoting, no globbing, no variables other than $?.
 */

#define MAX_TOKENS 256

static process *new_process(void)
{
    process *p = calloc(1, sizeof(process));
    p->argv = calloc(MAX_TOKENS + 1, sizeof(char *));
    return p;
}

static int argc_of(process *p)
{
    int n = 0;
    while (p->argv[n])
        n++;
    return n;
}

static void syntax_error(const char *near)
{
    fprintf(stderr, "my_shell: syntax error near '%s'\n",
            near ? near : "newline");
}

static int is_redirect(const char *t)
{
    return strcmp(t, "<") == 0 || strcmp(t, ">") == 0;
}

/*
 * Parse one command line into a job.
 * Returns NULL for an empty line or a syntax error.
 */
job *parse_line(const char *line)
{
    job *j = calloc(1, sizeof(job));
    j->command = strdup(line);
    j->command[strcspn(j->command, "\n")] = '\0';
    j->tokbuf = strdup(j->command);

    char *tokens[MAX_TOKENS];
    int ntok = 0;
    for (char *t = strtok(j->tokbuf, " \t"); t; t = strtok(NULL, " \t")) {
        if (ntok == MAX_TOKENS) {
            fprintf(stderr, "my_shell: too many words (at most %d)\n",
                    MAX_TOKENS);
            goto fail;
        }
        tokens[ntok++] = t;
    }

    if (ntok == 0)
        goto fail;

    process *p = new_process();
    j->first_process = p;

    for (int i = 0; i < ntok; i++) {
        char *t = tokens[i];

        if (strcmp(t, "|") == 0) {
            if (argc_of(p) == 0 || i + 1 == ntok) {
                syntax_error(t);
                goto fail;
            }
            p->next = new_process();
            p = p->next;
        } else if (strcmp(t, "&") == 0) {
            if (i + 1 != ntok || argc_of(p) == 0) {
                syntax_error(t);
                goto fail;
            }
            j->bg = 1;
        } else if (is_redirect(t)) {
            if (i + 1 == ntok || is_redirect(tokens[i + 1])) {
                syntax_error(i + 1 == ntok ? NULL : tokens[i + 1]);
                goto fail;
            }
            char *target = tokens[++i];
            if (strcmp(t, "<") == 0)
                p->infile = target;
            else
                p->outfile = target;
        } else if (strcmp(t, "$?") == 0) {
            snprintf(j->status_buf, sizeof(j->status_buf), "%d", last_status);
            p->argv[argc_of(p)] = j->status_buf;
        } else {
            p->argv[argc_of(p)] = t;
        }
    }

    if (argc_of(p) == 0) {
        syntax_error(NULL);
        goto fail;
    }
    return j;

fail:
    free_job(j);
    return NULL;
}
