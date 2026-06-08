"""CAA-source predicate (canonical Python implementation).

Walks up from start_dir looking for setup/src/init.ts. Max walk depth: 10 levels.
"""
import pathlib


def is_caa_source(start_dir: pathlib.Path) -> bool:
    """Return True iff a file at setup/src/init.ts exists in the walk-up from start_dir.

    Walk depth capped at 10 levels (per knowledge-audience-discipline.md).
    Exits the loop on filesystem-root reach (parent == self).
    """
    p = pathlib.Path(start_dir).resolve()
    for _ in range(10):
        if (p / "setup" / "src" / "init.ts").is_file():
            return True
        parent = p.parent
        if parent == p:
            break
        p = parent
    return False
