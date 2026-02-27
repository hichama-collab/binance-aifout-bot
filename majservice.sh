#!/bin/bash

# Usage:
# ./majservice.sh SYMBOL DRY_RUN PROFILE
# Example:
# ./majservice.sh OMUSDC 0 major

if [ $# -ne 3 ]; then
  echo "Usage: $0 SYMBOL DRY_RUN PROFILE"
  exit 1
fi

SYMBOL="$1"
DRY_RUN="$2"
PROFILE="$3"

ENV_FILE="$(dirname "$0")/.service.env"

cat > "$ENV_FILE" <<EOF
SYMBOL=$SYMBOL
DRY_RUN=$DRY_RUN
PROFILE=$PROFILE
EOF

echo ".service.env updated:"
cat "$ENV_FILE"
