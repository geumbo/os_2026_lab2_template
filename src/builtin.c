#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <limits.h>
#include "../include/shell.h"

/*
 * Built-in commands run inside the shell process itself (no fork).
 * They do not support pipes, redirection or "&".
 * Each returns its exit status, which becomes $?.
 */

/* Print the current working directory. Use it as a reference for cd. */
static int builtin_pwd(char **argv)
{
    (void) argv;
    char cwd[PATH_MAX];
    if (!getcwd(cwd, sizeof(cwd))) {
        perror("pwd");
        return 1;
    }
    printf("%s\n", cwd);
    return 0;
}

/* Change the shell's working directory. Returns the exit status ($?). */
static int builtin_cd(char **argv)
{
    /* TODO [1-2] chdir() here, in the shell itself: argv[1], or getenv("HOME")
       when there is no argument ("cd: HOME not set" if it is missing).
       On failure perror("cd") and return 1; on success return 0.
       Write your code above the provided fallback lines below. */
    /* (provided) fallback: reached only if your code above does not return */
    (void) argv;
    return 1;
}

static const struct {
    const char *name;
    int (*func)(char **);
} builtins[] = {
    {"pwd", builtin_pwd},
    {"cd", builtin_cd},
    {"exit", NULL},
};

#define NUM_BUILTINS (sizeof(builtins) / sizeof(builtins[0]))

int is_builtin(const char *name)
{
    for (size_t i = 0; i < NUM_BUILTINS; i++)
        if (strcmp(name, builtins[i].name) == 0)
            return 1;
    return 0;
}

int run_builtin(job *j)
{
    char **argv = j->first_process->argv;
    for (size_t i = 0; i < NUM_BUILTINS; i++) {
        if (strcmp(argv[0], builtins[i].name) != 0)
            continue;
        if (!builtins[i].func)
            return 0; /* exit */
        last_status = builtins[i].func(argv);
        return 1;
    }
    return 1;
}
