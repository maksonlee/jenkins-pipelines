#!/bin/bash
# Run a project's repository-root Python tests (tests/ with requirements-dev.txt) in the pinned Python image,
# with branch coverage of at least BACKEND_COVERAGE_FLOOR percent. With BACKEND_POSTGRES_DB a disposable PostGIS
# container is attached through TEST_DATABASE_URL. The optional scripts/validate_play_metadata.py and
# scripts/prepare_flutter_test_fixtures.py hooks run in the same container; only .tmp/flutter-test-fixtures is
# writable.
set -euo pipefail
coverage_floor="${BACKEND_COVERAGE_FLOOR:?BACKEND_COVERAGE_FLOOR is required}"
if ! [[ "${coverage_floor}" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  echo "Invalid backend coverage floor: ${coverage_floor}" >&2
  exit 1
fi
postgres_db="${BACKEND_POSTGRES_DB:-}"
if [ -n "${postgres_db}" ] && ! [[ "${postgres_db}" =~ ^[a-z_][a-z0-9_]*$ ]]; then
  echo "Invalid backend PostgreSQL database name: ${postgres_db}" >&2
  exit 1
fi
if [ ! -d source/tests ] || [ ! -f source/requirements-dev.txt ]; then
  echo "Backend tests need source/tests and source/requirements-dev.txt." >&2
  exit 1
fi
test_id=$(printf %s "quality-${BUILD_TAG}" | tr -c 'A-Za-z0-9_.-' '-')
test_network=""
test_db=""

cleanup() {
  if [ -n "${test_db}" ]; then
    docker rm -f -v "${test_db}" >/dev/null 2>&1 || true
  fi
  if [ -n "${test_network}" ]; then
    docker network rm "${test_network}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

docker pull "${PYTHON_TEST_IMAGE}"
docker_args=(
  --rm
  -e PYTHONDONTWRITEBYTECODE=1
  -e COVERAGE_FILE=/tmp/.coverage
  -e PIP_ROOT_USER_ACTION=ignore
  -e "BACKEND_COVERAGE_FLOOR=${coverage_floor}"
  -v "$PWD/source:/workspace:ro"
  -w /workspace
)
if [ -f source/scripts/prepare_flutter_test_fixtures.py ]; then
  mkdir -p source/.tmp/flutter-test-fixtures
  docker_args+=(
    -e "FLUTTER_FIXTURE_OWNER=$(id -u):$(id -g)"
    -v "$PWD/source/.tmp/flutter-test-fixtures:/workspace/.tmp/flutter-test-fixtures"
  )
fi

if [ -n "${postgres_db}" ]; then
  docker pull "${POSTGIS_TEST_IMAGE}"
  test_network="${test_id}-network"
  test_db="${test_id}-db"
  docker network create "${test_network}" >/dev/null
  docker run -d --rm \
    --name "${test_db}" \
    --network "${test_network}" \
    -e POSTGRES_PASSWORD=test-password \
    -e "POSTGRES_DB=${postgres_db}" \
    "${POSTGIS_TEST_IMAGE}" >/dev/null
  ready=false
  ready_checks=0
  for _ in $(seq 1 60); do
    if docker exec "${test_db}" pg_isready -U postgres -d "${postgres_db}" >/dev/null 2>&1; then
      ready_checks=$((ready_checks + 1))
      if [ "${ready_checks}" -ge 3 ]; then
        ready=true
        break
      fi
    else
      ready_checks=0
    fi
    sleep 1
  done
  if [ "${ready}" != true ]; then
    docker logs "${test_db}" >&2
    echo "PostgreSQL/PostGIS test database did not become ready." >&2
    exit 1
  fi
  docker_args+=(
    --network "${test_network}"
    -e "TEST_DATABASE_URL=postgresql://postgres:test-password@${test_db}:5432/${postgres_db}"
  )
fi

docker run "${docker_args[@]}" "${PYTHON_TEST_IMAGE}" bash -lc '
  set -euo pipefail
  if [ -f scripts/prepare_flutter_test_fixtures.py ]; then
    trap "chown -R ${FLUTTER_FIXTURE_OWNER} .tmp/flutter-test-fixtures" EXIT
  fi
  apt-get update >/dev/null
  apt-get install -y --no-install-recommends postgresql-client >/dev/null
  git config --global --add safe.directory /workspace
  python -m pip install --disable-pip-version-check -r requirements-dev.txt
  python -m pip check
  if [ -f scripts/validate_play_metadata.py ]; then
    python scripts/validate_play_metadata.py
  fi
  python -m coverage run --branch -m unittest discover -s tests -v
  python -m coverage report --fail-under="${BACKEND_COVERAGE_FLOOR}"
  if [ -f scripts/prepare_flutter_test_fixtures.py ]; then
    python scripts/prepare_flutter_test_fixtures.py --output .tmp/flutter-test-fixtures
  fi
'
