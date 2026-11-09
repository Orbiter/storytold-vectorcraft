#!/bin/sh
set +x
set -eu

action=${1:-}
case "$action" in
  deploy|undeploy) ;;
  *) echo 'Usage: sh .ci/cluster.sh deploy|undeploy' >&2; exit 2 ;;
esac

sh .ci/check-env.sh cluster
: "${NAMESPACE:?NAMESPACE is required}"
: "${HELM_TIMEOUT:=1200s}"
: "${HELM_CHART_VERSION:=0.1.1}"

work_dir=$(mktemp -d)
# Keep the caller's kubeconfig intact and isolate simultaneous deployments.
export KUBECONFIG="$work_dir/kubeconfig"
kubeconfig_ready=no
phase=cluster-login
cleanup() {
  status=$?
  trap - 0
  # Diagnostics must not interrupt credential cleanup or replace the real error.
  set +e
  if [ "$status" -ne 0 ]; then
    printf 'ERROR: %s failed during %s (exit %s).\n' "$action" "$phase" "$status" >&2
    {
      printf 'Action: %s; phase: %s; namespace: %s; release: %s; exit: %s\n' "$action" "$phase" "$NAMESPACE" "$RELEASE" "$status"
      if [ "$kubeconfig_ready" = yes ]; then
        cat "$work_dir/cluster-target.txt" 2>/dev/null
        printf 'CI permission: get secrets in %s: ' "$NAMESPACE"
        kubectl --request-timeout=20s auth can-i get secrets --namespace="$NAMESPACE" || true
        printf 'CI permission: list deployments.apps in %s: ' "$NAMESPACE"
        kubectl --request-timeout=20s auth can-i list deployments.apps --namespace="$NAMESPACE" || true
        kubectl --request-timeout=20s -n "$NAMESPACE" get deployments,replicasets,pods,services,ingresses -o wide || true
        kubectl --request-timeout=20s -n "$NAMESPACE" get events --sort-by=.metadata.creationTimestamp || true
      else
        echo 'Kubeconfig preparation failed; cluster diagnostics were skipped.'
      fi
    } > deployment-diagnostics.txt 2>&1
    cat deployment-diagnostics.txt
  fi
  rm -rf "$work_dir"
  exit "$status"
}
trap cleanup 0
trap 'exit 130' INT
trap 'exit 143' TERM

umask 077
if [ -f "$TARGET_KUBECONFIG" ]; then
  cat "$TARGET_KUBECONFIG" > "$KUBECONFIG"
else
  # Some base64 implementations ignore invalid characters instead of failing.
  # Refuse those before any kubectl command can use partially decoded credentials.
  case "$TARGET_KUBECONFIG" in
    *[!A-Za-z0-9+/=[:space:]]*)
      echo 'ERROR: TARGET_KUBECONFIG must contain a base64 kubeconfig or a CI File variable path.' >&2
      exit 1
      ;;
  esac
  if ! printf '%s' "$TARGET_KUBECONFIG" | base64 -d > "$KUBECONFIG"; then
    echo 'ERROR: TARGET_KUBECONFIG must contain a base64 kubeconfig or a CI File variable path.' >&2
    exit 1
  fi
fi
kubeconfig_ready=yes
# Namespace-scoped credentials need no access to kube-system.
printf 'Using cluster credentials for namespace %s (kubeconfig contents are hidden).\n' "$NAMESPACE"
{
  printf 'CI Kubernetes context: '
  kubectl config current-context || true
  printf 'CI Kubernetes API server: '
  kubectl config view --minify -o 'jsonpath={.clusters[0].cluster.server}' || true
  printf '\n'
} > "$work_dir/cluster-target.txt" 2>&1
cat "$work_dir/cluster-target.txt"

if [ "$action" = undeploy ]; then
  phase=helm-uninstall
  helm uninstall "$RELEASE" --namespace "$NAMESPACE" --timeout "$HELM_TIMEOUT" --wait
  exit 0
fi

for name in VALUES INGRESS_HOST DESTINATION_IMAGE IMAGE_VERSION; do
  test -n "$(printenv "$name")" || { echo "ERROR: $name is required" >&2; exit 1; }
done
test -f "$VALUES" || { echo "ERROR: Values file not found: $VALUES" >&2; exit 1; }

phase=namespace
if [ "${CREATE_NAMESPACE:-no}" = yes ]; then
  if kubectl --request-timeout=20s get namespace "$NAMESPACE" --ignore-not-found -o name \
    > "$work_dir/namespace-name" 2> "$work_dir/namespace-error"; then
    if [ ! -s "$work_dir/namespace-name" ]; then
      kubectl create namespace "$NAMESPACE"
    fi
  else
    namespace_status=$?
    cat "$work_dir/namespace-error" >&2
    if grep -q 'Forbidden' "$work_dir/namespace-error"; then
      printf 'Namespace lookup is forbidden; continuing with pre-provisioned namespace %s.\n' "$NAMESPACE"
    else
      exit "$namespace_status"
    fi
  fi
else
  printf 'Using pre-provisioned namespace %s; no cluster-wide namespace lookup or creation.\n' "$NAMESPACE"
fi

phase=namespace-access
if ! kubectl --request-timeout=20s auth can-i get secrets --namespace="$NAMESPACE" --quiet; then
  printf 'ERROR: CI credentials cannot access Secrets in %s. Assign the namespace to the intended Rancher project and grant the CI user deployment access there.\n' "$NAMESPACE" >&2
  exit 1
fi

phase=registry-secret
# Refresh credentials on each deployment; do not print the generated Secret.
kubectl -n "$NAMESPACE" create secret docker-registry harbor-registry \
  --docker-server="$HARBOR_REGISTRY" --docker-username="$HARBOR_USER" \
  --docker-password="$HARBOR_PASS" --dry-run=client -o yaml > "$work_dir/registry-secret.yaml"
kubectl -n "$NAMESPACE" apply -f "$work_dir/registry-secret.yaml"

phase=helm-registry-login
export HELM_REGISTRY_CONFIG="$work_dir/helm-registry.json"
printf '%s' "$HARBOR_PASS" | helm registry login "$HARBOR_REGISTRY" \
  --username "$HARBOR_USER" --password-stdin

phase=helm-upgrade
printf 'Deploying %s:%s to %s at https://%s (readiness: /healthz).\n' \
  "$DESTINATION_IMAGE" "$IMAGE_VERSION" "$NAMESPACE" "$INGRESS_HOST"
set -- "$RELEASE" "oci://$HARBOR_REGISTRY/ops/generic-microservice" \
  --install --wait --timeout "$HELM_TIMEOUT" --namespace "$NAMESPACE" \
  --version "$HELM_CHART_VERSION" \
  --values "$VALUES" \
  --post-renderer ./deployment/single-pod.sh \
  --set-string "image.repository=$DESTINATION_IMAGE" \
  --set-string "image.tag=$IMAGE_VERSION" \
  --set-string 'imagePullSecrets[0].name=harbor-registry' \
  --set-string "ingress.hosts[0].host=$INGRESS_HOST" \
  --set-string "ingress.tls[0].hosts[0]=$INGRESS_HOST"
helm upgrade "$@"
phase=complete
