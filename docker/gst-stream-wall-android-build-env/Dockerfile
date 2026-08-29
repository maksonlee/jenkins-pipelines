# syntax=docker/dockerfile:1

FROM cdlee/android-build-env@sha256:aa44de3756d5850330663fc920b50011da481d3e7e3b3aab2366d3360dce24ab

ARG RUST_TOOLCHAIN_VERSION=1.98.0
ARG RUST_ANDROID_TARGET=aarch64-linux-android
ARG BUNDLETOOL_VERSION=1.18.3
ARG BUNDLETOOL_SHA256=a099cfa1543f55593bc2ed16a70a7c67fe54b1747bb7301f37fdfd6d91028e29

LABEL org.opencontainers.image.title="gst-stream-wall Android build environment"
LABEL org.opencontainers.image.description="Pinned native and Rust toolchain for gst-stream-wall Android release builds"
LABEL org.opencontainers.image.source="https://github.com/maksonlee/jenkins-pipelines"

USER root

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        binutils \
        file \
        g++ \
        gcc \
        git \
        jq \
        libc6-dev \
        make \
        ninja-build \
        pkg-config \
        python3 \
    && rm -rf /var/lib/apt/lists/*

RUN install -d /opt/bundletool \
    && curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error \
        "https://github.com/google/bundletool/releases/download/${BUNDLETOOL_VERSION}/bundletool-all-${BUNDLETOOL_VERSION}.jar" \
        -o "/opt/bundletool/bundletool-all-${BUNDLETOOL_VERSION}.jar" \
    && printf '%s  %s\n' "$BUNDLETOOL_SHA256" \
        "/opt/bundletool/bundletool-all-${BUNDLETOOL_VERSION}.jar" \
        | sha256sum -c -

ENV CARGO_HOME=/opt/rust/cargo
ENV RUSTUP_HOME=/opt/rust/rustup
ENV RUSTUP_BIN_DIR=/opt/rust/cargo/bin
ENV BUNDLETOOL_JAR=/opt/bundletool/bundletool-all-1.18.3.jar
ENV PATH=/opt/rust/cargo/bin:$PATH

RUN install -d -o ubuntu -g ubuntu /opt/rust/cargo /opt/rust/rustup

USER ubuntu

RUN rustup_installer=$(mktemp) \
    && curl --proto '=https' --tlsv1.2 --fail --silent --show-error \
        https://sh.rustup.rs -o "$rustup_installer" \
    && sh "$rustup_installer" \
        -y --no-modify-path --profile minimal --default-toolchain none \
    && rm -f "$rustup_installer" \
    && rustup toolchain install "$RUST_TOOLCHAIN_VERSION" --profile minimal \
    && rustup target add "$RUST_ANDROID_TARGET" --toolchain "$RUST_TOOLCHAIN_VERSION" \
    && rustup default "$RUST_TOOLCHAIN_VERSION" \
    && rustc --version \
    && cargo --version \
    && rustup target list --installed --toolchain "$RUST_TOOLCHAIN_VERSION" \
        | grep -Fx "$RUST_ANDROID_TARGET"

WORKDIR /workspace
