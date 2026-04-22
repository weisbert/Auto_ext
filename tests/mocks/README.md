# Mock EDA binaries

Drop-in replacements for `calibre`, `qrc`, `jivaro`, `si`, `strmout` used by
the integration tests so CI doesn't need a Cadence license.

Each script:

- echoes a one-line banner with the argv it received
- creates a realistic-looking artifact under `$AUTO_EXT_MOCK_OUT` (default `./mock_out`)
- exits 0 on the happy path
- returns a distinct nonzero exit code (and writes a failing report where applicable) when invoked with `--fail`

Integration tests put this directory at the head of `PATH` (or call the
scripts by absolute path) so the runner resolves `calibre` to the mock.

Distinct failure exit codes: calibre=1, qrc=2, jivaro=3, si=4, strmout=5.
