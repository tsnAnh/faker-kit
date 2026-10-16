# The image every self-hosted CI job runs in: Ubuntu, like GitHub's hosted runners, so setup-python,
# setup-node, and the Rust toolchain actions work unchanged. team-kit-runner starts one container per job.
FROM ubuntu:24.04

ARG RUNNER_VERSION=2.338.0
ARG RUNNER_SHA256_ARM64=628b4a7258487b80c1d3c221095a7ded349f5ae0175fd3dfab708d07428f041b
ARG RUNNER_SHA256_X64=af4b794c1bc41d73d40535e3fe092a39f9679cd8d965954c2aca25a05ca41d32

# Daemonless, rootless image builds for pull requests (the docs workflow's build job): buildah as `runner`. The
# archive's signed metadata carries the checksums, so these exact versions are the pin. Node 22 is not in the
# image; a workflow step provides it with setup-node, like on a hosted runner.
ARG BUILDAH_VERSION=1.33.7+ds1-1ubuntu0.24.04.3
ARG UIDMAP_VERSION=1:4.13+dfsg1-4ubuntu3.2

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      build-essential ca-certificates curl git jq libssl-dev lsb-release pkg-config python3 python3-venv sudo tzdata unzip xz-utils zstd \
      buildah=${BUILDAH_VERSION} uidmap=${UIDMAP_VERSION} \
 && rm -rf /var/lib/apt/lists/*

# Jobs may use sudo inside their own throwaway container, as on a hosted runner. It reaches nothing outside it.
RUN useradd -m -u 1001 runner && echo "runner ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/runner \
 && mkdir -p /opt/hostedtoolcache && chown runner:runner /opt/hostedtoolcache

WORKDIR /home/runner/actions-runner
RUN case "$(dpkg --print-architecture)" in \
      arm64) arch=arm64; sha=$RUNNER_SHA256_ARM64 ;; \
      amd64) arch=x64; sha=$RUNNER_SHA256_X64 ;; \
      *) echo "no Actions runner for $(dpkg --print-architecture)" >&2; exit 1 ;; \
    esac \
 && curl -fsSL -o runner.tgz "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-${arch}-${RUNNER_VERSION}.tar.gz" \
 && echo "${sha}  runner.tgz" | sha256sum -c - \
 && tar xzf runner.tgz && rm runner.tgz \
 && ./bin/installdependencies.sh \
 && rm -rf /var/lib/apt/lists/* \
 && chown -R runner:runner /home/runner

ENV AGENT_TOOLSDIRECTORY=/opt/hostedtoolcache RUNNER_ALLOW_RUNASROOT=0
COPY --chmod=755 entrypoint.sh /usr/local/bin/team-kit-ci-entrypoint
# vfs storage and chroot isolation need no daemon, no fuse, and no device. The base image names no registry, so
# short names resolve to Docker Hub. ignore_chown_errors keeps a pull working when newuidmap cannot map a range.
RUN printf 'unqualified-search-registries = ["docker.io"]\n' > /etc/containers/registries.conf.d/00-docker-hub.conf \
 && mkdir -p /home/runner/.config/containers \
 && printf '[storage]\ndriver = "vfs"\n[storage.options.vfs]\nignore_chown_errors = "true"\n' > /home/runner/.config/containers/storage.conf \
 && chown -R runner:runner /home/runner/.config
ENV BUILDAH_ISOLATION=chroot
USER runner
ENTRYPOINT ["/usr/local/bin/team-kit-ci-entrypoint"]
