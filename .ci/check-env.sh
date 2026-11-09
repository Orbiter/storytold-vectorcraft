#!/bin/sh
# Report presence only; registry credentials and kubeconfig must never reach logs.
set +x
set -eu
case "${1:-}" in
    build)
        required='CI_SNIPPETS_REF RGCPDEPLOYER RGCPDEPLOYERVERSION RGAG_CA_BASE64 RGAG_CA RG23_CA HARBOR_REGISTRY HARBOR_USER HARBOR_PASS DESTINATION_IMAGE IMAGE_VERSION DOCKER_FILE KANIKO_CONTEXT'
        ;;
    cluster)
        required='HARBOR_REGISTRY HARBOR_USER HARBOR_PASS TARGET_KUBECONFIG RELEASE NAMESPACE'
        ;;
    *) echo 'Usage: sh .ci/check-env.sh build|cluster' >&2; exit 2 ;;
esac
missing=0
for name in $required; do
    if [ -z "$(printenv "$name" 2>/dev/null || true)" ]; then
        printf 'ERROR: CI variable %s is missing or empty.\n' "$name" >&2
        missing=1
    else
        printf 'OK: %s is set.\n' "$name"
    fi
done
if [ "$missing" -ne 0 ]; then
    echo 'Check inherited variables, environment scopes and protected branch settings.' >&2
    echo 'TARGET_KUBECONFIG defaults to KUBECONFIG_rad_app_01.' >&2
    exit 1
fi
