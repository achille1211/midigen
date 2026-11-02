#!/usr/bin/env bash
# Regenerate the 28 review songs (same genres/seeds as review/COMPARISON.md) into $1.
set -e
OUT=${1:?usage: gen_review_songs.sh OUTDIR}
mkdir -p "$OUT"; cd "$OUT"
for g in ambient bossa_nova cinematic drum_and_bass edm funk house jazz lofi_hiphop neo_soul pop reggae rock synthwave techno trap; do
  midigen new --genre "$g" --seed 1 --offline -o "${g}_s1" > "${g}_s1.log" 2>&1
done
for g in pop jazz house funk rock trap; do for s in 2 3; do
  midigen new --genre "$g" --seed "$s" --offline -o "${g}_s${s}" > "${g}_s${s}.log" 2>&1
done; done
