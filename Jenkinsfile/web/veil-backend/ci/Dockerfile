FROM docker.io/library/rust:1.98.0-bookworm@sha256:82150a52ec202c1b14d7817e14516c392bb7f5cfebd88f1ed531cb37ebd39922

# Clippy for the lint gate, and the shared libraries the pinned MariaDB 11.8.9 tarball needs to start.
RUN rustup component add clippy \
    && apt-get update \
    && apt-get install -y --no-install-recommends libaio1 libncurses6 libnuma1 procps \
    && rm -rf /var/lib/apt/lists/*
RUN rustc --version | grep -F 'rustc 1.98.0' && cargo clippy --version
