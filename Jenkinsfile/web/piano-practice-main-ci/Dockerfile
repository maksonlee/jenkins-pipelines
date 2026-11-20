FROM docker.io/library/node:24.4.0-bookworm-slim@sha256:1b044a60874f1b57ac8c4e708ddb3a00e55b34586ebbacce09a48796dafcc799 AS node24
FROM mcr.microsoft.com/playwright:v1.65.1-noble@sha256:1507cbfc550a63a6b7396791ca4f00df17beb56342d2447b3f106c306f525579

# Keep the project's Node major while using Playwright's matching browsers and system libraries.
COPY --from=node24 /usr/local/bin/ /usr/local/bin/
COPY --from=node24 /usr/local/lib/node_modules/ /usr/local/lib/node_modules/
RUN node --version | grep -Fx v24.4.0 && npm --version
