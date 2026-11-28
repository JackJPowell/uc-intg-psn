# Code Style

- Code line length: 120
- Use double quotes as default (don't mix and match for simple quoting, checked with pylint).
- Configuration:
    - `.pylint.rc` for pylint
    - `setup.cfg` for flake8 and isort

## Tooling

Install all code linting tools:

```shell
uv sync
```

### Verify

CI runs Ruff lint, formatting checks, and pytest on relevant pushes and pull requests.
They can also be run anytime on a local developer machine:
```shell
uv run ruff check intg-psn
uv run ruff format intg-psn --check
uv run pytest tests
```

The existing additional development tools are also installed:

```shell
uv run pylint intg-psn
uv run flake8 intg-psn --count --show-source --statistics
uv run isort intg-psn/. --check --verbose
uv run black intg-psn --check --verbose --line-length 120
```

Linting integration in PyCharm/IntelliJ IDEA:
1. Install plugin [Pylint](https://plugins.jetbrains.com/plugin/11084-pylint)
2. Open Pylint window and run a scan: `Check Module` or `Check Current File`

### Format Code
```shell
uv run black intg-psn --line-length 120
```

PyCharm/IntelliJ IDEA integration:
1. Go to `Preferences or Settings -> Tools -> Black`
2. Configure:
- Python interpreter
- Use Black formatter: `On code reformat` & optionally `On save`
- Arguments: `--line-length 120`

### Sort Imports

```shell
uv run isort intg-psn/.
```
