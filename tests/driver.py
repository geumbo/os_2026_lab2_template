#!/usr/bin/env python3
"""
OS Lab2 grader driver.

Runs the student's shell inside a pseudo-terminal, types commands into it,
and checks the results against bash and against /proc.
Every check starts a fresh shell so one failure cannot break the next check.
A requirement earns its point only if all of its checks pass.

usage: driver.py SHELL PROBE CHECKSUM [-v]
"""

import json
import os
import pty
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time

SHELL, PROBE, CHECKSUM = sys.argv[1:4]
VERBOSE = "-v" in sys.argv[4:]
PROMPT = b">>> $ "

LEVELS = {1: "Commands", 2: "Redirection and pipes", 3: "Background jobs"}
REQUIREMENTS = {
    "1-1": "External commands",
    "1-2": "Built-in commands",
    "2-1": "Redirection",
    "2-2": "A pipe",
    "2-3": "Pipelines",
    "3-1": "Background jobs",
    "3-2": "Reaping",
}

DATA = "data.txt"
DATA_TEXT = """fork creates a new process
exec replaces the process image
pipe connects two processes
fork returns twice
signal interrupts a process
wait collects a child
fork and exec work together
dup2 duplicates a file descriptor
"""

WORKDIR = os.path.realpath(tempfile.mkdtemp(prefix="lab2grade_"))
HOME = os.path.join(WORKDIR, "home")
os.makedirs(os.path.join(WORKDIR, "sub"))
os.makedirs(HOME)
with open(os.path.join(WORKDIR, DATA), "w") as f:
    f.write(DATA_TEXT)
with open(os.path.join(WORKDIR, "sub", "inner.txt"), "w") as f:
    f.write("inside sub\n")


class CheckFailed(Exception):
    pass


