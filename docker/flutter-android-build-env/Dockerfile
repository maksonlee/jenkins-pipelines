FROM ubuntu:24.04

ARG ANDROID_CMDLINE_TOOLS=13114758

# Install dependencies
RUN apt-get update && apt-get install -y \
    ca-certificates \
    curl \
    git \
    gnupg \
    unzip \
    xz-utils \
    zip \
    && rm -rf /var/lib/apt/lists/*

# Install Dart for FVM. Flutter SDK versions are managed by FVM at runtime.
RUN curl -fsSL https://dl-ssl.google.com/linux/linux_signing_key.pub \
    | gpg --dearmor -o /usr/share/keyrings/dart.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/dart.gpg arch=amd64] https://storage.googleapis.com/download.dartlang.org/linux/debian stable main" \
    > /etc/apt/sources.list.d/dart_stable.list && \
    apt-get update && apt-get install -y dart && \
    rm -rf /var/lib/apt/lists/*

# Environment paths
ENV SDKMAN_DIR=/home/ubuntu/.sdkman
ENV ANDROID_HOME=/home/ubuntu/Android
ENV FVM_CACHE_PATH=/home/ubuntu/fvm
ENV JAVA_HOME=$SDKMAN_DIR/candidates/java/current
ENV PATH=/home/ubuntu/.pub-cache/bin:$FVM_CACHE_PATH/default/bin:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$SDKMAN_DIR/bin:$SDKMAN_DIR/candidates/java/current/bin:/usr/lib/dart/bin:$PATH

# Use bash so we can use su -c with login shell behavior
SHELL ["/bin/bash", "-c"]

# Install SDKMAN and Java
RUN su - ubuntu -c "\
  curl -s https://get.sdkman.io | bash && \
  source ~/.sdkman/bin/sdkman-init.sh && \
  sdk install java 8.0.452-tem && \
  sdk install java 11.0.27-tem && \
  sdk install java 17.0.15-tem && \
  sdk install java 21.0.7-tem \
"

# Install Android command-line tools
RUN su - ubuntu -c "\
  mkdir -p ~/Android/cmdline-tools && \
  cd ~/Android && \
  curl -OL https://dl.google.com/android/repository/commandlinetools-linux-${ANDROID_CMDLINE_TOOLS}_latest.zip && \
  unzip commandlinetools-linux-${ANDROID_CMDLINE_TOOLS}_latest.zip -d cmdline-tools && \
  mv cmdline-tools/cmdline-tools cmdline-tools/latest && \
  rm commandlinetools-linux-${ANDROID_CMDLINE_TOOLS}_latest.zip && \
  source ~/.sdkman/bin/sdkman-init.sh && \
  yes | ~/Android/cmdline-tools/latest/bin/sdkmanager --licenses \
"

# Install FVM. The Flutter SDK cache is mounted by Jenkins and populated with fvm install.
RUN su - ubuntu -c "\
  dart pub global activate fvm \
"

COPY setup-java-from-gradle.sh /home/ubuntu/setup-java-from-gradle.sh

RUN su - ubuntu -c "mkdir -p ~/.gradle ~/pub-cache ~/fvm"

RUN git config --system --add safe.directory /workspace

WORKDIR /workspace
