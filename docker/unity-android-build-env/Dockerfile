# Unity 6000.6.7f1 Android, pinned to the GameCI linux/amd64 OCI index.
FROM unityci/editor:ubuntu-6000.6.7f1-android-3@sha256:f69fc708d7d6c0aa03521b6facb22c56744d43da2537d213e3a2e9f5bfb61e50

LABEL org.opencontainers.image.title="Unity 6000.6.7f1 Android build environment" \
      org.opencontainers.image.description="Pinned GameCI Editor with a writable Jenkins UID 1000 workspace and home" \
      org.opencontainers.image.source="https://github.com/maksonlee/jenkins-pipelines" \
      org.opencontainers.image.base.digest="sha256:f69fc708d7d6c0aa03521b6facb22c56744d43da2537d213e3a2e9f5bfb61e50"

# GameCI CLI runs as root and mounts its dedicated homeDir at /root. A future
# Jenkins docker run with -u 1000:1000 can use /home/ubuntu as HOME instead.
USER root
RUN set -eu; \
    test "$(cat "$UNITY_PATH/version")" = '6000.6.7f1'; \
    test -x "$UNITY_PATH/Editor/Unity"; \
    android="$UNITY_PATH/Editor/Data/PlaybackEngines/AndroidPlayer"; \
    for component in SDK NDK OpenJDK; do test -d "$android/$component"; done; \
    for tool in git curl jq zip unzip; do command -v "$tool" >/dev/null; done; \
    install -d -m 0755 -o 1000 -g 1000 /workspace /home/ubuntu

WORKDIR /workspace
CMD ["/bin/bash"]
