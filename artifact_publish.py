from __future__ import annotations

import fcntl
import pathlib
import shutil
import tempfile


def publish_artifacts(
    source: pathlib.Path,
    original_bytes: bytes,
    output_root: pathlib.Path,
    artifacts: list[tuple[pathlib.Path, bytes]],
    manifest_path: pathlib.Path,
) -> None:
    if not artifacts or artifacts[-1][0].resolve() != manifest_path.resolve():
        raise ValueError("Manifest must be the final published artifact.")
    if source.resolve() in {path.resolve() for path, _ in artifacts}:
        raise ValueError("Source and output paths collide; refusing to overwrite the source.")

    output_root.mkdir(parents=True, exist_ok=True)
    with (output_root / ".session-survivor-publish.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another candidate publication is running.") from exc
        staging = pathlib.Path(tempfile.mkdtemp(prefix=".building-", dir=output_root))
        try:
            staged: dict[pathlib.Path, pathlib.Path] = {}
            for final, data in artifacts:
                path = staging / final.resolve().relative_to(output_root.resolve())
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                staged[final] = path
            if source.read_bytes() != original_bytes:
                raise RuntimeError("Source changed during candidate generation; outputs were not published.")
            manifest_path.unlink(missing_ok=True)
            for final, _ in artifacts:
                final.parent.mkdir(parents=True, exist_ok=True)
                staged[final].replace(final)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
