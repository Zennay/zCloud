#!/usr/bin/env python3
"""Prepare a self-contained private zSentinel operator source bundle.

This local copy does not install, enable, run or update VPS services. It
packages only the three Python modules needed for bounded private previews,
runner HTML reports and T0/T1 receipts, plus an integrity manifest.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

SOURCES = (
    "zsentinel_background_priority.py",
    "zsentinel_runner_report.py",
    "zsentinel_runner_snapshot.py",
)
MAX_SOURCE_BYTES = 128 * 1024
MANIFEST_SCHEMA = "zsentinel.private-operator-bundle.v1"

README = """zSentinel | privaat operatorpakket (broncode, NIET geïnstalleerd)

Deze map is een lokaal hulpmiddel voor bevoegde operators. Dit commando heeft
GEEN systemd-timer, runner, GitHub workflow, wachtrij, model of productiecode
gestart, gestopt, gedeployed of gewijzigd.

Vanaf deze map:
  python3 zsentinel_background_priority.py --preview --human --runners
  python3 zsentinel_background_priority.py --preview --human --report NIEUW.html
  python3 zsentinel_background_priority.py --preview --snapshot T0.json
  python3 zsentinel_background_priority.py --preview --snapshot T1.json --previous T0.json
  python3 zsentinel_runner_report.py --t0 T0.json --t1 T1.json --output NIEUW.html

Rapporten worden create-only (0600) gemaakt in bestaande veilige doelmappen.
Gebruik nooit bestaande rapport-/bronbestandsnamen als nieuw doel. Historische
statuswijzigingen zijn geen bewijs van GitHub CI, werkclaim of productieherstel.
De modules kunnen bij onjuist gebruik ook live CPU-properties aanpassen: voer
de background_priority-module alleen met --preview uit totdat alle vereiste
code-review, exact-head CI, branch protection en runtime release-autorisatie
formeel zijn afgerond. De operatorbundel is geen production deploy approval.
"""


def _read_source(source_dir, name):
    path = source_dir / name
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("safe nofollow source access is required")
    fd = os.open(path, flags)
    try:
        meta = os.fstat(fd)
        if (not stat.S_ISREG(meta.st_mode) or
                meta.st_size <= 0 or meta.st_size > MAX_SOURCE_BYTES):
            raise ValueError("bounded regular Python source required")
        with os.fdopen(fd, "rb", closefd=False) as src:
            data = src.read(MAX_SOURCE_BYTES + 1)
        if not data or len(data) > MAX_SOURCE_BYTES:
            raise ValueError("source size not allowed")
        # Source is UTF-8 Python, not arbitrary executable bytes.
        data.decode("utf-8")
        return data
    finally:
        os.close(fd)


def _write_new(dir_fd, name, content):
    if "/" in name or "\\" in name or name in ("", ".", ".."):
        raise ValueError("unsafe operator package entry")
    fd = os.open(
        name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0), 0o600, dir_fd=dir_fd,
    )
    try:
        with os.fdopen(fd, "wb") as dest:
            os.fchmod(dest.fileno(), 0o600)
            dest.write(content)
            dest.flush()
            os.fsync(dest.fileno())
    except BaseException:
        os.unlink(name, dir_fd=dir_fd)
        raise


def export(source_dir, output_dir):
    """Materialize a new private folder containing one coherent source set."""
    if not isinstance(source_dir, Path) or not isinstance(output_dir, Path):
        raise ValueError("expected source and target directory")
    if not source_dir.is_dir() or source_dir.is_symlink():
        raise ValueError("source must be an existing non-symlink folder")
    files = {name: _read_source(source_dir, name) for name in SOURCES}
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "files": {name: {
            "sha256": hashlib.sha256(files[name]).hexdigest(),
            "bytes": len(files[name]),
        } for name in SOURCES},
        "preview_only_required_before_authorized_release": True,
        "systemd_installed": False,
        "workflow_dispatched": False,
        "production_released": False,
        "external_source_verified": False,
    }
    files["MANIFEST.json"] = (json.dumps(
        manifest, sort_keys=True, ensure_ascii=False, indent=2
    ) + "\n").encode("utf-8")
    files["LEESMIJ.txt"] = README.encode("utf-8")
    parent = output_dir.parent
    if not output_dir.name or output_dir.name in {".", ".."} or parent.is_symlink():
        raise ValueError("choose a fresh private bundle name and safe parent")
    if not parent.is_dir():
        raise ValueError("the output parent must already exist")
    # Exclusive directory creation prevents replacement of existing deployments.
    os.mkdir(output_dir, mode=0o700)
    directory = None
    try:
        directory = os.open(
            output_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
        )
        os.fchmod(directory, 0o700)
        for name, body in files.items():
            _write_new(directory, name, body)
        os.fsync(directory)
    except BaseException:
        if directory is not None:
            for name in files:
                try:
                    os.unlink(name, dir_fd=directory)
                except FileNotFoundError:
                    continue
        os.rmdir(output_dir)
        raise
    finally:
        if directory is not None:
            os.close(directory)
    return manifest


def main():
    parser = argparse.ArgumentParser(
        description="Create private zSentinel operator source bundle without deployment"
    )
    parser.add_argument("--source-dir", type=Path, default=Path(__file__).parent,
                        help="Source directory with the three reviewed Python modules")
    parser.add_argument("--output-dir", required=True, type=Path,
                        help="NEW private directory; no overwrite, no service operations")
    args = parser.parse_args()
    try:
        receipt = export(args.source_dir, args.output_dir)
    except (OSError, ValueError, UnicodeError):
        parser.exit(2, "Privé operatorpakket niet gemaakt; controleer nieuwe map en bron.\n")
    print("Privé bronpakket gemaakt: "
          + str(len(receipt["files"])) + " modules; manifest SHA-256 beschikbaar; "
          + "geen install, dispatch of productiewijziging.")


if __name__ == "__main__":
    main()
