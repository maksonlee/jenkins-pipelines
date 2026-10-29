# Flutter build image: android-build-env plus Dart and FVM. Flutter SDK versions are installed by FVM at run
# time into the FVM cache that Jenkins mounts (see Jenkinsfile/flutter/flutter-android-build), so this image
# does not need rebuilding for a new Flutter release.
FROM harbor.maksonlee.com/library/android-build-env@sha256:b88ae3849ada50e9943fb7cab8b1073063e1be23189c132395f9a853bb6154d7

ARG DART_VERSION=3.13.7-1
ARG FVM_VERSION=4.1.0

RUN set -eux; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends gnupg; \
    curl -fsSL https://dl-ssl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/dart.gpg; \
    echo "deb [signed-by=/usr/share/keyrings/dart.gpg arch=amd64] https://storage.googleapis.com/download.dartlang.org/linux/debian stable main" \
        > /etc/apt/sources.list.d/dart_stable.list; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "dart=$DART_VERSION"; \
    rm -rf /var/lib/apt/lists/*

ENV FVM_CACHE_PATH=/home/ubuntu/fvm
ENV PATH=/home/ubuntu/.pub-cache/bin:$FVM_CACHE_PATH/default/bin:/usr/lib/dart/bin:$PATH

RUN su - ubuntu -c "dart pub global activate fvm $FVM_VERSION && mkdir -p ~/.gradle ~/pub-cache ~/fvm"
