#ifndef SHELL_H
#define SHELL_H

#include <sys/types.h>

#define MAX_LINE 4096

/* A process is a single command in a pipeline. */
typedef struct process {
    struct process *next;
    char **argv;    /* NULL-terminated */
    char *infile;   /* "<" target, NULL if none */
    char *outfile;  /* ">" target, NULL if none */
    pid_t pid;      /* set after fork() */
    char completed; /* true once the process has been waited for */
    int status;     /* raw status reported by waitpid */
} process;

/* A job is one command line. */
typedef struct job {
    struct job *next;
    int jid;       /* job number shown as [1], [2], ... */
    char *command; /* original command line, for messages */
    char *tokbuf;  /* storage that argv strings point into */
    process *first_process;
    char bg;             /* true if the line ends with "&" */
    char status_buf[16]; /* storage for the expanded "$?" token */
} job;

/* ---------- global shell state (main.c) ---------- */
extern int last_status; /* value of $? */
extern job *first_job;

/* ---------- parser.c ---------- */
job *parse_line(const char *line);

/* ---------- jobs.c ---------- */
void add_job(job *j);
void delete_job(job *j);
void free_job(job *j);
int job_is_completed(job *j);
int mark_process_status(pid_t pid, int status);
void report_background_job(job *j);
void do_job_notification(void);

/* ---------- builtin.c ---------- */
int is_builtin(const char *name);
int run_builtin(job *j); /* returns 0 if the shell should exit */

/* ---------- exec.c (student TODOs) ---------- */
void launch_process(process *p, int infile, int outfile, int background);
void launch_job(job *j);
void wait_for_job(job *j);
void update_bg_jobs(void);

#endif
