"""THE JAIL: every test (AI build, push, "run tests") runs inside bubblewrap, never on the box itself.
 - a fresh COPY of the sandbox's files (no .git) is the only writable place, mounted at /work
 - /usr read-only (python3 etc.), a private /tmp, /proc and /dev of its own, a minimal fake /etc (no real users, hosts, keys)
 - every namespace unshared: no network at all (only a dead loopback), own pid space, cannot see host processes
 - capabilities dropped, new session (no terminal injection), dies with the server
 - prlimit: address-space (memory) cap, process cap, file-size cap, CPU seconds; plus a wall-clock timeout (process group killed)
 - optional on the real box: systemd-run --user --scope with MemoryMax / TasksMax (a cgroup on top)
Nothing else of the box is visible inside: not /home, not /var/lib/teambox (other sandboxes, tokens, chats), not /opt (this app),
not /etc, not the gateway secret. If bwrap is missing the jail refuses - tests never run unjailed."""
import os
import platform
import shutil
import signal
import subprocess
import tempfile
import time

FAKE_ETC = {
    "passwd": "root:x:0:0:root:/root:/usr/bin/nologin\nsandbox:x:1000:1000:team sandbox:/tmp:/usr/bin/nologin\nnobody:x:65534:65534:nobody:/:/usr/bin/nologin\n",
    "group": "root:x:0:\nsandbox:x:1000:\nnobody:x:65534:\n",
    "hosts": "127.0.0.1 localhost\n",
    "hostname": "team-sandbox-jail\n",
    "resolv.conf": "# no network inside the team sandbox jail\n",
}


class JailError(Exception):
    pass


def available(jcfg):
    if platform.system() != "Linux":
        return False, "the jail needs Linux (bubblewrap); this machine is %s" % platform.system()
    if jcfg.get("mode") != "bwrap":
        return False, "jail mode must be 'bwrap'"
    for k in ("bwrap", "prlimit"):
        if not (os.path.exists(jcfg[k]) and os.access(jcfg[k], os.X_OK)):
            return False, "%s not found at %s" % (k, jcfg[k])
    return True, ""


def _fake_etc(root):
    d = os.path.join(root, "etc")
    os.makedirs(d, exist_ok=True)
    for name, text in FAKE_ETC.items():
        with open(os.path.join(d, name), "w", encoding="utf8") as fh:
            fh.write(text)
    return d


def copy_tree(src, dst, max_bytes):
    """Copy the sandbox files (no .git, no symlinks followed: links are copied as links and only resolve INSIDE the jail)."""
    total = 0
    for dp, ds, fs in os.walk(src):
        ds[:] = [d for d in ds if d != ".git"]
        rel = os.path.relpath(dp, src)
        out = os.path.join(dst, rel) if rel != "." else dst
        os.makedirs(out, exist_ok=True)
        for d in list(ds):
            if os.path.islink(os.path.join(dp, d)):
                os.symlink(os.readlink(os.path.join(dp, d)), os.path.join(out, d))
                ds.remove(d)
        for f in fs:
            s = os.path.join(dp, f)
            if os.path.islink(s):
                os.symlink(os.readlink(s), os.path.join(out, f))
                continue
            total += os.path.getsize(s)
            if total > max_bytes:
                raise JailError("the sandbox is bigger than the %d MB limit" % (max_bytes // 2 ** 20))
            shutil.copy2(s, os.path.join(out, f))
    return total


def command(jcfg, work, etc, argv):
    mem = int(jcfg["memory_mb"]) * 2 ** 20
    lim = [jcfg["prlimit"], "--as=%d" % mem, "--nproc=%d" % int(jcfg["max_procs"]), "--fsize=%d" % (int(jcfg["max_file_mb"]) * 2 ** 20),
           "--cpu=%d" % (int(jcfg["timeout_s"]) + 5), "--nofile=256", "--core=0", "--"]
    bw = [jcfg["bwrap"], "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL",
          "--hostname", "team-sandbox-jail",
          "--ro-bind", "/usr", "/usr",
          "--symlink", "usr/bin", "/bin", "--symlink", "usr/bin", "/sbin", "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib", "/lib64",
          "--ro-bind", etc, "/etc",
          "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/run",
          "--bind", work, "/work", "--chdir", "/work",
          "--clearenv", "--setenv", "PATH", "/usr/bin", "--setenv", "HOME", "/tmp", "--setenv", "LANG", "C.UTF-8",
          "--setenv", "PYTHONDONTWRITEBYTECODE", "1", "--setenv", "TEAM_SANDBOX", "1", "--setenv", "USER", "sandbox",
          "--uid", "1000", "--gid", "1000", "--"]
    pre = []
    if jcfg.get("systemd_scope"):
        pre = [jcfg["systemd_run"], "--user", "--scope", "--quiet", "-p", "MemoryMax=%dM" % int(jcfg["memory_mb"]),
               "-p", "TasksMax=%d" % int(jcfg["max_procs"]), "--"]
    return pre + lim + bw + list(argv)


def run(jcfg, src_dir, argv, tmp_root, max_bytes=200 * 2 ** 20):
    """Run argv inside the jail on a COPY of src_dir. -> dict(ok, exit, output, seconds, timed_out). Raises JailError if no jail."""
    ok, why = available(jcfg)
    if not ok:
        raise JailError(why)
    if not argv or not all(isinstance(a, str) for a in argv):
        raise JailError("the test command must be a list of words")
    os.makedirs(tmp_root, exist_ok=True)
    root = tempfile.mkdtemp(prefix="jail-", dir=tmp_root)
    try:
        work = os.path.join(root, "work")
        copy_tree(src_dir, work, max_bytes)
        etc = _fake_etc(root)
        cmd = command(jcfg, work, etc, argv)
        t0 = time.time()
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        timed_out = False
        try:
            out, _ = p.communicate(timeout=int(jcfg["timeout_s"]))
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            out, _ = p.communicate()
        cap = int(jcfg["output_max_kb"]) * 1024
        text = (out or b"")[-cap:].decode("utf8", "replace")
        if timed_out:
            text += "\n[TIMEOUT after %s s - the test was ended]" % jcfg["timeout_s"]
        return {"ok": p.returncode == 0 and not timed_out, "exit": p.returncode, "output": text, "seconds": round(time.time() - t0, 2),
                "timed_out": timed_out, "argv": list(argv)}
    finally:
        shutil.rmtree(root, ignore_errors=True)
