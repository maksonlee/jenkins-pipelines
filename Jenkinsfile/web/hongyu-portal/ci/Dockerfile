FROM docker.io/library/composer:2.9@sha256:b09bccd91a78fe8a9ab4b33d707b862e8fe54fec17782e32683ad2a69c46867d AS composer
FROM mcr.microsoft.com/playwright:v1.65.1-noble@sha256:1507cbfc550a63a6b7396791ca4f00df17beb56342d2447b3f106c306f525579

# Ubuntu noble ships PHP 8.3.6, the platform version in the project's composer.json. The Playwright base adds Node
# and Chromium for the responsive browser tests.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        php8.3-cli php8.3-sqlite3 php8.3-xml php8.3-mbstring php8.3-curl php8.3-zip php8.3-intl php8.3-bcmath unzip \
    && rm -rf /var/lib/apt/lists/*
COPY --from=composer /usr/bin/composer /usr/local/bin/composer
RUN php --version | grep -F 'PHP 8.3.' && composer --version && node --version
