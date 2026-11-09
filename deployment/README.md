# Kubernetes web deployment

> **Last updated:** 2026-11-09 · **Status:** deployment configuration prepared; cluster rollout pending.

The GitLab pipeline in [`.gitlab-ci.yml`](../.gitlab-ci.yml) follows the Presidio
Analyzer deployment: the Kubernetes runner builds with `ops/snippets` and Kaniko,
pushes `$HARBOR_REGISTRY/triton/vectorcraft:$CI_COMMIT_SHA`, then deploys the
default branch to one production environment. Other branches build without
deploying. There is no test environment or test release. The existing GitHub
workflows continue to call `storytold/craft-common-actions` independently.

The [Dockerfile](../Dockerfile) compiles `apps/vectorcraft-web` with Trunk and
serves the generated HTML, JavaScript, WASM, icons and fonts through nginx on
port 8080. Rust and nginx images are digest-pinned; Trunk is pinned to the shared
release workflow's version. The craft-fonts commit matches VectorCraft's release
workflow, and its existing post-build hook verifies and copies web fonts with
their licences. No handwritten JavaScript is added.

## One-time setup

Provision namespace `vectorcraft` in the intended Rancher project and give the
CI identity deployment access, including Secrets. The pipeline defaults to
`CREATE_NAMESPACE=no`, so namespace administration needs no cluster-wide grants.
The registry identity needs push/pull access to Harbor project `triton` and pull
access to `ops/generic-microservice` chart version `0.1.1`.

Inherit the same instance/group CI variables as Presidio Analyzer:

| Variables | Purpose |
| --- | --- |
| `CI_SNIPPETS_REF` | Version of `ops/snippets`; must exist before includes resolve. |
| `RGCPDEPLOYER`, `RGCPDEPLOYERVERSION` | Runner image with Helm 3 and kubectl, including `kubectl kustomize`. |
| `RGAG_CA_BASE64`, `RGAG_CA`, `RG23_CA` | Public CA certificates required by the shared snippets. `RGAG_CA_BASE64` also supplies build-container trust. |
| `HARBOR_REGISTRY`, `HARBOR_USER`, `HARBOR_PASS` | Harbor address and registry credentials. |
| `KUBECONFIG_rad_app_01` | Cluster credentials, as base64 kubeconfig or a GitLab File variable. |

`TARGET_KUBECONFIG` can override the default kubeconfig source. Keep credentials
in appropriately protected and environment-scoped CI variables. Never commit
them. Build arguments may carry public CA certificates only, never private keys
or registry/cluster credentials. The deploy job refreshes the `harbor-registry`
pull Secret in the existing namespace and removes its temporary credential files
on exit, including after failures.

The default URL is `https://vectorcraft.bms7g.p.oediv.io`. Override `INGRESS_HOST`
for another DNS name; the deploy script updates both the route and TLS host.
[values.yaml](values.yaml) uses the same internal nginx ingress class, external
DNS annotation and production cert-manager issuer as Presidio Analyzer. A client
must have network access to that ingress. For public internet access, select a
public ingress class and the appropriate DNS/issuer in the values file; CORS
does not change firewall, routing or ingress reachability.

## One pod

The shared chart creates a ClusterIP Service on port 80 targeting the nginx
container on 8080, with startup/liveness/readiness checks at `/healthz`.
`replicaCount: 1` and disabled autoscaling keep one application replica.
[single-pod.sh](single-pod.sh), a Helm post-renderer using kubectl's bundled
Kustomize, sets the rendered Deployment's replica count to one and replaces its
update strategy with `Recreate`. This prevents a second application pod during
an upgrade: the old revision terminates before its replacement starts, causing
a brief interruption while updating. Always use this post-renderer for manual
Helm upgrades too. There are no extra services such as Redis or a database.

The pod runs as UID/GID 101 with dropped Linux capabilities and no privilege
escalation. Its requests are 50m CPU / 32 MiB memory, with limits of 500m / 128
MiB. The application runs in each client's browser, so these resources cover
static file serving. Documents, preferences and recovery data remain in browser
storage/downloads; no pod volume is needed. The web build has no TCP control
server or server-side command API; see [web hosting](../packaging/web/README.md).

## CORS and web serving

[nginx.conf](nginx.conf) sends `Access-Control-Allow-Origin: *` on successful
responses, preflight responses and errors. It permits `GET`, `HEAD` and
`OPTIONS`, arbitrary request headers and the explicitly listed `Authorization`
header. OPTIONS returns 204, including for asset paths. Cross-origin callers
must omit cookies/session credentials, as required by wildcard CORS. There is
no application login. Ingress CORS is disabled so the response contains one
Allow-Origin header; CORS also works when accessing the Service directly.

nginx serves WASM with its proper MIME type and gzip compression, and revalidates
`index.html` so reloads discover the current hashed files. Missing assets return
404 rather than HTML. HTTPS at the ingress provides a secure context for WebGPU,
clipboard access and Web Locks. Reference:
[nginx response headers](https://nginx.org/en/docs/http/ngx_http_headers_module.html),
[CORS request headers](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Access-Control-Allow-Headers),
[Kubernetes deployment strategies](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/#strategy).

## Build and verify

```sh
./build.sh
docker run --rm -p 127.0.0.1:8080:8080 triton/vectorcraft:local
```

Open `http://localhost:8080/`. From another terminal:

```sh
python3 deployment/tests/check_http.py http://127.0.0.1:8080/
python3 -m unittest discover -s deployment/tests -v
```

The first command checks the actual built page, JavaScript and WASM, MIME types,
compression, health, CORS/preflight, and missing assets. The unit tests exercise
deploy/stop orchestration, both kubeconfig formats, credential cleanup and
failure diagnostics without contacting a cluster. With kubectl installed they
also render a rolling Deployment offline and verify conversion to one replica
with `Recreate`. The GitLab validation job runs the orchestration tests; the
offline rendering check also runs locally when kubectl is available.

To check just the nginx image independently of the Rust downloads:

```sh
./build.sh triton/vectorcraft:web-server-check --target web-server
python3 deployment/tests/check_runtime.py
```

This uses temporary synthetic files to check the actual server configuration,
including running with a read-only root filesystem and dropped capabilities.
It does not verify the application's WASM build. The initial checks passed;
the full image build was blocked at the Rust toolchain download by network
certificate trust, and an independent endpoint check received a proxy 403.
The full source build, shared chart rendering and a cluster rollout still need
validation in CI/the deployment environment.

Behind a TLS-inspecting proxy, export `BUILD_CA_CERT_BASE64` with the public CA
certificate (base64, single line), then pass `--build-arg BUILD_CA_CERT_BASE64`
after the image tag to `build.sh`. The Dockerfile installs that CA only in the
builder. TLS verification stays enabled. The default build needs network access
to Debian, Rust distributions, crates.io and GitHub, including Trunk's tools.

CI calls `sh deploy.sh --kubernetes deploy`. A manual deployment needs the
variables above plus `NAMESPACE`, `RELEASE`, `VALUES=deployment/values.yaml`,
`INGRESS_HOST`, `DESTINATION_IMAGE` and `IMAGE_VERSION`. The stop action calls
`undeploy`; it removes only the Helm release, leaving the namespace and registry
Secret. Failed cluster jobs retain workload status and events in
`deployment-diagnostics.txt` for seven days, excluding application logs and
Secret contents.

## Revision history

| Date | Change |
| --- | --- |
| 2026-11-09 | Added GitLab/Harbor build and production-only Helm deployment, nonroot web image, wildcard CORS, one-pod updates and deployment checks. |
