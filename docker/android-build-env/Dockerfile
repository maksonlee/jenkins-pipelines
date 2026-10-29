# Base image for the Android build containers. The native Android pipelines run in it and
# flutter-android-build-env is built FROM it. JDK 25 is the default, matching the projects' Gradle daemon
# criteria and jvmToolchain(25); JDK 21 stays for older branches. Every download is pinned by checksum.
# The Android SDK is mounted from the host (android-cache/flutter-android-sdk) when a container runs; the
# command-line tools below only seed an empty SDK root. The image this replaces (built 2025-07-21, still the
# base of gst-stream-wall-android-build-env) is described in legacy-2025-07-21/.
FROM ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852

ARG JDK25_URL=https://github.com/adoptium/temurin25-binaries/releases/download/jdk-25.0.5%2B8/OpenJDK25U-jdk_x64_linux_hotspot_25.0.5_8.tar.gz
ARG JDK25_SHA256=68982529869257d0ee6b824ec5aa270ff8b118bb438670da0b97ebdab178d7fb
ARG JDK21_URL=https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.13%2B9/OpenJDK21U-jdk_x64_linux_hotspot_21.0.13_9.tar.gz
ARG JDK21_SHA256=d7f5d5cf865a2902b70ff549b97586a7d3f234e016f041ab447ea740c7d66d1c
ARG ANDROID_CMDLINE_TOOLS=16111833
ARG ANDROID_CMDLINE_TOOLS_SHA1=e025545c62a8e64c7559119566a569fb1dec5f60

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates curl git unzip xz-utils zip \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    install -d /opt/java/temurin-25 /opt/java/temurin-21; \
    curl -fsSL "$JDK25_URL" -o /tmp/jdk25.tar.gz; \
    echo "$JDK25_SHA256  /tmp/jdk25.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/jdk25.tar.gz -C /opt/java/temurin-25 --strip-components=1; \
    curl -fsSL "$JDK21_URL" -o /tmp/jdk21.tar.gz; \
    echo "$JDK21_SHA256  /tmp/jdk21.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/jdk21.tar.gz -C /opt/java/temurin-21 --strip-components=1; \
    rm -f /tmp/jdk25.tar.gz /tmp/jdk21.tar.gz

ENV JAVA_HOME=/opt/java/temurin-25
ENV ANDROID_HOME=/home/ubuntu/Android
ENV PATH=$JAVA_HOME/bin:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$PATH

RUN set -eux; \
    curl -fsSL "https://dl.google.com/android/repository/commandlinetools-linux-${ANDROID_CMDLINE_TOOLS}_latest.zip" -o /tmp/cmdline-tools.zip; \
    echo "$ANDROID_CMDLINE_TOOLS_SHA1  /tmp/cmdline-tools.zip" | sha1sum -c -; \
    install -d -o ubuntu -g ubuntu "$ANDROID_HOME" "$ANDROID_HOME/cmdline-tools" /home/ubuntu/.gradle; \
    unzip -q /tmp/cmdline-tools.zip -d "$ANDROID_HOME/cmdline-tools"; \
    mv "$ANDROID_HOME/cmdline-tools/cmdline-tools" "$ANDROID_HOME/cmdline-tools/latest"; \
    chown -R ubuntu:ubuntu "$ANDROID_HOME"; \
    rm -f /tmp/cmdline-tools.zip; \
    git config --system --add safe.directory /workspace

WORKDIR /workspace
