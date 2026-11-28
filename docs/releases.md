# Building and releasing

Use Python 3.14 from `.python-version` and uv 0.13.10. Runtime dependencies,
development tools, and packaging tools are declared in `pyproject.toml`; commit
`uv.lock` as the shared preferred baseline. Normal uv commands reconcile the lock
with the manifest. CI and Docker may update their copies, but do not commit those
updates back. Separate builds can resolve different versions when the committed
lock is stale, so run `uv lock` locally and commit updates before releasing.

## Release inputs

Update `driver.json`, the project version in `pyproject.toml`, and `CHANGELOG.md`.
Run `uv lock` and commit the release changes. Run `bash setup.sh` once per clone
to install the local hook that checks versions when pushing a tag.

Push a tag such as `v3.0.3`, or dispatch **Build & Release** with the version
`3.0.3` without the `v` prefix. For a manual release select the ref containing the
release commit. Both paths check the checked-out commit's manifest and project
version against the requested version. Tag builds check the tagged commit, not
the current main branch. No release-please is used.

The ARM64 binary and Docker image jobs run concurrently. The release waits for
both jobs, uses the existing changelog section, and publishes a non-draft release
with the `v`-prefixed tag. Containers remain at
`ghcr.io/jackjpowell/uc-intg-psn` for `linux/amd64` and `linux/arm64`. Tag builds
publish the Git tag, the version without `v`, and `latest`; manual builds publish
the input version and `latest`.

## Binary build

On an ARM64 Ubuntu 22.04 machine:

```shell
DRIVER_ID=$(jq -r .driver_id driver.json)
uv run --no-dev --group build pyinstaller --clean --onedir \
  --name "intg-${DRIVER_ID}" \
  --collect-all zeroconf --collect-all playdirector \
  --hidden-import pyremoteplay.takion_pb2 --collect-binaries Cryptodome \
  --exclude-module pyremoteplay.gui --exclude-module pyremoteplay.gamepad \
  intg-psn/driver.py
```

The CI runner is `ubuntu-22.04-arm` (glibc 2.35), targeting devices with glibc
2.38. PyInstaller does not bundle glibc, and builds made on a newer glibc baseline
can fail on the device. Inspect the bundled interpreter, executable, and native
libraries on ARM64 and validate on the target device before accepting a release.
See [PyInstaller's compatibility guidance](https://www.pyinstaller.org/en/stable/usage.html#making-gnu-linux-apps-forward-compatible)
and [Ubuntu's glibc package](https://packages.ubuntu.com/jammy/libc6).

CI stages the onedir contents under `bin/`, renames the executable to `driver`,
and copies `driver.json` to the archive root. It also includes the integration
source, the exact installed playdirector source, and licenses. Source packaging
downloads the source URL recorded in the resolved lockfile and verifies its hash:

```shell
uv run --no-dev python tools/package_sources.py artifacts
```

The filename remains `uc-intg-<driver_id>-<version>-aarch64.tar.gz`. Extraction
produces `driver.json`, `bin/driver`, `bin/_internal/`, `sources/`, and `licenses/`
directly. Executable permissions are preserved. The installer checksum file is
`<driver_id>.hash`, with each line containing `sha256`, two spaces, then the
normal `sha256sum` output.

## Local Docker validation

```shell
docker build -t uc-intg-psn:local .
```

The production image installs runtime dependencies only, excluding the `dev` and
`build` groups. Compilers are needed for netifaces and remain outside the final
image. Dependencies are installed at image build time, never at startup. The
driver runs directly from source with its environment at `/opt/venv`. Use the
existing `/config` volume and port 9090. Local development uses `uv sync` and
`uv run` as described in the [development setup](../README.md#development).

Local validation must not publish images, push tags, or create releases. Validate
each supported Docker architecture separately; successful local x86_64 tests do
not replace ARM64 CI, glibc checks, or execution on a glibc 2.38 device.
