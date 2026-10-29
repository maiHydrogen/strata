"""Run STAAD.Pro on a .std file from WSL.

STAAD runs on the Windows side. The input is copied into a Windows folder
(STAAD rewrites the .std it runs), the analysis engine is started through
WSL interop, and the .anl is read back. ManualRunner is the fallback: it
writes the .std and waits for a person to run it and save the .anl.
"""

import os
import subprocess
import time

DEFAULT_ENGINE = "/mnt/c/Program Files/Bentley/Engineering/STAAD.Pro 2026/STAAD/SProStaad/SProStaad.exe"
DEFAULT_WORKDIR = "/mnt/c/Users/Public/Documents/STRATA"


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
        if os.path.exists(anl_path):
            os.remove(anl_path)
        with open(std_path, "w", encoding="ascii", newline="\r\n") as f:
            f.write(std_text)
        proc = subprocess.run([self.engine, _windows_path(std_path)], capture_output=True,
                              text=True, timeout=self.timeout)
        if not os.path.exists(anl_path):
            raise RuntimeError(f"STAAD produced no {name}.anl (exit {proc.returncode}): "
                               f"{proc.stdout[-500:]} {proc.stderr[-500:]}")
        with open(anl_path, encoding="latin-1") as f:
            return f.read()


class ManualRunner:
    """Write the .std and wait for its .anl to appear (a person runs STAAD)."""

    def __init__(self, workdir, timeout=3600, poll=5):
        self.workdir, self.timeout, self.poll = workdir, timeout, poll

    def __call__(self, std_text: str, name: str) -> str:
        os.makedirs(self.workdir, exist_ok=True)
        std_path = os.path.join(self.workdir, f"{name}.std")
        anl_path = os.path.join(self.workdir, f"{name}.anl")
        with open(std_path, "w", encoding="ascii", newline="\r\n") as f:
            f.write(std_text)
        written = os.path.getmtime(std_path)
        print(f"Run {std_path} in STAAD.Pro; waiting for {anl_path}")
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            if os.path.exists(anl_path) and os.path.getmtime(anl_path) > written:
                time.sleep(1)  # let STAAD finish writing
                with open(anl_path, encoding="latin-1") as f:
                    return f.read()
            time.sleep(self.poll)
        raise TimeoutError(f"No {anl_path} after {self.timeout} s")
