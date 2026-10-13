# Deploy the docs site to k3s

`.github/workflows/docs-site.yml` publishes `site/` to a k3s server on a VPS:

1. `scripts/export-site.sh` generates the pages from `docs/` and runs `mint export` into `site/dist/`.
2. `deploy/docs/Dockerfile` copies that into an unprivileged nginx image, pushed to `ghcr.io/<owner>/team-kit-docs` with a tag per commit and `latest`.
3. The deploy job renders `deploy/k8s/` with that image and your host, pipes it over SSH to the server's own `kubectl`, and waits for the rollout.

Pull requests only build the image. Pushes to `main` build, push, and deploy. Until `DOCS_HOST` is set, pushes build and push but skip the deploy.

## What runs on the cluster

| Object | Notes |
|---|---|
| Namespace `team-kit-docs` | Everything below lives here |
| Deployment `team-kit-docs` | 2 replicas of nginx on port 8080, non-root, read-only root filesystem, `/healthz` probes, rolling updates with no downtime |
| Service `team-kit-docs` | Port 80 to the pods |
| Ingress `team-kit-docs` | k3s's bundled Traefik, host `DOCS_HOST`. With `DOCS_TLS_ISSUER` set, `ingress-tls.yaml` adds a cert-manager certificate and an HTTP-to-HTTPS redirect |

The Kubernetes API is never exposed: GitHub Actions reaches the server only through SSH.

The kit is now called faker-kit, but the deployment keeps the name `team-kit-docs`. That covers the image, the namespace, every object above, and the `ghcr-pull` secret inside that namespace. Renaming it means a new deployment on the server and a workflow change, which this repository's normal changes do not make.

## One-time setup

### On the VPS

1. Install k3s if it is not there: `curl -sfL https://get.k3s.io | sh -`.
2. Create a deploy user that can run `sudo k3s kubectl` without a password, for example in `/etc/sudoers.d/deploy`:

   ```text
   deploy ALL=(root) NOPASSWD: /usr/local/bin/k3s kubectl *
   ```

   Or give the user its own kubeconfig and set the `KUBECTL` variable to `kubectl` (see below).
3. Add a new SSH key's public half to `~deploy/.ssh/authorized_keys`. Generate it with `ssh-keygen -t ed25519 -f docs-deploy -N ''`.
4. Point the docs domain's DNS A record at the VPS.
5. For HTTPS, install cert-manager and create a ClusterIssuer (for example `letsencrypt-prod` using the HTTP-01 solver with `ingressClassName: traefik`).

### In GitHub (Settings → Secrets and variables → Actions)

| Name | Kind | Value |
|---|---|---|
| `VPS_HOST` | Secret | The server's hostname or IP |
| `VPS_USER` | Secret | The deploy user, e.g. `deploy` |
| `VPS_SSH_KEY` | Secret | The private key (`docs-deploy`) |
| `VPS_KNOWN_HOSTS` | Secret | Output of `ssh-keyscan -p <port> <host>`. Check the fingerprint against the server's |
| `VPS_PORT` | Secret, optional | SSH port, default 22 |
| `DOCS_HOST` | Variable | The docs domain, e.g. `docs.example.com`. Turns the deploy on |
| `DOCS_TLS_ISSUER` | Variable, optional | cert-manager ClusterIssuer name. Turns HTTPS on |
| `KUBECTL` | Variable, optional | The remote kubectl command, default `sudo k3s kubectl` |

The deploy job runs in the `docs` environment, so you can add required reviewers to it under Settings → Environments.

### The image pull

The first push creates the GHCR package as private. Either:

- Make it public: GitHub → your profile → Packages → `team-kit-docs` → Package settings → Change visibility. The docs are public anyway.
- Or keep it private and give the cluster a pull secret, using a token with only `read:packages`:

  ```bash
  sudo k3s kubectl create namespace team-kit-docs
  sudo k3s kubectl -n team-kit-docs create secret docker-registry ghcr-pull \
    --docker-server=ghcr.io --docker-username=<github user> --docker-password=<token>
  ```

The Deployment already refers to `ghcr-pull`. Kubernetes ignores the reference when the secret does not exist.

## Build and run it yourself

```bash
bash scripts/export-site.sh
docker build -f deploy/docs/Dockerfile -t team-kit-docs .
docker run --rm -p 8080:8080 team-kit-docs   # http://localhost:8080
```

For editing pages, `cd site && npx mint dev` is faster.

## Roll back

Every commit has its own image tag, so roll back to the previous version on the server:

```bash
sudo k3s kubectl -n team-kit-docs rollout undo deployment/team-kit-docs
```

Or re-run the deploy job of an earlier successful workflow run.
