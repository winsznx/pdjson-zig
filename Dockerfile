# Reproducible verification container.
#
#   docker build -t pdjson-zig-verify .
#   docker run --rm pdjson-zig-verify
#
# Runs the same `make verify` pipeline on a pinned toolchain, which is useful
# for two reasons: it removes "works on my laptop" from the evidence, and it
# runs on x86-64 Linux, a different target from the arm64 macOS the headline
# numbers were produced on.
#
# The upstream sources are committed, so no network access is needed for the
# core verification. Only the optional JSONTestSuite step fetches anything, and
# it is skipped when absent.

FROM debian:bookworm-slim

ARG ZIG_VERSION=0.16.0

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        git \
        python3 \
        xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Pinned Zig. The port is written against 0.16's std API (std.Io, process.Init)
# and will not compile on an older one.
RUN set -eux; \
    arch="$(uname -m)"; \
    case "$arch" in \
        x86_64)  zarch=x86_64  ;; \
        aarch64) zarch=aarch64 ;; \
        *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    url="https://ziglang.org/download/${ZIG_VERSION}/zig-${zarch}-linux-${ZIG_VERSION}.tar.xz"; \
    curl -fsSL "$url" -o /tmp/zig.tar.xz; \
    mkdir -p /opt/zig; \
    tar -xJf /tmp/zig.tar.xz -C /opt/zig --strip-components=1; \
    rm /tmp/zig.tar.xz; \
    ln -s /opt/zig/zig /usr/local/bin/zig; \
    zig version

WORKDIR /src
COPY . .

# Build during image creation so `docker run` is the verification step rather
# than a build plus a verification step.
RUN make build

CMD ["make", "verify"]
