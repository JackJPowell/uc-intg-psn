# Licenses

To generate the license overview file for remote-ui, [pip-licenses](https://pypi.org/project/pip-licenses/) is used
to extract the license information in JSON format. The output JSON is then transformed in a Markdown file with a
custom script.

Install the project's runtime dependencies, then run the report tool separately
against that environment so development and build tools stay out of the report:
```shell
uv sync --no-dev
uv tool run --from pip-licenses pip-licenses --python .venv/bin/python \
  --with-description --with-urls \
  --with-license-file --no-license-path \
  --with-notice-file \
  --format=json > licenses.json
```

Transform:
```shell
cd intg-psn/tools
node transform-pip-licenses.js ../../licenses.json licenses.md
```
