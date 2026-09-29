FROM golang:1.27.1-bookworm AS build
ENV CGO_ENABLED=0 GOMAXPROCS=2
RUN git clone --depth 1 --branch RELEASE.2025-10-15T17-29-55Z https://github.com/minio/minio.git /src
WORKDIR /src
RUN test "$(git rev-parse HEAD)" = "9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a" \
    && go build -p 2 -tags kqueue -trimpath \
       -ldflags "$(MINIO_RELEASE=RELEASE go run buildscripts/gen-ldflags.go 2025-10-15T17:29:55Z)" \
       -o /out/minio .

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 minio && useradd --uid 10001 --gid 10001 --create-home minio \
    && mkdir /data && chown minio:minio /data
COPY --from=build /out/minio /usr/local/bin/minio
COPY --from=build /src/LICENSE /usr/share/doc/minio/LICENSE
COPY --from=build /src/NOTICE /usr/share/doc/minio/NOTICE
LABEL org.opencontainers.image.source="https://github.com/minio/minio" \
      org.opencontainers.image.revision="9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
USER 10001:10001
EXPOSE 9000 9001
ENTRYPOINT ["/usr/local/bin/minio"]
