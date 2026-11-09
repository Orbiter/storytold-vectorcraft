ARG RUST_IMAGE=rust:1.99.0-slim-bookworm@sha256:2c3a22f0a5533ea2dd5a16627bc841228151faa2d4de2644ac9987e4a2f1f2fa
ARG NGINX_IMAGE=nginx:stable-alpine@sha256:583b7f2fce1cdb8da5ad5c39ebd3c33b98b87d493190e548bdee1fa8c619c311

FROM ${RUST_IMAGE} AS web-build
ARG TRUNK_VERSION=0.21.14
# Keep this aligned with the optional web fonts in .github/workflows/release.yml.
ARG CRAFT_FONTS_REF=b076417b5880bd4154bd22f00695b1d28c6bea83
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*
# Public CA certificates only; never pass private keys or credentials as build args.
# Kaniko's own trust store is separate from the commands inside the build image.
ARG BUILD_CA_CERT_BASE64
RUN if [ -n "${BUILD_CA_CERT_BASE64}" ]; then \
        printf '%s' "${BUILD_CA_CERT_BASE64}" | base64 -d \
          > /usr/local/share/ca-certificates/build-network.crt \
        && update-ca-certificates; \
    fi
RUN rustup target add wasm32-unknown-unknown \
    && cargo install trunk --locked --version "${TRUNK_VERSION}"
RUN mkdir -p /opt/craft-fonts \
    && curl --fail --location --retry 3 \
       "https://codeload.github.com/storytold/craft-fonts/tar.gz/${CRAFT_FONTS_REF}" \
       -o /tmp/craft-fonts.tar.gz \
    && tar -xzf /tmp/craft-fonts.tar.gz --strip-components=1 -C /opt/craft-fonts \
    && rm /tmp/craft-fonts.tar.gz
ENV CRAFT_FONTS_DIR=/opt/craft-fonts CRAFT_FONTS_REQUIRED=1
WORKDIR /src
COPY . .
WORKDIR /src/apps/vectorcraft-web
RUN trunk build --release --locked

FROM ${NGINX_IMAGE} AS web-server
COPY deployment/nginx.conf /etc/nginx/nginx.conf
USER 101:101
EXPOSE 8080
STOPSIGNAL SIGQUIT
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]

FROM web-server AS runtime
COPY --from=web-build /src/dist/web/ /usr/share/nginx/html/
COPY LICENSE-MIT LICENSE-APACHE NOTICE ASSETS.md /usr/share/nginx/html/licenses/
RUN test -s /usr/share/nginx/html/index.html && nginx -t
