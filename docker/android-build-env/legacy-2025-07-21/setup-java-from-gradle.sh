#!/bin/bash
JAVA_VERSIONS=(
  "8.0.452-tem"
  "11.0.27-tem"
  "17.0.15-tem"
  "21.0.7-tem"
)

GRADLEW="./gradlew"
TEST_CMD="$GRADLEW help"

echo "🔍 Scanning for the first compatible Java version..."

for version in "${JAVA_VERSIONS[@]}"; do
  echo -e "\n🧪 Trying Java $version ..."
  sdk use java "$version" >/dev/null
  java -version

  if $TEST_CMD &>/dev/null; then
    echo "✅ Java $version is compatible with Gradle!"
    echo "🎯 Selected Java version: $version"
    return 0
  else
    echo "❌ Java $version is NOT compatible."
  fi
done

echo -e "\n❌ No compatible Java version found."
return 1
