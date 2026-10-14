#!/usr/bin/env bash
set -euo pipefail
destination="${1:?tool directory required}"
mkdir -p "$destination"
curl --fail --location --retry 2 --max-time 120 https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz -o "$destination/gitleaks.tar.gz"
curl --fail --location --retry 2 --max-time 120 https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_amd64.tar.gz -o "$destination/actionlint.tar.gz"
echo "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb  $destination/gitleaks.tar.gz" | sha256sum --check
echo "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8  $destination/actionlint.tar.gz" | sha256sum --check
tar -xzf "$destination/gitleaks.tar.gz" -C "$destination" gitleaks
tar -xzf "$destination/actionlint.tar.gz" -C "$destination" actionlint
