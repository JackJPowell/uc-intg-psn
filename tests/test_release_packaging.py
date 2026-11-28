"""Release version contracts and verified corresponding-source packaging."""

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "package_sources", ROOT / "tools" / "package_sources.py"
)
packaging = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packaging)


@pytest.mark.parametrize("job", ["build", "docker"])
@pytest.mark.parametrize(
    "event,ref,manual,expected",
    [
        ("push", "refs/tags/v3.0.3", "", True),
        ("workflow_dispatch", "refs/heads/main", "3.0.3", True),
        ("push", "refs/tags/v3.0.2", "", False),
        ("workflow_dispatch", "refs/heads/main", "3.0.3\nUNSAFE=value", False),
    ],
)
def test_release_metadata_uses_checked_out_versions(
    tmp_path, job, event, ref, manual, expected
):
    (tmp_path / "driver.json").write_text(
        json.dumps({"driver_id": "psn", "version": "3.0.3"})
    )
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "3.0.3"\n')
    env_file, output_file = tmp_path / "env", tmp_path / "output"
    workflow = yaml.safe_load((ROOT / ".github/workflows/build.yml").read_text())
    script = next(
        step["run"]
        for step in workflow["jobs"][job]["steps"]
        if step.get("name") == "Get version and id"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        check=False,
        cwd=tmp_path,
        env={
            **os.environ,
            "GITHUB_EVENT_NAME": event,
            "GITHUB_REF": ref,
            "RELEASE_INPUT_VERSION": manual,
            "GITHUB_ENV": str(env_file),
            "GITHUB_OUTPUT": str(output_file),
        },
        text=True,
        capture_output=True,
    )
    assert (result.returncode == 0) == expected, result.stderr
    if expected:
        assert (
            env_file.read_text()
            == output_file.read_text()
            == ("VERSION=3.0.3\nDRIVER_ID=psn\n")
        )
    else:
        assert not env_file.exists()
        assert not output_file.exists()


@pytest.mark.parametrize("valid_hash", [True, False])
def test_download_checks_installed_version_and_source_hash(
    tmp_path, monkeypatch, valid_hash
):
    content = b"source archive"
    digest = hashlib.sha256(content).hexdigest() if valid_hash else "0" * 64
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "playdirector"\nversion = "0.4.2"\n'
        '[package.sdist]\nurl = "https://example.invalid/playdirector-0.4.2.tar.gz"\n'
        f'hash = "sha256:{digest}"\n'
    )
    monkeypatch.setattr(
        packaging, "__file__", str(tmp_path / "tools" / "package_sources.py")
    )
    monkeypatch.setattr(packaging, "version", lambda _: "0.4.2")
    monkeypatch.setattr(
        packaging, "urlopen", lambda *args, **kwargs: io.BytesIO(content)
    )
    destination = tmp_path / "artifacts"
    if valid_hash:
        assert packaging.download_library_source(destination).read_bytes() == content
    else:
        with pytest.raises(ValueError, match="locked hash"):
            packaging.download_library_source(destination)
        assert not list(destination.rglob("*.tar.gz"))


def test_source_archive_includes_build_inputs_and_excludes_configuration(tmp_path):
    source = tmp_path / "playdirector-0.4.2.tar.gz"
    with tarfile.open(source, "w:gz") as archive:
        license_file = tarfile.TarInfo("playdirector-0.4.2/LICENSE")
        content = b"test GPL license"
        license_file.size = len(content)
        archive.addfile(license_file, io.BytesIO(content))
    destination = tmp_path / "artifacts"
    packaging.package_sources(destination, source)
    assert (destination / "licenses" / "LICENSE.playdirector").read_bytes() == content
    with tarfile.open(destination / "sources" / "uc-intg-psn-source.tar.gz") as archive:
        names = set(archive.getnames())
    assert {
        "uc-intg-psn/.python-version",
        "uc-intg-psn/pyproject.toml",
        "uc-intg-psn/uv.lock",
        "uc-intg-psn/Dockerfile",
        "uc-intg-psn/.github/workflows/build.yml",
    } <= names
    assert not any(
        name.startswith(
            ("uc-intg-psn/.venv/", "uc-intg-psn/.aws/", "uc-intg-psn/config/")
        )
        for name in names
    )
