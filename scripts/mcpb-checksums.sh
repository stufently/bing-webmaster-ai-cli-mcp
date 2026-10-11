#!/usr/bin/env bash
# Add SHA256 lines for every immediate *.mcpb in DIR to a sha256sum file.
#
# Usage: mcpb-checksums.sh DIR SUMS
#
# SUMS may already hold lines for other release artifacts. Those lines stay,
# byte for byte and in their original order, including a final line with no
# trailing newline (a separator newline is added before the bundle lines).
# Lines whose filename ends in .mcpb are replaced, including GNU sha256sum
# escapes that start with a backslash, so a second run does not duplicate
# them. A directory with no bundles is an error and leaves SUMS untouched.
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: mcpb-checksums.sh DIR SUMS" >&2
  exit 2
fi

dir=$1
sums=$2

if [ ! -d "$dir" ]; then
  echo "mcpb-checksums: not a directory: ${dir}" >&2
  exit 1
fi

bundles=()
for path in "$dir"/*.mcpb; do
  # No match leaves the glob literal; a directory named *.mcpb is not a bundle.
  if [ -f "$path" ]; then
    base=$(basename -- "$path")
    bundles+=("$base")
  fi
done

if [ "${#bundles[@]}" -eq 0 ]; then
  echo "mcpb-checksums: no .mcpb files in ${dir}" >&2
  exit 1
fi

mapfile -t names < <(printf '%s\n' "${bundles[@]}" | LC_ALL=C sort)

sums_dir=$(dirname -- "$sums")
tmp=$(mktemp "${sums_dir}/.mcpb-checksums.XXXXXX")
cleanup() {
  if [ -n "${tmp:-}" ] && [ -f "$tmp" ]; then
    rm -f -- "$tmp"
  fi
}
trap cleanup EXIT

# Keep every existing line that is not a .mcpb sum. read keeps the bytes of
# the line (including a trailing CR); the newline we write back is the one
# read just stripped. A missing final newline is restored so the bundle
# lines that follow cannot stick to that line.
if [ -f "$sums" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    filename=""
    # sha256sum text: "<64 hex><two spaces><name>". GNU escapes a name that
    # contains a backslash or newline as "\<64 hex><two spaces><escaped>".
    # Anything else is kept whole, so an unknown line cannot be dropped.
    if [[ "$line" =~ ^[[:xdigit:]]{64}\ \ (.*)$ ]]; then
      filename=${BASH_REMATCH[1]}
    elif [[ "$line" =~ ^\\[[:xdigit:]]{64}\ \ (.*)$ ]]; then
      filename=${BASH_REMATCH[1]}
    fi
    if [ -n "$filename" ]; then
      filename=${filename%$'\r'}
    fi
    if [[ "$filename" == *.mcpb ]]; then
      continue
    fi
    printf '%s\n' "$line" >> "$tmp"
  done < "$sums"
fi

(
  cd -- "$dir" || exit 1
  sha256sum -- "${names[@]}" | LC_ALL=C sort -k2
) >> "$tmp"

if [ -f "$sums" ]; then
  chmod --reference="$sums" -- "$tmp"
else
  chmod 644 -- "$tmp"
fi

# Check the lines we just wrote before publishing them over SUMS. A mismatch
# must not replace a checksum file the release already published.
check_out=$(
  grep '\.mcpb$' -- "$tmp" | (
    cd -- "$dir" || exit 1
    sha256sum -c --strict -
  )
) || {
  printf '%s\n' "$check_out" >&2
  echo "mcpb-checksums: sha256sum -c failed for .mcpb lines" >&2
  exit 1
}

mv -f -- "$tmp" "$sums"
tmp=""
