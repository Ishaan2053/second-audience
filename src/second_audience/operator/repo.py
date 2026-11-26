"""Git facts the operator records or enforces: clean files, the commit, the approver."""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    """A git command failed."""


@dataclass(frozen=True)
class Repo:
    """A git working tree, queried through the `git` CLI."""

    root: Path

    @classmethod
    def discover(cls, start: Path) -> "Repo":
        """The repository containing `start`."""
        top = _git(start, "rev-parse", "--show-toplevel")
        return cls(Path(top))

    def uncommitted(self, paths: list[Path]) -> list[str]:
        """Those of `paths` that are modified, staged, or untracked (not yet committed)."""
        relative = [str(path.resolve().relative_to(self.root.resolve())) for path in paths]
        output = _git(self.root, "status", "--porcelain", "--untracked-files=all", "--", *relative)
        # Each line is a two-letter status, a space, then the path ("?? new.md", " M a.yaml").
        return [line.strip().split(maxsplit=1)[1] for line in output.splitlines() if line.strip()]

    def head_commit(self) -> str:
        """The full hash of HEAD."""
        return _git(self.root, "rev-parse", "HEAD")

    def user_name(self) -> str:
        """`git config user.name`, recorded as the approver."""
        return _git(self.root, "config", "user.name")


def _git(cwd: Path, *args: str) -> str:
    """Run git and return stripped stdout; raise `GitError` on failure."""
    executable = shutil.which("git")
    if executable is None:
        raise GitError("git is not on PATH")
    result = subprocess.run(
        [executable, *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()
