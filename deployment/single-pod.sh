#!/bin/sh
# Helm post-renderer: the shared chart need not expose Deployment strategy values.
set -eu
work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' 0
trap 'exit 130' INT
trap 'exit 143' TERM
cat > "$work_dir/resources.yaml"
cat > "$work_dir/kustomization.yaml" <<'YAML'
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - resources.yaml
patches:
  - target:
      group: apps
      version: v1
      kind: Deployment
    patch: |-
      - op: add
        path: /spec/replicas
        value: 1
      - op: add
        path: /spec/strategy
        value:
          type: Recreate
YAML
# Recreate waits for the old pod to terminate before starting the new revision.
kubectl kustomize "$work_dir"
