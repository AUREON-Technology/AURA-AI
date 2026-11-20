from pathlib import Path
import shutil
import tarfile
from typing import Any, Dict, List, Optional
import zipfile


class ArchiveExtractor:
    """Safe extractor and manager for ZIP, TAR, and GZ archives with Zip-Slip path traversal and decompression bomb defenses."""

    @staticmethod
    def list_archive_contents(archive_path: str) -> Dict[str, Any]:
        """List files, directories, uncompressed sizes, and file count inside an archive without extraction."""
        src = Path(archive_path)
        if not src.exists() or not src.is_file():
            return {"valid": False, "error": f"Archive file not found: {archive_path}"}

        members = []
        total_uncompressed = 0

        try:
            if zipfile.is_zipfile(src):
                with zipfile.ZipFile(src, "r") as zf:
                    for info in zf.infolist():
                        total_uncompressed += info.file_size
                        members.append({
                            "name": info.filename,
                            "size_bytes": info.file_size,
                            "is_dir": info.is_dir(),
                        })
            elif tarfile.is_tarfile(src):
                with tarfile.open(src, "r:*") as tf:
                    for m in tf.getmembers():
                        total_uncompressed += m.size
                        members.append({
                            "name": m.name,
                            "size_bytes": m.size,
                            "is_dir": m.isdir(),
                        })
            else:
                return {"valid": False, "error": "Unsupported archive format (expected zip or tar)."}

            return {
                "valid": True,
                "file_path": str(src.resolve()),
                "total_members": len(members),
                "uncompressed_bytes": total_uncompressed,
                "uncompressed_mb": round(total_uncompressed / (1024 * 1024), 2),
                "members": members,
            }
        except Exception as exc:
            return {"valid": False, "error": str(exc)}

    @staticmethod
    def audit_archive_safety(archive_path: str) -> Dict[str, Any]:
        """Audit an archive for Zip Slip path traversal and decompression bomb (high ratio) attacks."""
        src = Path(archive_path)
        if not src.exists():
            return {"safe": False, "error": "File does not exist"}

        compressed_size = src.stat().st_size
        suspicious_paths = []
        total_uncompressed = 0

        try:
            if zipfile.is_zipfile(src):
                with zipfile.ZipFile(src, "r") as zf:
                    for info in zf.infolist():
                        total_uncompressed += info.file_size
                        if ".." in info.filename or info.filename.startswith(("/", "\\")):
                            suspicious_paths.append(info.filename)
            elif tarfile.is_tarfile(src):
                with tarfile.open(src, "r:*") as tf:
                    for m in tf.getmembers():
                        total_uncompressed += m.size
                        if ".." in m.name or m.name.startswith(("/", "\\")) or m.issym():
                            suspicious_paths.append(m.name)

            ratio = round(total_uncompressed / max(1, compressed_size), 2)
            is_bomb = ratio > 100.0 and total_uncompressed > 50 * 1024 * 1024
            is_safe = (len(suspicious_paths) == 0) and not is_bomb

            return {
                "safe": is_safe,
                "compression_ratio": ratio,
                "is_decompression_bomb": is_bomb,
                "path_traversal_attempts": suspicious_paths,
                "uncompressed_size_bytes": total_uncompressed,
            }
        except Exception as exc:
            return {"safe": False, "error": str(exc)}

    @staticmethod
    def extract_archive(
        archive_path: str,
        destination_dir: str,
        max_size_mb: float = 100.0,
    ) -> Dict[str, Any]:
        """Safely extract a .zip or .tar.* archive into destination_dir with Zip-Slip defense."""
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

    @staticmethod
    def extract_single_file(
        archive_path: str,
        target_filename: str,
        destination_path: str,
    ) -> Dict[str, Any]:
        """Safely extract a single member file from an archive."""
        src = Path(archive_path)
        dest = Path(destination_path)
        if not src.exists():
            return {"extracted": False, "error": f"Archive not found: {archive_path}"}

        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            if zipfile.is_zipfile(src):
                with zipfile.ZipFile(src, "r") as zf:
                    if target_filename not in zf.namelist():
                        return {"extracted": False, "error": f"File '{target_filename}' not in archive."}
                    with zf.open(target_filename) as s_file, open(dest, "wb") as d_file:
                        shutil.copyfileobj(s_file, d_file)
            elif tarfile.is_tarfile(src):
                with tarfile.open(src, "r:*") as tf:
                    member = tf.getmember(target_filename)
                    extracted_f = tf.extractfile(member)
                    if not extracted_f:
                        return {"extracted": False, "error": "Cannot read tar member file."}
                    with open(dest, "wb") as d_file:
                        shutil.copyfileobj(extracted_f, d_file)
            else:
                return {"extracted": False, "error": "Unsupported archive format."}

            return {
                "extracted": True,
                "file_path": str(dest.resolve()),
                "bytes_written": dest.stat().st_size,
            }
        except Exception as exc:
            return {"extracted": False, "error": str(exc)}

    @staticmethod
    def create_archive(
        source_dir: str,
        output_archive_path: str,
        format_type: str = "zip",
    ) -> Dict[str, Any]:
        """Create a zip or tar.gz archive from a directory."""
        src = Path(source_dir)
        out = Path(output_archive_path)
        if not src.exists() or not src.is_dir():
            return {"created": False, "error": f"Source directory not found: {source_dir}"}

        out.parent.mkdir(parents=True, exist_ok=True)
        fmt = format_type.strip().lower()
        files_added = 0

        try:
            if fmt == "zip":
                with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
                    for p in src.rglob("*"):
                        if p.is_file():
                            zf.write(p, p.relative_to(src))
                            files_added += 1
            elif fmt in ("tar.gz", "tgz", "tar"):
                mode = "w:gz" if "gz" in fmt else "w"
                with tarfile.open(out, mode) as tf:
                    for p in src.rglob("*"):
                        if p.is_file():
                            tf.add(p, arcname=str(p.relative_to(src)))
                            files_added += 1
            else:
                return {"created": False, "error": f"Unsupported format: {fmt}"}

            return {
                "created": True,
                "archive_path": str(out.resolve()),
                "files_count": files_added,
                "archive_size_bytes": out.stat().st_size,
            }
        except Exception as exc:
            return {"created": False, "error": str(exc)}
