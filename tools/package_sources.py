"""Bundle corresponding library/integration source and licenses for releases."""

import argparse
import hashlib
import shutil
import tarfile
import tomllib
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen


def download_library_source(destination: Path) -> Path:
    """Fetch the installed playdirector version's source using its locked hash."""
    root = Path(__file__).resolve().parents[1]
    with (root / "uv.lock").open("rb") as source:
        packages = tomllib.load(source)["package"]
    package = next(
        item
        for item in packages
        if item["name"] == "playdirector" and item["version"] == version("playdirector")
    )
    sdist = package["sdist"]
    target = destination / "sources" / Path(urlparse(sdist["url"]).path).name
    target.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(sdist["url"], timeout=60) as response:
        content = response.read()
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    if digest != sdist["hash"]:
        raise ValueError("playdirector source archive does not match its locked hash")
    target.write_bytes(content)
    return target


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
            ".python-version",
            "uv.lock",
            "driver.json",
            "Dockerfile",
            "docker-entry.sh",
            ".dockerignore",
            "CONTRIBUTING.md",
            "setup.sh",
            "setup.cfg",
            ".pylintrc",
        )
    ]
    files.extend((root / "intg-psn").glob("*.py"))
    files.extend((root / "tests").glob("*.py"))
    files.extend((root / "docs").glob("*.md"))
    files.extend((root / "tools").glob("*.py"))
    files.extend((root / "git-hooks").glob("*"))
    files.extend((root / "intg-psn" / "tools").rglob("*.js"))
    files.extend((root / "intg-psn" / "tools").rglob("*.md"))
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
    parser.add_argument(
        "--library-source",
        type=Path,
        help="Use a local sdist instead of downloading the installed version's locked source",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Override the archived manifest for a prerelease preview",
    )
    args = parser.parse_args()
    library_source = args.library_source or download_library_source(args.destination)
    package_sources(args.destination, library_source, args.manifest)
