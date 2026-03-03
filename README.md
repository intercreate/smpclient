# Simple Management Protocol (SMP) Client

`smpclient` implements the transport layer of the Simple Management Protocol.  This library can be
used as a dependency in applications that use SMP over **serial (UART or USB)**, **Bluetooth (BLE)**,
or **UDP** connections.  Some abstractions are provided for common routines like upgrading device
firmware.

If you don't need a library with the transport layer implemented, then you might prefer to use
[smp](https://github.com/JPHutchins/smp) instead.  The SMP specification can be found
[here](https://docs.zephyrproject.org/latest/services/device_mgmt/smp_protocol.html).

If you'd like an SMP CLI application instead of a library, then you should try
[smpmgr](https://github.com/intercreate/smpmgr).

## Install

`smpclient` is [distributed by PyPI](https://pypi.org/project/smpclient/) and can be installed with `uv`, `pip`, and other dependency managers.

## User Documentation

Documentation is in the source code so that it is available to your editor.
An online version is generated and available [here](https://intercreate.github.io/smpclient/).

## Development Quickstart

> Assumes that you've already [setup your development environment](#development-environment-setup).

1. run `uv sync` when pulling in new changes
2. run `uv run task fix` after making changes (fast)
3. run `uv run task all` after making changes (thorough)
4. add library dependencies with `uv`:
   ```
   uv add <my_new_dependency>
   ```
5. add test or other development dependencies:
   ```
   uv add --group dev <my_dev_dependency>
   ```
6. run tests for all supported python versions:
   ```
   uv run task matrix
   ```

## Development Environment Setup

### Install Dependencies

- uv: https://docs.astral.sh/uv/getting-started/installation/

### Create the venv

```
uv sync
```

### Verify Your Setup

```
uv run task all
```

### Enable the githooks

```
git config core.hooksPath .githooks
```
