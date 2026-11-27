FROM rust:1.100.0-bookworm@sha256:75d26c23c24cc6ea1cdabf0714a6bff8fd7bf3248716043fd7ae86dac7f41b94 AS rust
FROM node:24.22.3-bookworm@sha256:49f7adaf9f1574bc45c20ea317139b39a59d7e758d0e93745e112fd85cb42623

COPY --from=rust /usr/local/rustup /usr/local/rustup
COPY --from=rust /usr/local/cargo /usr/local/cargo
ENV RUSTUP_HOME=/usr/local/rustup \
    RUSTUP_TOOLCHAIN=1.100.0 \
    PATH=/usr/local/cargo/bin:$PATH

RUN rustup component add clippy rustfmt && rustup target add aarch64-unknown-linux-musl \
    && npm install --global pnpm@10.34.9 \
    && npm exec --yes --package @playwright/test@1.65.1 -- playwright install-deps chromium \
    && apt-get update \
    && apt-get -y --no-install-recommends install binutils cmake python3 qemu-user \
    && rm -rf /var/lib/apt/lists/* /root/.npm
RUN set -eux; \
    case "$(dpkg --print-architecture)" in \
        amd64) arch=x86_64; sha256=02aa270f183da276e5b5920b1dac44a63f1a49e55050ebde3aecc9eb82f93239 ;; \
        arm64) arch=aarch64; sha256=958ed7d1e00d0ea76590d27666efbf7a932281b3d7ba0c6b01b0ff26498f667f ;; \
        *) exit 2 ;; \
    esac; \
    curl --fail --silent --show-error --proto '=https' \
        -o /tmp/zig.tar.xz "https://ziglang.org/download/0.15.2/zig-$arch-linux-0.15.2.tar.xz"; \
    printf '%s  /tmp/zig.tar.xz\n' "$sha256" | sha256sum --check --strict; \
    mkdir /opt/zig; \
    tar -xJf /tmp/zig.tar.xz --strip-components=1 -C /opt/zig; \
    rm /tmp/zig.tar.xz; \
    ln -s /opt/zig/zig /usr/local/bin/zig; \
    test "$(zig version)" = 0.15.2