# --------------------------------------------------------------------------
# /proc helpers
# --------------------------------------------------------------------------
def proc_stat(pid):
    """Return dict(state, ppid, session) or None if pid is gone."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            s = f.read()
    except OSError:
        return None
    rest = s[s.rindex(")") + 2 :].split()
    return {"state": rest[0], "ppid": int(rest[1]), "session": int(rest[3])}


def state(pid):
    """Process state letter (R, S, T, Z, ...), or None if the process is gone."""
    st = proc_stat(pid)
    return st["state"] if st else None


def all_pids():
    return [int(d) for d in os.listdir("/proc") if d.isdigit()]


def children_of(pid):
    kids = []
    for p in all_pids():
        st = proc_stat(p)
        if st and st["ppid"] == pid:
            kids.append(p)
    return kids


def wait_for(pred, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


# --------------------------------------------------------------------------
# Shell session in a pty
# --------------------------------------------------------------------------
class Shell:
    def __init__(self):
        env = dict(os.environ)
        env["PATH"] = os.path.dirname(PROBE) + ":" + env.get("PATH", "/usr/bin:/bin")
        env["TERM"] = "dumb"
        env["HOME"] = HOME
        self.typed = []  # what the grader typed, shown when a check fails
        self.exit_status = None
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.chdir(WORKDIR)
            # Python ignores SIGPIPE; give the shell the default a real terminal would.
            signal.signal(signal.SIGPIPE, signal.SIG_DFL)
            # Start the shell with only 0, 1, 2 open, whatever the caller's
            # environment leaked (IDE terminals often leave extra fds open).
            os.closerange(3, os.sysconf("SC_OPEN_MAX"))
            os.execve(SHELL, [SHELL], env)
        self.buf = b""
        try:
            self.read_until(PROMPT, 3, "the shell did not print its prompt '>>> $ '")
        except BaseException:
            self.close()  # the caller never gets this object, so clean up here
            raise

    def read_until(self, pat, timeout, what=None):
        end = time.time() + timeout
        while pat not in self.buf:
            left = end - time.time()
            if left <= 0:
                raise CheckFailed(what or f"timed out waiting for {pat.decode(errors='replace')!r}")
            r, _, _ = select.select([self.fd], [], [], left)
            if r:
                try:
                    data = os.read(self.fd, 65536)
                except OSError:
                    data = b""
                if not data:
                    raise CheckFailed("the shell exited unexpectedly")
                self.buf += data
        i = self.buf.index(pat) + len(pat)
        out, self.buf = self.buf[:i], self.buf[i:]
        return out

    def type_line(self, text):
        self.typed.append(text if text else "(Enter)")
        os.write(self.fd, (text + "\n").encode())

    def wait_prompt(self, timeout=3.0, what=None):
        out = self.read_until(PROMPT, timeout, what or "the prompt did not come back")
        return clean(out[: -len(PROMPT)])

    def run(self, cmd, timeout=3.0):
        """Type a command and return its output (echo line removed)."""
        self.type_line(cmd)
        out = self.wait_prompt(timeout, f"did not finish within {timeout:g} s")
        lines = out.split("\n")
        return "\n".join(lines[1:]).strip("\n")

    def status(self):
        return self.run("echo $?")

    def exited(self):
        """True once the shell process has exited (records its exit status)."""
        if self.exit_status is None:
            pid, st = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self.exit_status = os.waitstatus_to_exitcode(st)
        return self.exit_status is not None

    def live_children(self):
        return [k for k in children_of(self.pid) if state(k) not in (None, "Z")]

    def zombies(self):
        return [k for k in children_of(self.pid) if state(k) == "Z"]

    def close(self):
        # Kill everything in the shell's session, then the shell.
        for p in all_pids():
            st = proc_stat(p)
            if st and st["session"] == self.pid and p != self.pid:
                try:
                    os.kill(p, signal.SIGKILL)
                except OSError:
                    pass
        if self.exit_status is None:
            try:
                os.kill(self.pid, signal.SIGKILL)
                os.waitpid(self.pid, 0)
            except (OSError, ChildProcessError):
                pass
        os.close(self.fd)


def clean(b):
    return b.decode(errors="replace").replace("\r\n", "\n").replace("\r", "")


def bash_out(cmd):
    r = subprocess.run(["bash", "-c", cmd], cwd=WORKDIR, capture_output=True, text=True, timeout=10)
    return r.stdout.strip("\n")


def probe_lines(text):
    return [dict(kv.split("=") for kv in line.split()[1:]) for line in text.split("\n") if line.startswith("PROBE ")]


def read_file(name):
    try:
        with open(os.path.join(WORKDIR, name)) as f:
            return f.read()
    except OSError:
        return None


def expect(cond, msg):
    if not cond:
        raise CheckFailed(msg)


def expect_same_as_bash(sh, cmd, timeout=3.0):
    out, exp = sh.run(cmd, timeout), bash_out(cmd)
    expect(out == exp, f"printed {out!r}, bash prints {exp!r}")


def timed(sh, cmd, timeout=3.0):
    t = time.time()
    out = sh.run(cmd, timeout)
    return out, time.time() - t


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------
CHECKS = []


def check(req, name, hint):
    def deco(f):
        CHECKS.append({"req": req, "name": name, "hint": hint, "func": f})
        return f

    return deco


# ---------------- 1-1 External commands ----------------
@check("1-1", "prints the output of a command", "the child must execvp() the program with p->argv")
def _(sh):
    expect_same_as_bash(sh, "echo hello world")
    expect_same_as_bash(sh, "head -n 3 data.txt")


@check("1-1", "waits until the command finishes", "the shell must waitpid() for the child before the next prompt")
def _(sh):
    _, took = timed(sh, "probe sleep 0.5")
    expect(took >= 0.45, "the prompt came back before the command finished")


@check("1-1", "$? is the exit status of the command", "waitpid() reports the status: WIFEXITED → WEXITSTATUS")
def _(sh):
    for cmd, code in [("true", "0"), ("false", "1"), ("probe exit 7", "7")]:
        sh.run(cmd)
        out = sh.status()
        expect(out == code, f"$? is {out!r} after '{cmd}', expected {code}")


@check(
    "1-1",
    "command not found: message, $? = 127, shell keeps going",
    "if execvp() returns, it failed: print the message and leave the child with _exit(127)",
)
def _(sh):
    out = sh.run("nosuchcmd")
    expect("nosuchcmd: command not found" in out, f"expected 'nosuchcmd: command not found', got {out!r}")
    expect(sh.status() == "127", "$? is not 127")
    expect(sh.run("echo still here") == "still here", "the shell does not work after a failed exec")


@check("1-1", "no zombies after foreground commands", "every child must be reaped with waitpid()")
def _(sh):
    for _ in range(5):
        sh.run("probe exit 0")
    expect(not sh.zombies(), f"{len(sh.zombies())} zombie processes left")


# ---------------- 1-2 Built-in commands ----------------
@check(
    "1-2",
    "cd changes the shell's directory",
    "chdir() must run in the shell itself: a child's chdir() does not change its parent",
)
def _(sh):
    sh.run("cd /tmp")
    out = sh.run("pwd")
    expect(out == "/tmp", f"pwd prints {out!r} after 'cd /tmp'")


@check("1-2", "later commands run in the new directory", "children inherit the shell's working directory")
def _(sh):
    sh.run("cd sub")
    expect(sh.run("cat inner.txt") == "inside sub", "cat cannot find inner.txt after 'cd sub'")
    out = sh.run("/bin/pwd")
    expect(out.endswith("/sub"), f"/bin/pwd prints {out!r}")


@check("1-2", "cd without an argument goes to $HOME", 'with no argument, use getenv("HOME")')
def _(sh):
    sh.run("cd sub")
    sh.run("cd")
    out = sh.run("pwd")
    expect(out == HOME, f"pwd prints {out!r}, expected {HOME!r}")


@check(
    "1-2",
    "cd to a missing directory: error, $? = 1, directory unchanged",
    'if chdir() fails, perror("cd") and return 1',
)
def _(sh):
    out = sh.run("cd /no_such_dir")
    expect("No such file" in out, f"no error message, got {out!r}")
    expect(sh.status() == "1", "$? is not 1")
    expect(sh.run("pwd") == WORKDIR, "the directory changed")


@check("1-2", "exit ends the shell", "'exit' makes run_builtin() return 0, which ends the main loop")
def _(sh):
    sh.type_line("exit")
    expect(wait_for(sh.exited), "the shell is still running after 'exit'")
    expect(sh.exit_status == 0, f"the shell exited with status {sh.exit_status}")


# ---------------- 2-1 Redirection ----------------
@check("2-1", "< reads a file", "in the child, open() the file O_RDONLY and dup2() it onto fd 0 before execvp()")
def _(sh):
    expect_same_as_bash(sh, "wc -l < data.txt")
    expect_same_as_bash(sh, "grep fork < data.txt")


@check("2-1", "> creates a file", "'>' opens with O_WRONLY | O_CREAT | O_TRUNC and mode 0644, then dup2() onto fd 1")
def _(sh):
    sh.run("echo hello > new.txt")
    got = read_file("new.txt")
    expect(got == "hello\n", f"new.txt contains {got!r}, expected 'hello\\n'")


@check("2-1", "> truncates an existing file", "without O_TRUNC the old content stays behind the new one")
def _(sh):
    sh.run("echo this line is much longer than the next one > trunc.txt")
    sh.run("echo short > trunc.txt")
    got = read_file("trunc.txt")
    expect(got == "short\n", f"trunc.txt contains {got!r}, expected 'short\\n'")


@check("2-1", "< and > on the same command", "handle infile and outfile independently: a command may have both")
def _(sh):
    sh.run("sort < data.txt > both.txt")
    got = (read_file("both.txt") or "").strip("\n")
    expect(got == bash_out("sort < data.txt"), "content of both.txt differs from bash")


@check(
    "2-1",
    "< with a missing file: error message, $? = 1",
    "if open() fails, perror() and leave the child with _exit(1)",
)
def _(sh):
    out = sh.run("cat < missing.txt")
    expect("No such file" in out, f"no error message, got {out!r}")
    expect(sh.status() == "1", "$? is not 1")


@check(
    "2-1",
    "> to a path that cannot be opened: error message, $? = 1",
    "check open()'s result for '>' too: perror() and _exit(1)",
)
def _(sh):
    out = sh.run("echo hello > no_such_dir/out.txt")
    expect("No such file" in out, f"no error message, got {out!r}")
    expect(sh.status() == "1", "$? is not 1")


@check(
    "2-1",
    "the shell's own output is not redirected",
    "do the redirection in the child, after fork(); the shell's fd 1 must not change",
)
def _(sh):
    sh.run("echo first > out1.txt")
    expect(sh.run("echo visible") == "visible", "the next command's output did not reach the screen")


# ---------------- 2-2 A pipe ----------------
@check(
    "2-2",
    "two commands connected by a pipe",
    "the writer dup2()s the write end onto fd 1, the reader dup2()s the read end onto fd 0",
)
def _(sh):
    expect_same_as_bash(sh, "cat data.txt | wc -l")
    expect_same_as_bash(sh, "cat data.txt | grep fork")


@check(
    "2-2",
    "the reader sees end-of-file",
    "the shell must close its copy of the write end, or the reader waits forever",
)
def _(sh):
    expect_same_as_bash(sh, "cat data.txt | cat", timeout=2)


@check(
    "2-2",
    "yes | head -n 1 terminates",
    "the writer must not keep the read end open, or 'yes' never gets SIGPIPE",
)
def _(sh):
    out = sh.run("yes | head -n 1", timeout=2)
    expect(out == "y", f"got {out!r}")


@check("2-2", "a pipe with redirection", "pipe ends are connected first, then file redirection")
def _(sh):
    sh.run("sort < data.txt | uniq > sorted.txt")
    got = (read_file("sorted.txt") or "").strip("\n")
    expect(got == bash_out("sort < data.txt | uniq"), "content of sorted.txt differs from bash")


@check(
    "2-2",
    "a file overrides the pipe",
    "connect the pipe ends first, then the files: the later dup2() onto the same fd wins",
)
def _(sh):
    out = sh.run("echo pipe > diverted.txt | cat")
    expect(out == "", f"the screen shows {out!r}; the output should have gone to diverted.txt")
    got = read_file("diverted.txt")
    expect(got == "pipe\n", f"diverted.txt contains {got!r}, expected 'pipe\\n'")


@check(
    "2-2",
    "both processes have only fds 0, 1, 2 open",
    "after dup2(), close the original fd — in the child and in the shell",
)
def _(sh):
    pr = probe_lines(sh.run("probe info | probe info"))
    expect(len(pr) == 2, f"expected 2 probe reports, got {len(pr)}")
    bad = [p["fds"] for p in pr if p["fds"] != "0,1,2"]
    expect(not bad, f"open fds: {' / '.join(bad)}, expected 0,1,2")


def expect_children_of_shell(sh, cmd, n):
    """Every process of the pipeline must be forked by the shell itself.

    A shell that hands the line to bash -c (or sh -c) passes the output checks,
    but then bash forks the pipeline and becomes the parent of each process.
    """
    pr = probe_lines(sh.run(cmd))
    expect(len(pr) == n, f"expected {n} probe reports, got {len(pr)}")
    parents = [int(p["ppid"]) for p in pr]
    expect(
        all(pp == sh.pid for pp in parents),
        f"parent pids {parents}, expected the shell's pid {sh.pid} for every process",
    )


@check(
    "2-2",
    "both commands are children of the shell",
    "the shell itself must fork() every command; running the line through another shell is not allowed",
)
def _(sh):
    expect_children_of_shell(sh, "probe info | probe info", 2)


# ---------------- 2-3 Pipelines ----------------
@check("2-3", "pipelines of 3 and 5 commands", "each pipe's read end becomes the next process's stdin")
def _(sh):
    expect_same_as_bash(sh, "cat data.txt | grep fork | wc -l")
    expect_same_as_bash(sh, "cat data.txt | tr a-z A-Z | sort | uniq | head -n 3")


@check(
    "2-3",
    "more than 64 KiB through a pipeline",
    "fork every process first, then wait: a pipe holds only a limited amount of data",
)
def _(sh):
    out = sh.run("seq 1 200000 | cat | wc -l", timeout=5)
    expect(out == "200000", f"got {out!r}, expected 200000")


@check(
    "2-3",
    "the shell waits for every process of a pipeline",
    "wait for each process of the job, not only the last one",
)
def _(sh):
    out, took = timed(sh, "probe sleep 0.5 | echo fast")
    expect(out == "fast", f"got {out!r}")
    expect(took >= 0.45, f"the prompt came back after {took:.1f} s, while the first process was still running")


@check(
    "2-3",
    "$? of a pipeline is the status of its last command",
    "take the status of the job's last process, not the first one",
)
def _(sh):
    for cmd, code in [("true | false", "1"), ("false | true", "0")]:
        sh.run(cmd)
        out = sh.status()
        expect(out == code, f"$? is {out!r} after '{cmd}', expected {code}")


@check(
    "2-3",
    "every process has only fds 0, 1, 2 open",
    "close every pipe end that a process does not use",
)
def _(sh):
    pr = probe_lines(sh.run("probe info < data.txt | probe info | probe info > fds.txt"))
    expect(len(pr) == 3, f"expected 3 probe reports, got {len(pr)}")
    bad = [p["fds"] for p in pr if p["fds"] != "0,1,2"]
    expect(not bad, f"open fds: {' / '.join(bad)}, expected 0,1,2")


@check(
    "2-3",
    "a failing command inside a pipeline",
    "a child whose exec failed must _exit(); the rest of the pipeline still runs",
)
def _(sh):
    out = sh.run("nosuchcmd | wc -l")
    expect("command not found" in out and out.split("\n")[-1].strip() == "0", f"got {out!r}")


@check(
    "2-3",
    "every command is a child of the shell",
    "the shell itself must fork() every command; running the line through another shell is not allowed",
)
def _(sh):
    expect_children_of_shell(sh, "probe info < data.txt | probe info | probe info > parents.txt", 3)


# ---------------- 3-1 Background jobs ----------------
@check(
    "3-1",
    "& returns at once, prints [jid] pid, $? = 0",
    "do not wait for a background job; starting it counts as success",
)
def _(sh):
    sh.run("false")
    out, took = timed(sh, "probe sleep 2 &")
    expect(took < 1, "the shell waited for the background job")
    m = re.search(r"\[1\] (\d+)", out)
    expect(m, f"expected '[1] <pid>', got {out!r}")
    expect(int(m.group(1)) in sh.live_children(), "the printed pid is not the background process")
    out = sh.status()
    expect(out == "0", f"$? is {out!r} after starting a background job, expected 0")


@check("3-1", "the shell keeps working while a job runs in the background", "do not wait for a background job")
def _(sh):
    sh.run("probe sleep 2 &")
    out, took = timed(sh, "echo hi")
    expect(out == "hi" and took < 1, f"'echo hi' printed {out!r} after {took:.1f} s")


@check(
    "3-1",
    "a foreground command waits only for itself",
    "wait for the pids of this job, not for any child (waitpid(p->pid, ...))",
)
def _(sh):
    sh.run("probe sleep 1.5 &")
    _, took = timed(sh, "probe sleep 0.3")
    expect(took < 1.0, f"took {took:.1f} s: the shell also waited for the longer background job")
    sh.run("probe sleep 0.3 &")
    _, took = timed(sh, "probe sleep 1")
    expect(took >= 0.9, f"took {took:.1f} s: the shell stopped waiting when the background job ended")


@check("3-1", "job numbers count up", "each new job gets the next job number")
def _(sh):
    a = sh.run("probe sleep 1 &")
    b = sh.run("probe sleep 1 &")
    expect(a.startswith("[1] ") and b.startswith("[2] "), f"got {a!r} and {b!r}")


@check(
    "3-1",
    "a background job does not read the terminal",
    "give a background job /dev/null as its stdin",
)
def _(sh):
    sh.run("cat &")
    # Finished (zombie) or already reaped both count; a live cat is waiting for the terminal.
    expect(wait_for(lambda: not sh.live_children(), 1.0), "cat is still waiting for input")


@check(
    "3-1",
    "a background pipeline with redirection",
    "only a process whose stdin is still the terminal gets /dev/null; pipe ends and '<' stay",
)
def _(sh):
    sh.run("cat data.txt | wc -l > bg.txt &")
    expect(
        wait_for(lambda: (read_file("bg.txt") or "").strip() == "8", 2.0),
        f"bg.txt contains {read_file('bg.txt')!r}, expected 8",
    )


# ---------------- 3-2 Reaping ----------------
@check(
    "3-2",
    "a finished job is reported Done exactly once",
    "update_bg_jobs() must hand every finished pid to mark_process_status()",
)
def _(sh):
    sh.run("probe sleep 0.3 &")
    time.sleep(0.6)
    out = sh.run("")
    expect(re.search(r"\[1\]\s+Done\s+probe sleep 0.3 &", out), f"no 'Done' message, got {out!r}")
    expect("Done" not in sh.run(""), "'Done' was reported twice")


@check(
    "3-2",
    "jobs finishing together are all reported",
    "several children can end between two prompts: loop until waitpid() returns 0",
)
def _(sh):
    for _ in range(3):
        sh.run("probe sleep 0.4 &")
    time.sleep(0.8)
    out = sh.run("")
    expect(out.count("Done") == 3, f"{out.count('Done')} 'Done' lines at once, expected 3")


@check("3-2", "no zombies after 30 background jobs", "reap finished children with waitpid(-1, ..., WNOHANG)")
def _(sh):
    for _ in range(30):
        sh.run("probe exit 0 &")
    time.sleep(0.3)
    sh.run("")
    expect(not sh.zombies(), f"{len(sh.zombies())} zombie processes left")


@check("3-2", "a background pipeline is Done when all its processes end", "a job is done when every process is reaped")
def _(sh):
    sh.run("probe sleep 0.2 | probe sleep 0.8 &")
    time.sleep(0.5)
    expect("Done" not in sh.run(""), "reported Done while a process was still running")
    time.sleep(0.6)
    expect("Done" in sh.run(""), "no 'Done' message")
    expect(not sh.zombies(), "zombie processes left")


@check(
    "3-2",
    "the prompt is not blocked while background jobs run",
    "WNOHANG makes waitpid() return at once when no child has finished",
)
def _(sh):
    sh.run("probe sleep 3 &")
    for _ in range(3):
        out, took = timed(sh, "echo ok")
        expect(out == "ok" and took < 1, f"'echo ok' took {took:.1f} s")


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------
TTY = sys.stdout.isatty()
COLOR = TTY and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"
UNICODE = (sys.stdout.encoding or "").lower().replace("-", "") == "utf8"

if UNICODE:
    BOX = dict(h="─", v="│", tl="┌", tm="┬", tr="┐", ml="├", mm="┼", mr="┤", bl="└", bm="┴", br="┘")
    PASS, FAIL, SKIP, ARROW = "✔", "✘", "–", "→"
else:
    BOX = dict(h="-", v="|", tl="+", tm="+", tr="+", ml="+", mm="+", mr="+", bl="+", bm="+", br="+")
    PASS, FAIL, SKIP, ARROW = "ok", "X", "-", "->"


def paint(text, *codes):
    if not COLOR:
        return text
    sgr = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "cyan": "36"}
    return "\033[" + ";".join(sgr[c] for c in codes) + "m" + text + "\033[0m"


def progress(i, n, item):
    if TTY:
        text = f"  running check {i}/{n}  {item['req']}  {item['name']}"
        sys.stdout.write("\r\033[K" + paint(text[:78], "dim"))
        sys.stdout.flush()


def clear_progress():
    if TTY:
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()


def table(header, rows, aligns, sections):
    """Draw a box table. rows: list of (cells, colors); sections: row indexes that start a new block."""
    widths = [max(len(header[c]), *(len(r[0][c]) for r in rows)) for c in range(len(header))]

    def line(left, mid, right):
        return BOX[left] + BOX[mid].join(BOX["h"] * (w + 2) for w in widths) + BOX[right]

    def cells(values, colors):
        out = []
        for v, w, a, col in zip(values, widths, aligns, colors):
            text = v.center(w) if a == "c" else v.rjust(w) if a == "r" else v.ljust(w)
            out.append(" " + (paint(text, *col) if col else text) + " ")
        return BOX["v"] + BOX["v"].join(out) + BOX["v"]

    print(line("tl", "tm", "tr"))
    print(cells(header, [("bold",)] * len(header)))
    for i, (values, colors) in enumerate(rows):
        if i in sections:
            print(line("ml", "mm", "mr"))
        print(cells(values, colors))
    print(line("bl", "bm", "br"))


def field(label, text, width, color=None):
    indent = 4 + 8
    lines = textwrap.wrap(text, width - indent) or [""]
    out = [" " * 4 + paint(label.ljust(8), "dim") + (paint(lines[0], *color) if color else lines[0])]
    out += [" " * indent + (paint(t, *color) if color else t) for t in lines[1:]]
    return "\n".join(out)


def report(results):
    reqs = list(REQUIREMENTS)
    rows, sections = [], set()
    total = passed_checks = ran_checks = 0
    failed_reqs = []
    for level, title in LEVELS.items():
        sections.add(len(rows))
        for i, r in enumerate(x for x in reqs if x.startswith(f"{level}-")):
            res = results[r]
            ok_n = sum(1 for x in res if x["error"] is None)
            ran_checks += len(res)
            passed_checks += ok_n
            level_cell = f"{level}  {title}" if i == 0 else ""
            if not res:
                rows.append(
                    ([level_cell, r, REQUIREMENTS[r], "", f"{SKIP} skip"], [None, None, None, None, ("yellow",)])
                )
                continue
            ok = ok_n == len(res)
            total += ok
            if not ok:
                failed_reqs.append(r)
            mark = f"{PASS} 1" if ok else f"{FAIL} 0"
            color = ("green",) if ok else ("red", "bold")
            rows.append(
                ([level_cell, r, REQUIREMENTS[r], f"{ok_n}/{len(res)}", mark], [None, None, None, color, color])
            )
    sections.add(len(rows))
    rows.append(
        (["", "", "Total", f"{passed_checks}/{ran_checks}", f"{total} / 7"], [None, None, ("bold",), None, ("bold",)])
    )

    print()
    print(paint("  OS Lab 2 grader", "bold"))
    table(["Level", "Step", "Requirement", "Checks", "Score"], rows, ["l", "c", "l", "r", "c"], sections)

    width = 78
    for r in reqs:
        res = results[r]
        failed = [x for x in res if x["error"] is not None]
        if not failed and not (VERBOSE and res):
            continue
        print()
        if failed:
            head = f"{FAIL} {r} {REQUIREMENTS[r]}  ·  {len(failed)} of {len(res)} checks failed"
            print(paint(head, "red", "bold"))
        else:
            print(paint(f"{PASS} {r} {REQUIREMENTS[r]}", "green", "bold"))
        for x in res:
            if x["error"] is None:
                if VERBOSE:
                    print("  " + paint(PASS, "green") + " " + x["name"])
                continue
            print("  " + paint(FAIL, "red") + " " + paint(x["name"], "bold"))
            if x["typed"]:
                print(field("typed", f"  {ARROW}  ".join(x["typed"][-4:]), width))
            print(field("result", x["error"], width))
            print(field("hint", x["hint"], width, ("cyan",)))

    print()
    if failed_reqs:
        nxt = failed_reqs[0]
        print(f"  {paint('Next step', 'bold')}  {nxt} {REQUIREMENTS[nxt]} {ARROW} TODO [{nxt}] in src/")
    elif ran_checks == len(CHECKS):
        print(f"  {paint('All checks passed.', 'green', 'bold')}")
    if not VERBOSE:
        print(f"  {paint('Details  ', 'bold')}  ./grade.sh -v lists every check")
    expected = os.environ.get("GRADER_EXPECTED_CHECKSUM", "")
    if not expected:
        note = ""
    elif expected == CHECKSUM:
        note = "  " + paint(f"{PASS} matches README", "green")
    else:
        note = "  " + paint(
            f"{FAIL} differs from README ({expected}): run git pull, or restore grade.sh and tests/", "red", "bold"
        )
    print(f"  {paint('Checksum ', 'bold')}  {CHECKSUM}{note}")


def write_json(results):
    """For the TA self-test (tools/selftest.sh): GRADER_RESULT_JSON=<file> stores the per-requirement result."""
    path = os.environ.get("GRADER_RESULT_JSON")
    if not path:
        return
    out = {
        r: {"ran": len(res), "passed": bool(res) and all(x["error"] is None for x in res)}
        | {"failed": [x["name"] for x in res if x["error"] is not None]}
        for r, res in results.items()
    }
    with open(path, "w") as f:
        json.dump(out, f, indent=2)


def main():
    only = os.environ.get("GRADER_ONLY")  # run only matching checks, e.g. GRADER_ONLY=2-2
    todo = [c for c in CHECKS if not only or only in f"{c['req']} {c['name']}"]
    results = {r: [] for r in REQUIREMENTS}
    try:
        for i, c in enumerate(todo, 1):
            progress(i, len(todo), c)
            sh = None
            error = None
            try:
                sh = Shell()
                c["func"](sh)
            except CheckFailed as e:
                error = str(e)
            except Exception as e:  # unexpected grader-side problem
                error = f"{type(e).__name__}: {e}"
            finally:
                typed = sh.typed if sh else []
                if sh:
                    sh.close()
            results[c["req"]].append({"name": c["name"], "hint": c["hint"], "error": error, "typed": typed})
        clear_progress()
        report(results)
        write_json(results)
    except KeyboardInterrupt:
        clear_progress()
        print("\n  Interrupted.")
        sys.exit(130)
    finally:
        # Leave nothing behind, however the run ends.
        shutil.rmtree(WORKDIR, ignore_errors=True)


if __name__ == "__main__":
    main()
