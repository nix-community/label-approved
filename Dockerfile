# Inspired from https://mitchellh.com/writing/nix-with-dockerfiles
#
# Using a Dockerfile instead of Nix's native Docker building support because
# this gets built by GitHub Actions' release infra (and it only supports plain
# Dockerfiles).

FROM nixos/nix:latest AS builder

COPY . /tmp/build
WORKDIR /tmp/build

RUN nix \
    --extra-experimental-features "nix-command flakes" \
    --option filter-syscalls false \
    build

RUN mkdir /tmp/nix-store-closure
RUN cp -R $(nix-store -qR result/) /tmp/nix-store-closure

FROM scratch

WORKDIR /app

COPY --from=builder /tmp/nix-store-closure /nix/store
COPY --from=builder /tmp/build/result /app
ENTRYPOINT ["/app/bin/label-approved"]
