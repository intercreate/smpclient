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

`smpclient` is [distributed by PyPI](https://pypi.org/project/smpclient/) and can be installed with `poetry`, `pip`, and other dependency managers.

## User Documentation

Documentation is in the source code so that it is available to your editor.
An online version is generated and available [here](https://intercreate.github.io/smpclient/).

## Development Quickstart

> Assumes that you've already [setup your development environment](#development-environment-setup).

1. activate [envr](https://github.com/JPhutchins/envr), the environment manager for **bash**, **zsh**, and **PS**:
   ```
   . ./envr.ps1
   ```
2. run `poetry install` when pulling in new changes
3. run `lint` after making changes
4. run `test` after making changes
5. add library dependencies with `poetry`:
   ```
   poetry add <my_new_dependency>
   ```
6. add test or other development dependencies using [poetry groups](https://python-poetry.org/docs/managing-dependencies#dependency-groups):
   ```
   poetry add -G dev <my_dev_dependency>
   ```
7. run tests for all supported python versions:
   ```
   tox
   ```

## Development Environment Setup

### Install Dependencies

- poetry: https://python-poetry.org/docs/#installation

### Create the venv

```
poetry install
```

The `venv` should be installed to `.venv`.

### Activate envr

> [envr](https://github.com/JPhutchins/envr) supports **bash**, **zsh**, and **PS** in Linux, MacOS, and Windows.  If you are using an unsupported shell, you can activate the `.venv` environment manually, use `poetry run` and `poetry shell`, and refer to `envr-default` for useful aliases.

```
. ./envr.ps1
```

### Verify Your Setup

To verify the installation, make sure that all of the tests are passing using these envr aliases:

```
lint
test
```

### Enable the githooks

> The pre-commit hook will run the linters but not the unit tests.

```
git config core.hooksPath .githooks
```
