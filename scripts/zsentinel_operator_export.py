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
  python3 zsentinel_runner_report.py --timeline T0.json T1.json T2.json --output VERLOOP.html
  python3 zsentinel_runner_report.py --format markdown --output ACTUEEL.md
  python3 zsentinel_runner_report.py --bundle INCIDENT-PAKKET
  python3 zsentinel_runner_report.py --format issue --output GITHUB-EIGENAAR.md
  python3 zsentinel_runner_report.py --verify-bundle INCIDENT-PAKKET
  python3 zsentinel_runner_report.py --receipt T0.json --bundle INCIDENT-OUD
  python3 zsentinel_runner_report.py --format markdown --receipt T0.json --output OPGENOMEN.md
  python3 zsentinel_runner_report.py --format markdown --t0 T0.json --t1 T1.json --output VERGELIJKING.md
  python3 zsentinel_runner_report.py --format markdown --timeline T0.json T1.json T2.json --output OVERDRACHT.md
  python3 zsentinel_runner_report.py --format json --output inbox-nu.json
  python3 zsentinel_runner_report.py --format json --receipt T0.json --output inbox-opgenomen.json
  python3 zsentinel_runner_report.py --format json --t0 T0.json --t1 T1.json --output inbox-vergelijking.json
  python3 zsentinel_runner_report.py --format json --timeline T0.json T1.json T2.json --output inbox-historie.json
  # Buiten dit pakket, vanuit een checkout met scripts/zsentinel_operator_export.py:\n  python3 scripts/zsentinel_operator_export.py --verify-dir PAKKET-B --compare-dir PAKKET-A --compare-format markdown\n  python3 scripts/zsentinel_operator_export.py --verify-dir PAKKET-B --compare-dir PAKKET-A --compare-format markdown --compare-output NIEUWE-PRIVATE-MAP/OVERDRACHT.md

Rapporten en de nieuwe gestructureerde JSON-eigenaarinbox worden create-only (0600)\ngemaakt in bestaande veilige doelmappen. JSON noemt bronmoment,\nbeperkte capaciteit/servicetellingen en maximaal drie handmatige acties.\nDe JSON-inbox activeert GEEN herstel of workflow. Ook Markdown kan direct\nvanuit de eenmalige live bron of twee historische bonnetjes worden gemaakt;\nook één opgeslagen privébon kan offline opnieuw als rapport worden getoond.\nHistorische bronmomenten blijven uitdrukkelijk niet actueel.
Bij --previous is alleen de nieuwe meting actueel gelezen: de T0-bron blijft
expliciet historisch, ook in JSON, Markdown en het GitHub-issueconcept.\n
Het nieuwe --bundle maakt een verse 0700 map met incident.html,\nowner-handoff.md, owner-inbox.json, github-owner-issue-draft.md en\nMANIFEST.json; ieder bestand is 0600.
De drie rapporten delen dezelfde bronobservatie.
Het incident.html start met lokale navigatie naar de eigenaaroverdracht,
het geredigeerde GitHub-issueconcept en de JSON-inbox; geen webformulier,
upload of externe verbinding. HTML en Markdown tonen dezelfde incidentreferentie.
Bij ontbrekende/ongeldige CPU-capaciteitscijfers blijft de P0-actiestatus
ONBEKEND/BEHEERDER NODIG; de rapportage gaat niet stuk door typefouten. --verify-bundle leest alleen,
controleert bronvenster en SHA256-consistentie, GEEN cryptografische handtekening.\nDe GitHub-issueconcepttekst heeft bewuste handmatige overdracht,\nbevoegdheidsvragen en geen privé-runnernamen, host of ruwe capaciteit.
Plaats de bundel onder een bestaande eigenaar-private (0700) doelmap.

