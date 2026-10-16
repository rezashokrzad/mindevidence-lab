"""Development document storage: originals are copied under content-addressed names."""
import hashlib
import shutil
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def store_original(source: Path, storage_dir: Path, document_id: str, sha256: str) -> str:
    """Copy the PDF to `<storage>/<document_id>-<sha12>.pdf` (once) and return that name."""
    storage_dir.mkdir(parents=True, exist_ok=True)
    name = f"{document_id}-{sha256[:12]}.pdf"
    target = storage_dir / name
    if not target.exists() or sha256_file(target) != sha256:
        tmp = target.with_suffix(".part")
        shutil.copyfile(source, tmp)
        tmp.replace(target)
    return name
