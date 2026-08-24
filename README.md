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

Installs every bundled third-party wheel and runs a smoke test.
**The `auto_ext` package itself is NOT pip-installed**: `run.sh` puts
the project root on `PYTHONPATH` instead, so no absolute workarea path
ends up in `~/.local/lib/python3.11/site-packages/` (and no stray entry
in `pip list`).

### Launch

```
./run.sh [args]                                 # chdir to ../ (workarea), set PYTHONPATH, python -m auto_ext
# equivalently, from anywhere:
PYTHONPATH=/abs/path/to/Auto_ext python3.11 -m auto_ext [args]
```

**Relative paths mean what you expect.** The chdir above is not optional --
`si -batch` reads `si.env` from cwd -- but it would otherwise silently
reinterpret every relative path you typed against the workarea instead of
against where you are standing, so `./run.sh check-env --config-dir config`
died with "Directory 'config' does not exist" while `config/` sat right there.
`run.sh` therefore absolutizes path arguments from your cwd *before* the
chdir. Output *patterns* (`--to`, `--layout-out`) are left alone on purpose:
they are workarea-relative by design and may carry `{cell}` placeholders.

`AUTO_EXT_ARGV_DEBUG=1 ./run.sh ...` prints what the launcher would pass on
and exits, without needing a working Python -- for when a path argument is not
landing where you meant.

## Tests

```
cd Auto_ext
pytest            # pyproject.toml sets pythonpath = ["."] so no install needed
```

Phase 1 only ships a sanity test; real test coverage lands with the core
modules in later phases.
