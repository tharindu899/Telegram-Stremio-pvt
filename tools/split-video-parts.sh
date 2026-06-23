#!/usr/bin/env bash
# Split a video into Telegram-friendly parts and auto-name them as:
#   Movie.mkv.001, Movie.mkv.002, Movie.mkv.003 ...
# Usage:
#   bash tools/split-video-parts.sh "Movie.mkv" 1900M
# Termux:
#   pkg install coreutils
#   bash split-video-parts.sh "Movie.mkv"

set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <video-file> [part-size, default: 1900M]" >&2
  exit 1
fi

input="$1"
part_size="${2:-1900M}"

if [ ! -f "$input" ]; then
  echo "File not found: $input" >&2
  exit 1
fi

dir="$(dirname "$input")"
base="$(basename "$input")"
work_prefix="$dir/.${base}.split-tmp."

# Clean old temp files from an interrupted run only for this exact input name.
rm -f "${work_prefix}"*

# Use alphabetic temp chunks so this works on Android/Termux and Linux.
split -b "$part_size" "$input" "$work_prefix"

i=1
for part in "${work_prefix}"*; do
  [ -e "$part" ] || continue
  target="$dir/${base}.$(printf '%03d' "$i")"
  if [ -e "$target" ]; then
    echo "Target already exists: $target" >&2
    echo "Delete old parts first or move them to another folder." >&2
    exit 1
  fi
  mv "$part" "$target"
  echo "Created: $target"
  i=$((i + 1))
done

echo "Done. Upload all parts to Telegram in the same channel/group."
