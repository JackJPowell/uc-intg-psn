#!/bin/bash

set -euo pipefail
cd /app
exec python -u intg-psn/driver.py "$@"
