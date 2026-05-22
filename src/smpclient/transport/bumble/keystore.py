"""Keystore strategies for persisting bumble bond keys.

`SMPBumbleTransport` accepts a `KeystoreStrategy` that describes *where* bond
keys (LTKs, IRKs, CSRKs) should be stored.  Persistence choice is exposed as a
sum type so callers exhaustively handle the available options.
"""

import os
import tempfile
from pathlib import Path
from typing import Final, NamedTuple, TypeAlias, assert_never

from bumble.keys import JsonKeyStore, KeyStore, MemoryKeyStore

DEFAULT_FILENAME: Final = "smpclient_bumble_bonds.json"


class Tempfile(NamedTuple):
    """`tempfile.gettempdir()/<filename>`.  On Linux, `/tmp` is typically cleared at boot."""

    filename: str = DEFAULT_FILENAME


class Local(NamedTuple):
    """`platformdirs.user_data_dir("smpclient", "intercreate")/<filename>`."""

    filename: str = DEFAULT_FILENAME


class Custom(NamedTuple):
    """User-supplied path; the parent directory is created if missing."""

    path: Path


class InMemory(NamedTuple):
    """Bonds vanish on process exit."""


KeystoreStrategy: TypeAlias = Tempfile | Local | Custom | InMemory


def resolve(strategy: KeystoreStrategy, namespace: str) -> KeyStore:
    """Resolve a `KeystoreStrategy` to a concrete bumble `KeyStore`.

    Args:
        strategy: The strategy to resolve.
        namespace: The keystore namespace, typically the local BD_ADDR.
            Ignored by `InMemory`.

    Returns:
        A bumble `KeyStore` ready to be assigned to `Device.keystore`.
    """
    match strategy:
        case Tempfile(filename):
            return JsonKeyStore(
                namespace=namespace,
                filename=os.path.join(tempfile.gettempdir(), filename),
            )
        case Local(filename):
            from platformdirs import user_data_dir

            data_dir = Path(user_data_dir("smpclient", "intercreate"))
            data_dir.mkdir(parents=True, exist_ok=True)
            return JsonKeyStore(namespace=namespace, filename=str(data_dir / filename))
        case Custom(path):
            path.parent.mkdir(parents=True, exist_ok=True)
            return JsonKeyStore(namespace=namespace, filename=str(path))
        case InMemory():
            return MemoryKeyStore()
        case _:
            assert_never(strategy)
