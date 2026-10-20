#include <stdio.h>
#include <stdlib.h>
#include <sys/wait.h>
#include "../include/shell.h"

job *first_job = NULL;

/* Append a job to the job list and give it the next free job number. */
void add_job(job *j)
{
    int max = 0;
    job **tail = &first_job;
    for (job *k = first_job; k; k = k->next) {
        if (k->jid > max)
            max = k->jid;
        tail = &k->next;
    }
    j->jid = max + 1;
    j->next = NULL;
    *tail = j;
}

/* Remove a job from the job list and free it. */
void delete_job(job *j)
{
    for (job **pp = &first_job; *pp; pp = &(*pp)->next) {
        if (*pp == j) {
            *pp = j->next;
            break;
        }
    }
    free_job(j);
}

void free_job(job *j)
{
    process *p = j->first_process;
    while (p) {
        process *next = p->next;
        free(p->argv);
        free(p);
        p = next;
    }
    free(j->tokbuf);
    free(j->command);
    free(j);
}

/* Return true if every process in the job has completed. */
int job_is_completed(job *j)
{
    for (process *p = j->first_process; p; p = p->next)
        if (!p->completed)
            return 0;
    return 1;
}

/*
 * Record the status that waitpid() returned for pid.
 * Return 0 if pid was valid, -1 if pid <= 0 (nothing to record).
 * A pid that is not in the job table is ignored.
 */
int mark_process_status(pid_t pid, int status)
{
    if (pid <= 0)
        return -1;

    for (job *j = first_job; j; j = j->next) {
        for (process *p = j->first_process; p; p = p->next) {
            if (p->pid == pid) {
                p->status = status;
                p->completed = 1;
                return 0;
            }
        }
    }
    return 0;
}

/* Print "[jid] pid" for a job just started in the background
   (pid of its last process). */
void report_background_job(job *j)
{
    process *last = j->first_process;
    while (last->next)
        last = last->next;
    printf("[%d] %d\n", j->jid, last->pid);
    fflush(stdout);
}

/*
 * Called before every prompt: collect finished background processes,
 * report finished background jobs and remove them from the job list.
 * Output format:  [1]  Done       sleep 2 &
 */
void do_job_notification(void)
{
    update_bg_jobs();

    job *j = first_job;
    while (j) {
        job *next = j->next;
        if (job_is_completed(j)) {
            printf("[%d]  Done       %s\n", j->jid, j->command);
            fflush(stdout);
            delete_job(j);
        }
        j = next;
    }
}
