#!/bin/sh
# Portable SHA-256 helper shared by provenance scripts.
# Emits the lowercase hex digest of "$1" on stdout, with no trailing filename.

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | cut -d' ' -f1
    elif command -v openssl >/dev/null 2>&1; then
        openssl dgst -sha256 "$1" | awk '{print $NF}'
    else
        echo "no sha256 tool available (need sha256sum, shasum, or openssl)" >&2
        exit 127
    fi
}
