# Reconstructed on 2026-10-29 from `docker image history --no-trunc` of
# android-build-env@sha256:aa44de3756d5850330663fc920b50011da481d3e7e3b3aab2366d3360dce24ab (built 2025-07-21),
# the image the native Android pipelines run in and the base of docker/gst-stream-wall-android-build-env.
# Its original Dockerfile was not in this repository. setup-java-from-gradle.sh is the copy found in that image.
# Building this file today does not reproduce that digest: ubuntu:24.04 and the SDKMAN installer are not pinned.
FROM ubuntu:24.04

RUN apt-get update && apt-get install -y \
    zip unzip curl \
    && rm -rf /var/lib/apt/lists/*

ENV SDKMAN_DIR=/home/ubuntu/.sdkman
ENV ANDROID_HOME=/home/ubuntu/Android
ENV PATH=$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$SDKMAN_DIR/bin:$SDKMAN_DIR/candidates/java/current/bin:$PATH

SHELL ["/bin/bash", "-c"]

RUN su - ubuntu -c "\
  curl -s https://get.sdkman.io | bash && \
  source ~/.sdkman/bin/sdkman-init.sh && \
  sdk install java 8.0.452-tem && \
  sdk install java 11.0.27-tem && \
  sdk install java 17.0.15-tem && \
  sdk install java 21.0.7-tem \
"

RUN su - ubuntu -c "\
  mkdir -p ~/Android/cmdline-tools && \
  cd ~/Android && \
  curl -OL https://dl.google.com/android/repository/commandlinetools-linux-13114758_latest.zip && \
  unzip commandlinetools-linux-13114758_latest.zip -d cmdline-tools && \
  mv cmdline-tools/cmdline-tools cmdline-tools/latest && \
  rm commandlinetools-linux-13114758_latest.zip && \
  source ~/.sdkman/bin/sdkman-init.sh && \
  yes | $ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager --licenses \
"

COPY setup-java-from-gradle.sh /home/ubuntu/setup-java-from-gradle.sh

RUN su - ubuntu -c "mkdir ~/.gradle"

WORKDIR /workspace
