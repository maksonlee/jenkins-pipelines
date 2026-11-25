FROM docker.io/library/ubuntu@sha256:d66b366dac61b575bee2aef07e9c26e8070096fbe84a3d4d3eb0da069b5d76db

# The host packages termstay's DEVELOPMENT.md lists for Ubuntu 26.04. Everything else (PowerShell, mise, Rust, Node,
# the Android SDK and Gradle's caches) is installed by the repository's own setup script under .toolchains.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        git unzip curl ca-certificates python3 build-essential perl libicu78 openjdk-21-jdk-headless \
    && rm -rf /var/lib/apt/lists/*
