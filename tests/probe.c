/*
 * probe: a tiny helper program used by the grader.
 *
 * probe info
 *     Report pid, parent pid, pgid, terminal foreground pgid and open fds
 *     on stderr.
 *     If stdin is not a terminal, copy stdin to stdout.
 * probe wait
 *     Sleep until a signal arrives.
 * probe sleep SEC
 *     Sleep SEC seconds (may be fractional), then exit 0.
 * probe exit CODE
 *     Exit with status CODE.
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static void list_fds(char *out, size_t len)
{
    DIR *d = opendir("/proc/self/fd");
    int self = dirfd(d);
    int fds[256], n = 0;
    struct dirent *e;

    while ((e = readdir(d)) && n < 256) {
        if (e->d_name[0] == '.')
            continue;
        int fd = atoi(e->d_name);
        if (fd != self)
            fds[n++] = fd;
    }
    closedir(d);

    /* sort ascending */
    for (int i = 1; i < n; i++)
        for (int j = i; j > 0 && fds[j - 1] > fds[j]; j--) {
            int t = fds[j];
            fds[j] = fds[j - 1];
            fds[j - 1] = t;
        }

    out[0] = '\0';
    for (int i = 0; i < n; i++) {
        char buf[16];
        snprintf(buf, sizeof(buf), i ? ",%d" : "%d", fds[i]);
        strncat(out, buf, len - strlen(out) - 1);
    }
}

static void do_info(void)
{
    char fds[1024], line[1200];
    list_fds(fds, sizeof(fds));
    int n = snprintf(line, sizeof(line),
                     "PROBE pid=%d ppid=%d pgid=%d tpgid=%d fds=%s\n", getpid(),
                     getppid(), getpgrp(), tcgetpgrp(STDERR_FILENO), fds);
    /* A single write() so lines from different processes never interleave. */
    if (write(STDERR_FILENO, line, n) < 0)
        return;

    if (!isatty(STDIN_FILENO)) {
        char buf[4096];
        ssize_t r;
        while ((r = read(STDIN_FILENO, buf, sizeof(buf))) > 0)
            if (write(STDOUT_FILENO, buf, r) < 0)
                break;
    }
}

int main(int argc, char **argv)
{
    if (argc >= 2 && strcmp(argv[1], "info") == 0) {
        do_info();
        return 0;
    }
    if (argc >= 2 && strcmp(argv[1], "wait") == 0) {
        for (;;)
            pause();
    }
    if (argc >= 3 && strcmp(argv[1], "sleep") == 0) {
        double sec = atof(argv[2]);
        struct timespec ts = {(time_t) sec,
                              (long) ((sec - (time_t) sec) * 1e9)};
        while (nanosleep(&ts, &ts) < 0)
            ;
        return 0;
    }
    if (argc >= 3 && strcmp(argv[1], "exit") == 0)
        return atoi(argv[2]);

    fprintf(stderr, "usage: probe info|wait|sleep SEC|exit CODE\n");
    return 2;
}
