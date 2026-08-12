ARG PYTHON_BASE_IMAGE=python:3.14-slim

FROM ${PYTHON_BASE_IMAGE} AS node-document-builder

RUN set -eu; \
    if ! apt-get -o Acquire::Retries=5 update; then \
        sleep 2; \
        apt-get -o Acquire::Retries=5 update; \
    fi; \
    if ! apt-get -o Acquire::Retries=5 install -y --no-install-recommends nodejs npm; then \
        sleep 2; \
        apt-get -o Acquire::Retries=5 --fix-missing install -y --no-install-recommends nodejs npm; \
    fi; \
    rm -rf /var/lib/apt/lists/*
COPY package.json package-lock.json /opt/openppx-document-runtime/
RUN npm ci \
        --prefix /opt/openppx-document-runtime \
        --omit=dev \
        --no-audit \
        --no-fund

FROM ${PYTHON_BASE_IMAGE} AS python-document-builder

RUN python -m venv /opt/openppx-document-runtime/python
COPY requirements-document.txt /tmp/requirements-document.txt
RUN /opt/openppx-document-runtime/python/bin/pip install \
        --no-cache-dir \
        --requirement /tmp/requirements-document.txt \
    && /opt/openppx-document-runtime/python/bin/python -m pip uninstall --yes pip

FROM ${PYTHON_BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive
ENV NODE_PATH=/opt/openppx-document-runtime/node_modules
ENV PATH=/opt/openppx-document-runtime/python/bin:${PATH}

RUN set -eu; \
    set -- \
        ca-certificates \
        coreutils \
        fonts-liberation \
        fonts-noto-cjk \
        git \
        libreoffice-calc-nogui \
        libreoffice-impress-nogui \
        libreoffice-writer-nogui \
        nodejs \
        pandoc \
        poppler-utils \
        procps \
        tini \
        unzip \
        zip; \
    if ! apt-get -o Acquire::Retries=5 update; then \
        sleep 2; \
        apt-get -o Acquire::Retries=5 update; \
    fi; \
    if ! apt-get -o Acquire::Retries=5 install -y --no-install-recommends "$@"; then \
        sleep 2; \
        apt-get -o Acquire::Retries=5 --fix-missing install -y --no-install-recommends "$@"; \
    fi; \
    rm -rf /var/lib/apt/lists/*

RUN python -m pip uninstall --yes pip setuptools wheel \
    && rm -rf /usr/local/lib/python3.14/ensurepip \
    && rm -rf /usr/share/nodejs/corepack \
    && rm -f /usr/bin/corepack /usr/bin/pnpm /usr/bin/yarn

COPY --from=node-document-builder \
    /opt/openppx-document-runtime/node_modules \
    /opt/openppx-document-runtime/node_modules
COPY --from=python-document-builder \
    /opt/openppx-document-runtime/python \
    /opt/openppx-document-runtime/python

RUN cat > /usr/local/bin/openppx-sandbox-entrypoint <<'EOF' \
    && chmod +x /usr/local/bin/openppx-sandbox-entrypoint
#!/bin/sh
set -eu

: "${HOME:=/tmp/openppx-home}"
mkdir -p "$HOME"
export HOME
export TMPDIR="${TMPDIR:-/tmp}"

exec "$@"
EOF

ENTRYPOINT ["/usr/local/bin/openppx-sandbox-entrypoint"]
CMD ["python", "--version"]
