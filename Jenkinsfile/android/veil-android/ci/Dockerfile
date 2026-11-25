FROM harbor.maksonlee.com/library/android-build-env@sha256:b88ae3849ada50e9943fb7cab8b1073063e1be23189c132395f9a853bb6154d7

# The Android image plus the Rust toolchain veil-android's Gradle build requires exactly: 1.98.0 with the four
# Android targets, and a host C toolchain for build scripts and proc macros. The NDK comes from the mounted SDK.
ENV RUSTUP_HOME=/opt/rust/rustup
ENV PATH=/opt/rust/cargo/bin:$PATH
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && curl --proto '=https' --tlsv1.2 --fail --silent --show-error https://sh.rustup.rs -o /tmp/rustup-init.sh \
    && CARGO_HOME=/opt/rust/cargo sh /tmp/rustup-init.sh -y --no-modify-path --profile minimal \
        --default-toolchain 1.98.0 \
        --target aarch64-linux-android --target armv7-linux-androideabi \
        --target i686-linux-android --target x86_64-linux-android \
    && rm /tmp/rustup-init.sh \
    && chmod -R a+rX /opt/rust
RUN rustc --version | grep -F 'rustc 1.98.0' \
    && test -f "$(rustc --print sysroot)/share/doc/rust/COPYRIGHT-library.html" \
    && test -f "$(rustc --print sysroot)/share/doc/rust/licenses/MIT.txt"
