#!/bin/sh
# The runner's one-job configuration arrives on stdin, so it is in no image layer, environment, or host argv.
set -eu
IFS= read -r config || { echo "faker-kit-ci-runner: no runner configuration on stdin" >&2; exit 2; }
exec /home/runner/actions-runner/run.sh --jitconfig "$config"
