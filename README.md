# Auto_ext

PyQt5 GUI + plugin-based automation for the Cadence post-layout extraction flow
(`si` / `strmout` / `calibre` / `qrc` / `jivaro`).

Status: **under construction**. Phase 1 (skeleton + offline wheel pipeline) only.
See `docs/` (future) and the implementation plan for phase-by-phase scope.

## Layout (high-level)

```
Auto_ext/
├── auto_ext/        # Python package (core/ tools/ ui/ cli.py migrate.py)
├── config/          # workspace.yaml + cells.yaml + profiles/
├── recipes/         # one portable extraction configuration per file
├── templates/       # the catalog's .j2 files (see auto_ext/catalog/)
├── examples/legacy/ # the v1 config pair, kept as migration input
├── scripts/         # download_wheels.py (Windows) + install_offline.sh (Linux)
├── tests/           # unit + integration tests (with mocks/)
├── pyproject.toml
└── run.sh           # entry: chdir to ../ (workarea) then python -m auto_ext
```

## Phase 1 quick start

### Windows dev box — download wheels

```
python scripts/download_wheels.py
```

Produces `wheels/*.whl` and `wheels/MANIFEST.txt` targeting Python 3.11 /
`manylinux2014_x86_64` (the server's glibc 2.17 ceiling).

### Linux server — install offline

```
cd Auto_ext
bash scripts/install_offline.sh
```

Installs every bundled third-party wheel into `Auto_ext/_vendor/`
(`pip install --target`; gitignored, never packed, never touched by
`deploy.sh`) and runs a smoke test against it. Nothing goes into
`~/.local`: deleting the project directory removes the dependencies too,
and other `python3.11` programs never see them. Each rerun rebuilds
`_vendor/` from scratch and swaps it in only once the smoke test passed.
Copies an older installer left in `~/.local` are not removed; the
installer lists them and prints the `pip uninstall` command.

**The `auto_ext` package itself is NOT pip-installed**: `run.sh` puts
the project root, then `_vendor/`, on `PYTHONPATH` instead, so no absolute
workarea path ends up in any site-packages (and no stray entry in
`pip list`). Without `_vendor/` (e.g. the Windows dev venv) `run.sh` adds
only the project root, as before.

### Launch

```
./run.sh [args]                                 # chdir to ../ (workarea), set PYTHONPATH, python -m auto_ext
# or, from anywhere (bash or csh) -- but see below, prefer ./run.sh:
env PYTHONPATH=/abs/path/to/Auto_ext:/abs/path/to/Auto_ext/_vendor python3.11 -m auto_ext [args]
```

**Child processes get your environment, not the launcher's.** `run.sh`'s
`PYTHONPATH` (with `_vendor/`), `PYTHONSAFEPATH` and the Qt `LD_LIBRARY_PATH`
are for Auto_ext's own interpreter. It records what you had
(`AUTO_EXT_CALLER_*`), and every process Auto_ext starts -- si, strmout,
calibre, qrc, jivaro, Calibre Interactive, xdg-open -- gets those original
values back (`auto_ext/core/child_env.py`), so a Python an EDA tool starts
never imports from `_vendor/`. The bare `env PYTHONPATH=...` form above has no
such record, and its children inherit `_vendor/`.

**Relative paths mean what you expect.** The chdir above is not optional --
`si -batch` reads `si.env` from cwd -- but it would otherwise silently
reinterpret every relative path you typed against the workarea instead of
against where you are standing, so `./run.sh check-env --config-dir config`
died with "Directory 'config' does not exist" while `config/` sat right there.
`run.sh` therefore absolutizes path arguments from your cwd *before* the
chdir. Output *patterns* (`--to`, `--layout-out`) are left alone on purpose:
they are workarea-relative by design and may carry `{cell}` placeholders.

`AUTO_EXT_ARGV_DEBUG=1 ./run.sh ...` prints what the launcher would pass on
(argv and the composed `PYTHONPATH`) and exits, without needing a working
Python -- for when a path argument is not landing where you meant.

## Tests

```
cd Auto_ext
pytest            # pyproject.toml sets pythonpath = ["."] so no install needed
```

Phase 1 only ships a sanity test; real test coverage lands with the core
modules in later phases.
