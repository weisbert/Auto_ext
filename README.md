# Auto_ext

PyQt5 GUI + plugin-based automation for the Cadence post-layout extraction flow
(`si` / `strmout` / `calibre` / `qrc` / `jivaro`).

Status: **under construction**. Phase 1 (skeleton + offline wheel pipeline) only.
See `docs/` (future) and the implementation plan for phase-by-phase scope.

## Layout (high-level)

```
Auto_ext/
├── auto_ext/        # Python package (core/ tools/ ui/ cli.py migrate.py)
├── config/          # project.yaml + tasks.yaml live here
├── templates/       # parameterized .j2 + manifest.yaml per template
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

Runs `pip install --no-index --find-links ./wheels/ -e .[dev]` and a smoke test.

## Tests

```
pytest
```

Phase 1 only ships a sanity test; real test coverage lands with the core
modules in later phases.
