"""
Path setup for the MPI matcher benchmark.

Mirrors the fixture pattern of tests/unit/mpi/test_matcher.py: the matcher is
imported as a bare module (`from matcher import ...`), so services/mpi must be
on sys.path before the benchmark module is imported. A module-level insert in
this conftest guarantees that, regardless of which other test directories are
collected in the same session.
"""
import sys
from pathlib import Path

_MPI_PATH = str(Path(__file__).parent.parent.parent / "services" / "mpi")
if _MPI_PATH not in sys.path:
    sys.path.insert(0, _MPI_PATH)
