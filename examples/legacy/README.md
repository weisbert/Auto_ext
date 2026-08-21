# The v1 configuration, kept as migration input

Everything under this directory is the **old** file format. Auto_ext no longer
runs from it: `project.yaml` + `tasks.yaml` with their template slots and
`*.j2.manifest.yaml` knob sidecars were replaced by

    config/workspace.yaml       where the Cadence work lands
    config/cells.yaml           the DUT table
    config/profiles/<id>.yaml   the PDK facts
    config/resources.yaml       CPU / turbo / licence counts
    recipes/<name>.yaml         the extraction parameters

and pointing `auto-ext run --config-dir` at a v1 directory now fails with the
migration command instead of half-loading.

## Why it is still here

`auto-ext migrate` has to be able to read the thing it migrates away from, and
a migration proved only against a hand-written sample is not proved. These are
the *real* files the repository shipped, migrated in place:

| this directory                     | was                | migrated into        |
|------------------------------------|--------------------|----------------------|
| `config/project.yaml` + `tasks.yaml` | `<repo>/config/` | `<repo>/config/` + `<repo>/recipes/` |
| `demo/config/project.yaml` + `tasks.yaml` | `examples/demo/` | `examples/demo/config/` + `examples/demo/recipes/` |
| `templates/`                       | `<repo>/templates/` (with sidecars) | the catalog, `auto_ext/catalog/options.yaml` |

`tests/test_migrate.py` migrates both pairs on every run and asserts the result
describes the same runs the old files described. Six rows of
`auto_ext/catalog/options.yaml` also cite `templates/**/*.j2.manifest.yaml`
here as the provenance of their default — this is the only place a v1 sidecar
still exists.

## Reproducing the migration

```
auto-ext migrate --config-dir examples/legacy/config \
                 --out-root . \
                 --template-root examples/legacy/templates \
                 --write
```

Nothing under this directory is written to; `migrate` opens both source files
read-only. Existing output files are never overwritten, so delete the target
before re-running if you want it regenerated.

## Do not "fix" anything in here

The `<runset>` / `<pdk_subdir>` placeholders in `config/project.yaml`, the
`TODO_LIBRARY_NAME` cell in `config/tasks.yaml` and the hardcoded
`viewsToReduce av_extracted` in `templates/jivaro/default.xml.j2` are all
faithful copies of what shipped. Editing them would change what the migration
test proves.
