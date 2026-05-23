FROM ubuntu:24.04

ARG FLUTTER_VERSION=3.44.0
ARG ANDROID_CMDLINE_TOOLS=13114758

# Install dependencies
RUN apt-get update && apt-get install -y \
    ca-certificates \
    curl \
    git \
    unzip \
    xz-utils \
    zip \
    && rm -rf /var/lib/apt/lists/*

# Environment paths
ENV SDKMAN_DIR=/home/ubuntu/.sdkman
ENV ANDROID_HOME=/home/ubuntu/Android
ENV FLUTTER_HOME=/home/ubuntu/flutter
ENV JAVA_HOME=$SDKMAN_DIR/candidates/java/current
ENV PATH=$FLUTTER_HOME/bin:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$SDKMAN_DIR/bin:$SDKMAN_DIR/candidates/java/current/bin:$PATH

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

# Install Flutter
RUN su - ubuntu -c "\
  git clone --depth 1 --branch ${FLUTTER_VERSION} https://github.com/flutter/flutter.git ~/flutter && \
  source ~/.sdkman/bin/sdkman-init.sh && \
  sdk use java 17.0.15-tem && \
  export JAVA_HOME=~/.sdkman/candidates/java/current && \
  export PATH=~/flutter/bin:~/Android/cmdline-tools/latest/bin:~/Android/platform-tools:\$PATH && \
  flutter config --no-analytics && \
  yes | flutter doctor --android-licenses && \
  flutter doctor -v \
"

COPY setup-java-from-gradle.sh /home/ubuntu/setup-java-from-gradle.sh

RUN su - ubuntu -c "mkdir -p ~/.gradle ~/.pub-cache"

RUN git config --system --add safe.directory /home/ubuntu/flutter && \
    git config --system --add safe.directory /workspace

WORKDIR /workspace
