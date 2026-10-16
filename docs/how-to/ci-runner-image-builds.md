# Build container images on the CI runner

The self-hosted CI runner (`team-kit-runner`, image in `hermes/ci-runner/`) can build and validate a Docker image in a pull request without a Docker daemon, root, or the host's Docker socket. It does this with `buildah` run by the unprivileged `runner` user inside the job's throwaway container. This is how the docs site's `deploy/docs/Dockerfile` is checked on a pull request. The runner never pushes: pushing and deploying stay on `ubuntu-latest` jobs.

## What the container gets

Each job's container starts with `docker run --rm -i --security-opt seccomp=hermes/ci-runner/seccomp.json`. That one option is the only relaxation. The container is still removed after the job, still has no secrets, and the start line has no `--privileged`, no `--cap-add`, no socket mount, no `seccomp=unconfined`, and no AppArmor change. Tests in `hermes/tests/test_runner.py` fail if any of those appears.

`seccomp.json` is Docker's default profile (`moby/profiles` `seccomp/v0.2.1`, kept in `hermes/tests/fixtures/docker-default-seccomp.json`) plus two rules, each added because a measured failure needed it:

| Addition | Failure without it |
|---|---|
| `unshare` | `Error during unshare(CLONE_NEWUSER): Operation not permitted` |
| `mount` | `remount /, flags: 0x44000: operation not permitted` |

`setns`, `clone`, `clone3`, `umount2`, and `pivot_root` stay as in the default: the build passes without them. `hermes/tests/test_runner_seccomp.py` fails if the profile allows anything beyond those two rules. The unprivileged user still has no capability in the host's namespaces, so `mount` and `unshare` only act inside the namespace buildah makes for itself.

When the runner host's Docker changes, re-derive: copy that version's default profile over the fixture, rebuild `seccomp.json` from it plus the two rules, and update the Docker version in the header and this page. The version this profile was measured against is Docker 29.8.2 on x86_64. The default profile that Docker 29.8.2 embeds could not be fetched by its tag, so the fixture is the latest tagged `moby/profiles` release and the match is not proven.

## What the image provides

- `buildah` and `uidmap`, pinned to exact Ubuntu 24.04 package versions in the Dockerfile (the signed archive carries the checksums), run as `runner` with vfs storage and chroot isolation, so there is no daemon and no device.
- `unzip`, which `scripts/export-site.sh` needs.
- No Node. A workflow step provides Node 22, with `actions/setup-node`, as on a hosted runner. This kit does not edit workflows from this change.

A build in the job:

```bash
bash scripts/export-site.sh
buildah bud -f deploy/docs/Dockerfile -t team-kit-docs .
```

`BUILDAH_ISOLATION=chroot` is set in the image. Short image names resolve to Docker Hub.

## Roll it out

On the runner host, as the machine's owner:

```bash
team-kit-runner build                # builds the new image tag; a changed Dockerfile or seccomp.json is a new tag
team-kit-runner up --config ~/.hermes/team-kit/machine.yaml   # restarts every slot on the new image
```

Each slot starts its next job on the newest image. A job already running finishes on the old one.

## Prove it

Prove the image has the tool:

```bash
tag=$(docker images --format '{{.Repository}}:{{.Tag}}' team-kit-ci-runner | head -1)
docker run --rm --entrypoint buildah "$tag" --version
docker run --rm --entrypoint unzip "$tag" -v | head -1
```

Prove the build works under the profile and fails under the default one (this runs on a machine with Docker and builds a throwaway Dockerfile):

```bash
dir=$(mktemp -d); printf 'FROM scratch\nCOPY f /f\n' > "$dir/Dockerfile"; echo hi > "$dir/f"
docker run --rm --entrypoint buildah --security-opt seccomp=hermes/ci-runner/seccomp.json -v "$dir:/work:ro" -w /work "$tag" bud -t t .   # tags localhost/t
docker run --rm --entrypoint buildah -v "$dir:/work:ro" -w /work "$tag" bud -t t .   # unshare(CLONE_NEWUSER): Operation not permitted
```

Prove no extra privilege was given:

```bash
python3 -m pytest hermes/tests/test_runner.py hermes/tests/test_runner_seccomp.py -q
TEAM_KIT_DOCKER_TESTS=1 TEAM_KIT_RUNNER_IMAGE="$tag" python3 -m pytest hermes/tests/test_runner_seccomp.py -q   # retained denials
```

The second command runs `reboot`, `kexec_load`, `init_module`, `bpf`, `mount`, and a `ptrace` attach to a process that is not a child inside a job container, and expects `EPERM` from every one.

## What was and was not observed

Observed on x86_64 with Docker 29.8.2 in a cloud sandbox, with the profile: the `FROM scratch` build and the real `deploy/docs/Dockerfile` build (against a stand-in `site/dist`, because `mint export` could not download its renderer there) both succeed as `runner`; the image they produce is `amd64/linux`. Under the default profile the same build stops at the `unshare` error above. The retained-denial probe returned `EPERM` for all six calls.

Not yet observed, on the arm64 Pi: all of it. Run the commands above there and compare. Expected, not observed: the same `unshare` failure under the default profile, success under `seccomp.json`, and an `arm64/linux` image. The image architecture follows the runner's, so it is not assumed to match the docs deploy target (`docs-site.yml` builds the pushed image on its own runner).

Two things stayed as they were and will decide the Pi result. In the sandbox `newuidmap` could not write the subordinate id map even with `seccomp=unconfined` (only `--privileged` made it work), so buildah falls back to a single id mapping and the image sets `ignore_chown_errors` for vfs storage: layers extract, but file ownership inside the built image is not preserved. That is fine for a pull request check that does not push, and it is not fine for an image you ship. If the Pi's build instead stops on AppArmor, `kernel.unprivileged_userns_clone`, `user.max_user_namespaces`, or `/etc/subuid`, do not widen the container: stop and read the exact error, because each of those is a host setting or a separate permission, and the owner decides.
