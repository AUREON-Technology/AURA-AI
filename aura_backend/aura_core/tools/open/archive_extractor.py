import tarfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List


class ArchiveExtractor:
    """Safe extractor for ZIP and TAR archives with Zip-Slip path traversal and size checks."""

    @staticmethod
    def extract_archive(
        archive_path: str,
        destination_dir: str,
        max_size_mb: float = 100.0,
    ) -> Dict[str, Any]:
        """Safely extract a .zip or .tar.* archive into destination_dir."""
        src = Path(archive_path)
        dest = Path(destination_dir)
        if not src.exists() or not src.is_file():
            return {"extracted": False, "error": f"Archive file not found: {archive_path}"}

        dest.mkdir(parents=True, exist_ok=True)
        resolved_dest = dest.resolve()
        max_bytes = int(max(0.1, max_size_mb) * 1024 * 1024)
        total_bytes = 0
        extracted_members: List[str] = []

        try:
            if zipfile.is_zipfile(src):
                with zipfile.ZipFile(src, "r") as zf:
                    for info in zf.infolist():
                        target = (resolved_dest / info.filename).resolve()
                        if target != resolved_dest and resolved_dest not in target.parents:
                            return {
                                "extracted": False,
                                "error": f"Path traversal blocked for entry: {info.filename}",
                            }
                        total_bytes += info.file_size
                        if total_bytes > max_bytes:
                            return {
                                "extracted": False,
                                "error": f"Archive exceeds max size limit ({max_size_mb} MB).",
                            }
                        zf.extract(info, resolved_dest)
                        extracted_members.append(info.filename)

            elif tarfile.is_tarfile(src):
                with tarfile.open(src, "r:*") as tf:
                    for member in tf.getmembers():
                        target = (resolved_dest / member.name).resolve()
                        if target != resolved_dest and resolved_dest not in target.parents:
                            return {
                                "extracted": False,
                                "error": f"Path traversal blocked for member: {member.name}",
                            }
                        total_bytes += member.size
                        if total_bytes > max_bytes:
                            return {
                                "extracted": False,
                                "error": f"Archive exceeds max size limit ({max_size_mb} MB).",
                            }
                        tf.extract(member, resolved_dest)
                        extracted_members.append(member.name)
            else:
                return {"extracted": False, "error": "Unsupported archive format (expected zip or tar)."}

            return {
                "extracted": True,
                "destination": str(resolved_dest),
                "extracted_count": len(extracted_members),
                "total_bytes": total_bytes,
                "members": extracted_members,
            }
        except Exception as exc:
            return {"extracted": False, "error": f"{type(exc).__name__}: {exc}"}