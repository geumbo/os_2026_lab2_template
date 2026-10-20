#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include "../include/shell.h"

int last_status = 0;

/*
 * A built-in runs inside the shell, so it cannot be part of a pipeline,
 * have its stdin/stdout redirected, or run in the background.
 * Returns 1 (and prints an error) if the job uses a built-in that way.
 */
static int builtin_misused(job *j)
{
    process *first = j->first_process;
    int has_builtin = 0;
    for (process *p = first; p; p = p->next)
        if (is_builtin(p->argv[0]))
            has_builtin = 1;
    if (!has_builtin)
        return 0;
    if (!first->next && !j->bg && !first->infile && !first->outfile)
        return 0;
    fprintf(stderr,
            "my_shell: built-in commands do not support pipes, redirection "
            "or &\n");
    return 1;
}

int main(void)
{
    char line[MAX_LINE];

    while (1) {
        do_job_notification();

        printf(">>> $ ");
        fflush(stdout); /* flush before fork() so children do not copy it */

        if (!fgets(line, sizeof(line), stdin)) {
            putchar('\n');
            break;
        }

        job *j = parse_line(line);
        if (!j)
            continue;

        if (builtin_misused(j)) {
            last_status = 1;
            free_job(j);
            continue;
        }

        if (is_builtin(j->first_process->argv[0])) {
            int keep_going = run_builtin(j);
            free_job(j);
            if (!keep_going)
                break;
            continue;
        }

        add_job(j);
        launch_job(j);
        if (!j->bg)
            delete_job(j); /* a foreground job is finished here */
    }

    return 0;
}
