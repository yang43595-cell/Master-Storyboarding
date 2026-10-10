#!/usr/bin/env sh
# Source this from the repository root: . ./backup/use_snapshot.sh
# Only this shell is changed. No original Vault, account or global setting is touched.
export MSB_VAULT_ROOT="$(pwd)/knowledge_snapshot/vault"
export PYTHONDONTWRITEBYTECODE=1
