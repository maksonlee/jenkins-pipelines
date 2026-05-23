#!/bin/bash
JAVA_VERSIONS=(
  "8.0.452-tem"
  "11.0.27-tem"
  "17.0.15-tem"
  "21.0.7-tem"
)

GRADLEW="./gradlew"
TEST_CMD="$GRADLEW help"

echo "Scanning for the first compatible Java version..."

source "$HOME/.sdkman/bin/sdkman-init.sh"

for version in "${JAVA_VERSIONS[@]}"; do
  echo
  echo "Trying Java $version ..."
  sdk use java "$version" >/dev/null
  java -version

  if $TEST_CMD >/dev/null 2>&1; then
    echo "Java $version is compatible with Gradle."
    echo "Selected Java version: $version"
    return 0
  else
    echo "Java $version is not compatible."
  fi
done

echo
echo "No compatible Java version found."
return 1
