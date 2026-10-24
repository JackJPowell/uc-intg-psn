"""Bundle corresponding library/integration source and licenses for releases."""

import argparse
import shutil
import tarfile
from pathlib import Path


def package_sources(
    destination: Path, library_source: Path, manifest: Path | None = None
) -> None:
    """Copy the exact library sdist and archive explicit, non-secret sources."""
    root = Path(__file__).resolve().parents[1]
    sources, licenses = destination / "sources", destination / "licenses"
    sources.mkdir(parents=True, exist_ok=True)
    licenses.mkdir(parents=True, exist_ok=True)
    target = sources / library_source.name
    if library_source.resolve() != target.resolve():
        shutil.copyfile(library_source, target)
    with tarfile.open(library_source, "r:gz") as archive:
        member = next(
            row
            for row in archive.getmembers()
            if row.name.endswith("/LICENSE") and row.isfile()
        )
        with archive.extractfile(member) as source:
            (licenses / "LICENSE.playdirector").write_bytes(source.read())
    shutil.copyfile(root / "LICENSE", licenses / "LICENSE.integration")
    shutil.copyfile(
        root / "THIRD_PARTY_NOTICES.md", licenses / "THIRD_PARTY_NOTICES.md"
    )
    files = [
        root / name
        for name in (
            "LICENSE",
            "THIRD_PARTY_NOTICES.md",
            "README.md",
            "pyproject.toml",
            "requirements.txt",
            "uv.lock",
            "driver.json",
            "Dockerfile",
            ".dockerignore",
            "test-requirements.txt",
        )
    ]
    files.extend((root / "intg-psn").glob("*.py"))
    files.extend((root / "tests").glob("*.py"))
    files.extend((root / "docs").glob("*.md"))
    files.extend((root / "tools").glob("*.py"))
    files.extend((root / ".github" / "workflows").glob("*.yml"))
    with tarfile.open(sources / "uc-intg-psn-source.tar.gz", "w:gz") as archive:
        for path in files:
            source = (
                manifest
                if manifest is not None and path.name == "driver.json"
                else path
            )
            archive.add(
                source, arcname=str(Path("uc-intg-psn") / path.relative_to(root))
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--library-source", type=Path, required=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Override the archived manifest for a prerelease preview",
    )
    args = parser.parse_args()
    package_sources(args.destination, args.library_source, args.manifest)