Gebruik nooit bestaande rapport-/bronbestandsnamen als nieuw doel. Het huidige --preview toont de werkelijke effectieve CPU-toewijzing
en of een verhoging nog op ten minste 5 minuten/3 observaties wacht.
De private HTML/JSON-receipts bewaren deze beslisstatus uitsluitend
als momentopname; geen CPU-wijziging in preview. Oude historische
statuswijzigingen zijn geen bewijs van GitHub CI, werkclaim of productieherstel.
De modules kunnen bij onjuist gebruik ook live CPU-properties aanpassen: voer
de background_priority-module alleen met --preview uit totdat alle vereiste
code-review, exact-head CI, branch protection en runtime release-autorisatie
formeel zijn afgerond. Een vergelijking kan optioneel uitsluitend in een NIEUW .txt/.md/.json bestand\nonder een bestaande eigen 0700-map worden opgeslagen (--compare-output).\nHet bestand wordt create-only met 0600 gemaakt; bestaande uitvoer wordt nooit vervangen.\nDe operatorbundel is geen production deploy approval.
"""


def _stable_file_identity(meta):
    return (meta.st_dev, meta.st_ino, meta.st_mode, meta.st_size,
            meta.st_mtime_ns, meta.st_ctime_ns)


def _read_source(source_dir_fd, name):
    """Read through one pinned source directory, reject changed/replaced files."""
    if name not in SOURCES:
        raise ValueError("unexpected source module")
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW |
                 getattr(os, "O_CLOEXEC", 0), dir_fd=source_dir_fd)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or
                before.st_size <= 0 or before.st_size > MAX_SOURCE_BYTES):
            raise ValueError("bounded regular Python source required")
        with os.fdopen(fd, "rb", closefd=False) as src:
            data = src.read(MAX_SOURCE_BYTES + 1)
        after = os.fstat(fd)
        named = os.stat(name, dir_fd=source_dir_fd, follow_symlinks=False)
        if (_stable_file_identity(before) != _stable_file_identity(after)
                or _stable_file_identity(after) != _stable_file_identity(named)
                or not data or len(data) != before.st_size):
            raise ValueError("source module changed while creating operator snapshot")
        data.decode("utf-8")
        return data
    finally:
        os.close(fd)


def read_local_source(source_dir):
    """A bounded checkout observation, NOT a Git/CI/installed-source receipt."""
    if not isinstance(source_dir, Path):
        raise ValueError("expected source directory")
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("safe nofollow source access is required")
    directory = os.open(source_dir, os.O_RDONLY | os.O_DIRECTORY |
                        os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    try:
        before = os.fstat(directory)
        if (not stat.S_ISDIR(before.st_mode)
                or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) & 0o022):
            raise ValueError("source folder must be owner-controlled")
        initial = {name: _read_source(directory, name) for name in SOURCES}
        # Re-read the entire set before constructing an owner handoff. This
        # is bounded verification of a stable local observation, NOT an
        # atomic Git checkout snapshot or signed repository provenance.
        again = {name: _read_source(directory, name) for name in SOURCES}
        named = os.stat(source_dir, follow_symlinks=False)
        if initial != again or _stable_file_identity(before) != _stable_file_identity(named):
            raise ValueError("source directory or modules changed during observation")
        return initial
    finally:
        os.close(directory)

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
    files = read_local_source(source_dir)
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
        "readme_sha256": hashlib.sha256(README.encode("utf-8")).hexdigest(),
    }
    files["MANIFEST.json"] = (json.dumps(
        manifest, sort_keys=True, ensure_ascii=False, indent=2
    ) + "\n").encode("utf-8")
    files["LEESMIJ.txt"] = README.encode("utf-8")
    parent = output_dir.parent
    if not output_dir.name or output_dir.name in {".", ".."} or parent.is_symlink():
        raise ValueError("choose a fresh operator bundle name and private parent")
    if not parent.is_dir():
        raise ValueError("operator bundle parent must already exist")
    # Open the parent once, then create/open/remove entries relative to its fd.
    # A shared or replaceable parent could let another user rename our 0700
    # bundle after creation, so require the owner and private parent mode.
    parent_fd = os.open(
        parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    directory = None
    created = False
    try:
        info = os.fstat(parent_fd)
        if (not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077):
            raise ValueError("operator bundle parent must be private and owned")
        os.mkdir(output_dir.name, mode=0o700, dir_fd=parent_fd)
        created = True
        directory = os.open(
            output_dir.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        os.fchmod(directory, 0o700)
        for name, body in files.items():
            _write_new(directory, name, body)
        os.fsync(directory)
        os.fsync(parent_fd)
    except BaseException:
        if directory is not None:
            for name in files:
                try:
                    os.unlink(name, dir_fd=directory)
                except FileNotFoundError:
                    continue
        if created:
            os.rmdir(output_dir.name, dir_fd=parent_fd)
        raise
    finally:
        if directory is not None:
            os.close(directory)
        os.close(parent_fd)
    return manifest


def verify(bundle_dir):
    """Read-only provenance check; never executes or installs packaged modules."""
    if (not isinstance(bundle_dir, Path) or not bundle_dir.is_dir()
            or bundle_dir.is_symlink()):
        raise ValueError("private bundle directory required")
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("safe nofollow access required")
    dirfd = os.open(bundle_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        directory = os.fstat(dirfd)
        if (not stat.S_ISDIR(directory.st_mode)
                or directory.st_uid != os.geteuid()
                or stat.S_IMODE(directory.st_mode) & 0o077):
            raise ValueError("bundle directory not owner-private")
        required = set(SOURCES) | {"MANIFEST.json", "LEESMIJ.txt"}
        if set(os.listdir(dirfd)) != required:
            raise ValueError("unknown or missing bundle entries")
        content = {}
        for name in required:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dirfd)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode)
                        or info.st_uid != os.geteuid()
                        or stat.S_IMODE(info.st_mode) & 0o077
                        or not 0 < info.st_size <= MAX_SOURCE_BYTES):
                    raise ValueError("not a private regular bundle file")
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    content[name] = stream.read(MAX_SOURCE_BYTES + 1)
                if len(content[name]) > MAX_SOURCE_BYTES:
                    raise ValueError("bundle file too large")
            finally:
                os.close(fd)
    finally:
        os.close(dirfd)
    manifest = json.loads(content["MANIFEST.json"].decode("utf-8"))
    if (type(manifest) is not dict or manifest.get("schema") != MANIFEST_SCHEMA
            or manifest.get("preview_only_required_before_authorized_release") is not True
            or manifest.get("systemd_installed") is not False
            or manifest.get("workflow_dispatched") is not False
            or manifest.get("production_released") is not False
            or manifest.get("external_source_verified") is not False
            or manifest.get("readme_sha256") != hashlib.sha256(
                content["LEESMIJ.txt"]).hexdigest()
            or type(manifest.get("files")) is not dict
            or set(manifest["files"]) != set(SOURCES)):
        raise ValueError("unexpected bundle manifest")
    for name in SOURCES:
        entry = manifest["files"][name]
        if (type(entry) is not dict or type(entry.get("bytes")) is not int
                or entry["bytes"] != len(content[name])
                or entry.get("sha256") != hashlib.sha256(content[name]).hexdigest()):
            raise ValueError("operator source digest differs from manifest")
    return {
        "verified_modules": len(SOURCES),
        "release_authorized": False,
        # The hashes below are derived from the actually opened bounded bytes,
        # not from untrusted manifest labels or guessed release timestamps.
        "digests": {name: hashlib.sha256(content[name]).hexdigest() for name in SOURCES},
        "source_bytes": {name: len(content[name]) for name in SOURCES},
        "readme_sha256": hashlib.sha256(content["LEESMIJ.txt"]).hexdigest(),
        "readme_bytes": len(content["LEESMIJ.txt"]),
    }


def compare_packages(first_dir, second_dir):
    """Compare two separately verified private source bundles, never execute code.

    A and B are caller-selected snapshots: neither path nor local file metadata
    establishes which release is newer or whether either is authorized/live.
    """
    first, second = verify(first_dir), verify(second_dir)
    entries = []
    for name in SOURCES:
        a, b = first["digests"][name], second["digests"][name]
        entries.append({
            "module": name,
            "changed": a != b,
            "a_sha256": a,
            "b_sha256": b,
            "a_bytes": first["source_bytes"][name],
            "b_bytes": second["source_bytes"][name],
        })
    readme_changed = first["readme_sha256"] != second["readme_sha256"]
    return {
        "schema": "zsentinel.private-operator-source-comparison.v1",
        "scope": "two-locally-verified-private-manifests",
        "source_order_proven": False,
        "external_source_verified": False,
        "release_authorized": False,
        "installed_or_live_verified": False,
        "changes": entries,
        "changed_modules": sum(row["changed"] for row in entries),
        "operator_instructions": {
            "filename": "LEESMIJ.txt",
            "changed": readme_changed,
            "a_sha256": first["readme_sha256"],
            "b_sha256": second["readme_sha256"],
            "a_bytes": first["readme_bytes"],
            "b_bytes": second["readme_bytes"],
        },
    }


def render_comparison(data, format_name):
    """Emit only fixed safe module basenames and checksums; never private paths."""
    if format_name == "json":
        return json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2)
    lines = [
        "zSentinel · offline vergelijking van twee privé bronpakketten",
        "Pakket A = --compare-dir, pakket B = --verify-dir.",
        "A/B-volgorde zegt NIETS over releasevolgorde, CI, eigenaar of installatie.",
        "Beide pakketten intern op bytes/SHA-256 gecontroleerd; niet extern vertrouwd.",
        "Geen broncode uitgevoerd en geen dienst, claim, CI, merge of deploy gewijzigd.",
        "",
    ]
    if format_name == "markdown":
        lines = ["# " + lines[0], "", *lines[1:]]
        lines += ["| Module | Vergelijking | A bytes | B bytes | A SHA-256 | B SHA-256 |",
                  "| --- | --- | ---: | ---: | --- | --- |"]
        for row in data["changes"]:
            lines.append("| `" + row["module"] + "` | " +
                         ("GEWIJZIGD" if row["changed"] else "identiek") +
                         " | " + str(row["a_bytes"]) +
                         " | " + str(row["b_bytes"]) +
                         " | `" + row["a_sha256"] + "` | `" +
                         row["b_sha256"] + "` |")
    else:
        for row in data["changes"]:
            lines.append(("[GEWIJZIGD] " if row["changed"] else "[identiek]  ") +
                         row["module"] + " · " + str(row["a_bytes"]) + " → " +
                         str(row["b_bytes"]) + " bytes · SHA-256 " +
                         row["a_sha256"][:16] + " → " + row["b_sha256"][:16])
    guide = data["operator_instructions"]
    guide_state = "GEWIJZIGD" if guide["changed"] else "identiek"
    if format_name == "markdown":
        lines.append("| `LEESMIJ.txt` | " + guide_state + " | " +
                     str(guide["a_bytes"]) + " | " + str(guide["b_bytes"]) +
                     " | `" + guide["a_sha256"] + "` | `" +
                     guide["b_sha256"] + "` |")
    else:
        lines.append((" [GEWIJZIGD] " if guide["changed"] else " [identiek]  ") +
                     "LEESMIJ.txt · " + str(guide["a_bytes"]) + " → " +
                     str(guide["b_bytes"]) + " bytes · SHA-256 " +
                     guide["a_sha256"][:16] + " → " + guide["b_sha256"][:16])
    lines += ["", "Gewijzigde modules: " + str(data["changed_modules"]) +
              "/" + str(len(data["changes"])),
              "Operatorinstructies: " + guide_state + " (geen live-toepassingsbewijs).",
              "Vergelijk daarna zelf exacte GitHub HEAD, verplichte checks, review en runtime-eigenaar.",
              "Geen release- of herstelautorisatie."]
    return "\n".join(lines)


def save_private_comparison(output, rendered, format_name):
    """Create a single new 0600 owner-private operator handoff, no overwrite."""
    expected_suffix = {"text": ".txt", "markdown": ".md", "json": ".json"}[format_name]
    if (not isinstance(output, Path) or not output.name
            or output.name in {".", ".."} or output.suffix != expected_suffix):
        raise ValueError("private comparison extension required")
    body = (rendered + "\n").encode("utf-8")
    if not 0 < len(body) <= 64 * 1024:
        raise ValueError("private comparison too large")
    parent = output.parent
    if not parent.is_dir() or parent.is_symlink():
        raise ValueError("private existing output parent required")
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY |
                        os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    try:
        meta = os.fstat(parent_fd)
        if (not stat.S_ISDIR(meta.st_mode)
                or meta.st_uid != os.geteuid()
                or stat.S_IMODE(meta.st_mode) & 0o077):
            raise ValueError("private output parent must be owner-only")
        _write_new(parent_fd, output.name, body)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def main():
    parser = argparse.ArgumentParser(
        description="Private, non-deploying zSentinel operator source bundle"
    )
    parser.add_argument("--source-dir", type=Path, default=Path(__file__).parent,
                        help="Directory with the three reviewed source modules")
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--output-dir", type=Path,
                        help="NEW 0700 bundle folder; no production operations")
    choice.add_argument("--verify-dir", type=Path,
                        help="READ ONLY check of an existing owner-private bundle")
    parser.add_argument("--compare-dir", type=Path,
                        help="Another EXISTING private bundle A, only with --verify-dir B")
    parser.add_argument("--compare-format", choices=("text", "markdown", "json"),
                        default="text", help="Offline operator comparison output")
    parser.add_argument("--compare-output", type=Path,
                        help="Optional NEW owner-private 0600 .txt/.md/.json comparison file")
    args = parser.parse_args()
    if args.compare_dir is not None and args.verify_dir is None:
        parser.error("--compare-dir requires --verify-dir for the second package")
    if args.compare_output is not None and args.compare_dir is None:
        parser.error("--compare-output requires two existing private packages")
    try:
        if args.compare_dir is not None:
            info = compare_packages(args.compare_dir, args.verify_dir)
        elif args.verify_dir is not None:
            info = verify(args.verify_dir)
        else:
            info = export(args.source_dir, args.output_dir)
    except (OSError, ValueError, UnicodeError, TypeError, KeyError):
        parser.exit(2, "Privé operatorpakket niet bruikbaar; controleer bron en map.\n")
    if args.compare_dir is not None:
        comparison = render_comparison(info, args.compare_format)
        if args.compare_output is not None:
            try:
                save_private_comparison(args.compare_output, comparison, args.compare_format)
            except (OSError, ValueError):
                parser.exit(2, "Privé vergelijking niet opgeslagen; geen overschrijving.\n")
            print("Nieuwe privé vergelijking aangemaakt (0600); geen bron-, workflow-, release- of VPS-wijziging.")
        else:
            print(comparison)
    elif args.verify_dir is not None:
        print("Privé bronpakket herkend: " + str(info["verified_modules"])
              + " modules stemmen overeen met manifest. Geen releaseautorisatie.")
    else:
        print("Privé bronpakket gemaakt: " + str(len(info["files"]))
              + " modules met bronvingerafdruk; geen productie-ingreep.")


if __name__ == "__main__":
    main()
