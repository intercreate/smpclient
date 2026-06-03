"""End-to-end integration tests against real Zephyr SMP servers.

The servers are vendored prebuilt fixtures (`tests/fixtures/smp-server/`) launched
as subprocesses; see `tests.integration.servers`. These tests are Linux-only and
are excluded from the default `task test` run (they carry the `integration`
marker); run them with `task test-integration`.
"""
