# The veil-backend CI toolchain plus the aarch64 cross-compiler and Rust target the origin needs.
FROM docker.io/library/rust:1.98.0-bookworm@sha256:82150a52ec202c1b14d7817e14516c392bb7f5cfebd88f1ed531cb37ebd39922

RUN rustup target add aarch64-unknown-linux-gnu \
    && apt-get update \
    && apt-get install -y --no-install-recommends gcc-aarch64-linux-gnu libc6-dev-arm64-cross binutils-aarch64-linux-gnu \
    && rm -rf /var/lib/apt/lists/*
RUN rustc --version | grep -F 'rustc 1.98.0' && aarch64-linux-gnu-gcc --version | head -1
