"""Shared pgserver support — Stage 8 (test infra).

PostgreSQL test clusters live under ``~/.pg-tmp`` (or
``$TAVESSA_PG_TMP``), NEVER under /tmp: the sandbox /tmp is a 512M
tmpfs and repeated initdb runs filled it, breaking every Postgres
fixture with "No space left on device" (Stages 5–7 all hit this).

One unique directory per cluster. ``server.cleanup()`` removes the
data directory; :func:`remove_pg_cluster_dir` is the belt-and-braces
cleanup in the fixture teardown and is scoped to paths this helper
created — it never deletes unrelated user data.
"""
import os
import shutil
import uuid
from pathlib import Path


def pg_base_dir() -> Path:
    """The shared base for test clusters (created on demand)."""
    base = Path(os.environ.get("TAVESSA_PG_TMP", Path.home() / ".pg-tmp"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def pg_cluster_dir(name: str) -> Path:
    """A fresh, not-yet-created directory for one pgserver cluster.

    The leaf path is deliberately NOT created here: ``initdb``
    creates it. Uniqueness per call keeps parallel/sequential runs
    from colliding.
    """
    return pg_base_dir() / f"{name}-{uuid.uuid4().hex[:8]}"


def remove_pg_cluster_dir(path: Path) -> None:
    """Remove a cluster directory created by :func:`pg_cluster_dir`
    (no-op when it is already gone)."""
    shutil.rmtree(path, ignore_errors=True)
