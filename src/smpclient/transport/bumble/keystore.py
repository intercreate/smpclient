"""Keystore strategies for persisting bumble bond keys.

`SMPBumbleTransport` accepts a `KeystoreStrategy` that describes *where* bond
keys (LTKs, IRKs, CSRKs) should be stored.  Persistence choice is exposed as a
sum type so callers exhaustively handle the available options.
"""

import os
import tempfile
from pathlib import Path
from typing import Final, NamedTuple, TypeAlias

from bumble.keys import JsonKeyStore, KeyStore, MemoryKeyStore
from typing_extensions import assert_never

DEFAULT_FILENAME: Final = "smpclient_bumble_bonds.json"


class InvalidKeystoreFilename(ValueError):
    """Raised when `Tempfile.filename` / `Local.filename` is not a bare filename."""


def _check_bare_filename(filename: str) -> None:
    # `Tempfile` and `Local` join `filename` onto a system-chosen directory.
    # Absolute paths or path separators would let the caller escape that
    # directory; reject them so the strategy contract holds.
    if os.path.isabs(filename) or os.sep in filename or (os.altsep and os.altsep in filename):
        raise InvalidKeystoreFilename(
            f"{filename!r} must be a bare filename — use Custom(path=...) for an arbitrary location"
        )


class Tempfile(NamedTuple):
    """`tempfile.gettempdir()/<filename>`.  On Linux, `/tmp` is typically cleared at boot."""

    filename: str = DEFAULT_FILENAME


class Local(NamedTuple):
    """`platformdirs.user_data_dir("smpclient", "intercreate")/<filename>`."""

    filename: str = DEFAULT_FILENAME


class Custom(NamedTuple):
    """User-supplied path; the parent directory is created if missing."""

    path: Path


class ExistingCustom(NamedTuple):
    """User-supplied path that must already exist; raises `FileNotFoundError` if not."""

    path: Path


class InMemory(NamedTuple):
    """Bonds vanish on process exit."""


KeystoreStrategy: TypeAlias = Tempfile | Local | Custom | ExistingCustom | InMemory


def resolve(strategy: KeystoreStrategy, namespace: str) -> KeyStore:
    """Resolve a `KeystoreStrategy` to a concrete bumble `KeyStore`.

    Args:
        strategy: The strategy to resolve.
        namespace: The keystore namespace, typically the local BD_ADDR.
            Ignored by `InMemory`.

    Returns:
        A bumble `KeyStore` ready to be assigned to `Device.keystore`.

    Raises:
        FileNotFoundError: when `ExistingCustom.path` does not exist.
    """
    match strategy:
        case Tempfile(filename):
            _check_bare_filename(filename)
            return JsonKeyStore(
                namespace=namespace,
                filename=os.path.join(tempfile.gettempdir(), filename),
            )
        case Local(filename):
            _check_bare_filename(filename)
            from platformdirs import user_data_dir

            data_dir: Final = Path(user_data_dir("smpclient", "intercreate"))
            data_dir.mkdir(parents=True, exist_ok=True)
            return JsonKeyStore(namespace=namespace, filename=str(data_dir / filename))
        case Custom(path):
            path.parent.mkdir(parents=True, exist_ok=True)
            return JsonKeyStore(namespace=namespace, filename=str(path))
        case ExistingCustom(path):
            if not path.exists():
                raise FileNotFoundError(f"ExistingCustom keystore not found: {path}")
            return JsonKeyStore(namespace=namespace, filename=str(path))
        case InMemory():
            return MemoryKeyStore()
        case _:
            assert_never(strategy)
