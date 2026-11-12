"""Run STAAD.Pro on a .std file from WSL.

STAAD runs on the Windows side. The input is copied into a Windows folder
(STAAD rewrites the .std it runs), the analysis engine is started hidden
through WSL interop, and the .anl is read back with STAAD's IS 13920 joint report
(<name>_13920.txt) appended. ManualRunner is the fallback: it writes the
.std and waits for a person to run it and save the .anl.
"""

import os
import subprocess
import time

DEFAULT_ENGINE = "/mnt/c/Program Files/Bentley/Engineering/STAAD.Pro 2026/STAAD/SProStaad/SProStaad.exe"
DEFAULT_WORKDIR = "/mnt/c/Users/Public/Documents/STRATA"


def joint_report_path(workdir, name):
    return os.path.join(workdir, f"{name}_13920.txt")


def read_report(workdir, name):
    """The .anl text, followed by the IS 13920 joint report when STAAD wrote one."""
    with open(os.path.join(workdir, f"{name}.anl"), encoding="latin-1") as f:
        text = f.read()
    joints = joint_report_path(workdir, name)
    if os.path.exists(joints):
        with open(joints, encoding="latin-1") as f:
            text += "\n" + f.read()
    return text


LINGER = 15  # s the engine may stay open after its report is complete


def _analysis_finished(anl_path):
    """True once STAAD has written the closing banner of the .anl."""
    try:
        with open(anl_path, "rb") as f:
            f.seek(max(0, os.path.getsize(anl_path) - 2000))
            return b"END OF ANALYSIS" in f.read().upper()
    except OSError:
        return False


def _windows_path(path):
    return subprocess.run(["wslpath", "-w", path], capture_output=True, text=True, check=True).stdout.strip()


class StaadRunner:
    def __init__(self, engine=None, workdir=None, timeout=600):
        self.engine = engine or os.environ.get("STAAD_ENGINE", DEFAULT_ENGINE)
        self.workdir = workdir or os.environ.get("STRATA_STAAD_WORKDIR", DEFAULT_WORKDIR)
        self.timeout = timeout

    def __call__(self, std_text: str, name: str) -> str:
        if not os.path.exists(self.engine):
            raise FileNotFoundError(f"STAAD engine not found: {self.engine} (set STAAD_ENGINE)")
        os.makedirs(self.workdir, exist_ok=True)
        std_path = os.path.join(self.workdir, f"{name}.std")
        anl_path = os.path.join(self.workdir, f"{name}.anl")
        for stale in (anl_path, joint_report_path(self.workdir, name)):
            if os.path.exists(stale):
                os.remove(stale)
        with open(std_path, "w", encoding="ascii", newline="\r\n") as f:
            f.write(std_text)
        # STAAD command line syntax: SProStaad.exe STAAD <input> [/s | /h]. /h runs hidden and
        # closes at the end; without it the engine waits for someone to click Done. Now and then
        # the engine stays open after writing a complete .anl, so the run also ends once the
        # report is finished and the engine has had LINGER seconds to close by itself.
        proc = subprocess.Popen([self.engine, "STAAD", _windows_path(std_path), "/h"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + self.timeout
        finished_at = None
        while proc.poll() is None:
            if time.time() > deadline:
                proc.kill()
                raise TimeoutError(f"STAAD did not finish {name} in {self.timeout} s")
            if finished_at is None and _analysis_finished(anl_path):
                finished_at = time.time()
            if finished_at is not None and time.time() - finished_at > LINGER:
                proc.kill()
                break
            time.sleep(0.5)
        if not os.path.exists(anl_path):
            raise RuntimeError(f"STAAD produced no {name}.anl (exit {proc.returncode})")
        return read_report(self.workdir, name)


class ManualRunner:
    """Write the .std and wait for its .anl to appear (a person runs STAAD)."""

    def __init__(self, workdir, timeout=3600, poll=5):
        self.workdir, self.timeout, self.poll = workdir, timeout, poll

    def __call__(self, std_text: str, name: str) -> str:
        os.makedirs(self.workdir, exist_ok=True)
        std_path = os.path.join(self.workdir, f"{name}.std")
        anl_path = os.path.join(self.workdir, f"{name}.anl")
        stale = joint_report_path(self.workdir, name)
        if os.path.exists(stale):
            os.remove(stale)
        with open(std_path, "w", encoding="ascii", newline="\r\n") as f:
            f.write(std_text)
        written = os.path.getmtime(std_path)
        print(f"Run {std_path} in STAAD.Pro; waiting for {anl_path}")
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            if os.path.exists(anl_path) and os.path.getmtime(anl_path) > written:
                time.sleep(1)  # let STAAD finish writing
                return read_report(self.workdir, name)
            time.sleep(self.poll)
        raise TimeoutError(f"No {anl_path} after {self.timeout} s")
