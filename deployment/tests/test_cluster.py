"""Exercise deployment orchestration without contacting a cluster or registry."""

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
KUBECONFIG = b"apiVersion: v1\nkind: Config\nclusters: []\n"


class ClusterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        shutil.copytree(ROOT / ".ci", self.work / ".ci")
        shutil.copytree(ROOT / "deployment", self.work / "deployment")
        shutil.copy(ROOT / "deploy.sh", self.work)
        self.bin = self.work / "bin"
        self.bin.mkdir()
        # Record argv and credential-file modes, but never credential contents.
        stub = self.bin / "stub"
        stub.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            "kube = pathlib.Path(os.environ['KUBECONFIG'])\n"
            "record = {'tool': pathlib.Path(sys.argv[0]).name, 'args': args,\n"
            "          'kubeconfig': str(kube), 'mode': kube.stat().st_mode & 0o777}\n"
            "with open(os.environ['CALLS'], 'a') as out:\n"
            "    out.write(json.dumps(record) + '\\n')\n"
            "if args[:2] == ['registry', 'login']:\n"
            "    sys.stdin.read()\n"
            "if os.environ.get('FAIL_UPGRADE') and args[:1] == ['upgrade']:\n"
            "    sys.exit(19)\n"
            "if 'docker-registry' in args:\n"
            "    print('apiVersion: v1\\nkind: Secret\\nmetadata:\\n  name: harbor-registry')\n"
        )
        stub.chmod(0o755)
        for name in ("helm", "kubectl"):
            (self.bin / name).symlink_to(stub)
        self.env = {
            **os.environ,
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "CALLS": str(self.work / "calls.jsonl"),
            "HARBOR_REGISTRY": "registry.example.invalid",
            "HARBOR_USER": "test-user",
            "HARBOR_PASS": "never-print-this-password",
            "TARGET_KUBECONFIG": base64.b64encode(KUBECONFIG).decode(),
            "RELEASE": "vectorcraft",
            "NAMESPACE": "vectorcraft",
            "VALUES": "deployment/values.yaml",
            "INGRESS_HOST": "vectorcraft.example.invalid",
            "DESTINATION_IMAGE": "registry.example.invalid/triton/vectorcraft",
            "IMAGE_VERSION": "test-commit-sha",
            "CREATE_NAMESPACE": "no",
        }

    def run_deploy(self, *args, expected=0):
        result = subprocess.run(
            ["sh", "deploy.sh", *args], cwd=self.work, env=self.env,
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertNotIn(self.env["HARBOR_PASS"], result.stdout + result.stderr)
        self.assertNotIn(self.env["TARGET_KUBECONFIG"], result.stdout + result.stderr)
        return result

    def calls(self):
        path = self.work / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_deploy_uses_commit_image_production_host_and_single_pod_renderer(self):
        self.run_deploy("--kubernetes", "deploy")
        calls = self.calls()
        upgrade, = [c for c in calls if c["tool"] == "helm" and c["args"][0] == "upgrade"]
        args = upgrade["args"]
        for value in (
            "oci://registry.example.invalid/ops/generic-microservice", "0.1.1",
            "image.repository=" + self.env["DESTINATION_IMAGE"], "image.tag=test-commit-sha",
            "ingress.hosts[0].host=vectorcraft.example.invalid",
            "ingress.tls[0].hosts[0]=vectorcraft.example.invalid",
        ):
            self.assertIn(value, args)
        self.assertEqual(args[args.index("--namespace") + 1], "vectorcraft")
        self.assertEqual(args[args.index("--post-renderer") + 1], "./deployment/single-pod.sh")
        self.assertFalse(any("namespace" in c["args"] for c in calls))
        for call in calls:
            self.assertEqual(call["mode"], 0o600)
            self.assertFalse(Path(call["kubeconfig"]).exists(), "temporary credentials survived")

    def test_file_variable_is_copied_and_caller_kubeconfig_is_preserved(self):
        original = self.work / "caller-config"
        original.write_bytes(KUBECONFIG)
        original.chmod(0o640)
        self.env["TARGET_KUBECONFIG"] = str(original)
        self.env["KUBECONFIG"] = str(original)
        self.run_deploy("--kubernetes")
        self.assertEqual(original.read_bytes(), KUBECONFIG)
        self.assertEqual(original.stat().st_mode & 0o777, 0o640)
        self.assertTrue(all(c["kubeconfig"] != str(original) for c in self.calls()))

    def test_wrapped_base64_kubeconfig_is_accepted(self):
        self.env["TARGET_KUBECONFIG"] = base64.encodebytes(KUBECONFIG).decode()
        self.run_deploy("--kubernetes", "deploy")
        self.assertTrue(any(c["args"][:1] == ["upgrade"] for c in self.calls()))

    def test_undeploy_only_uninstalls_release(self):
        self.run_deploy("--kubernetes", "undeploy")
        helm = [c["args"] for c in self.calls() if c["tool"] == "helm"]
        self.assertEqual(len(helm), 1)
        self.assertEqual(helm[0][:2], ["uninstall", "vectorcraft"])
        self.assertFalse(any(c["args"][:2] == ["create", "secret"] for c in self.calls()))

    def test_failure_preserves_exit_status_collects_diagnostics_and_removes_credentials(self):
        self.env["FAIL_UPGRADE"] = "yes"
        self.run_deploy("--kubernetes", "deploy", expected=19)
        diagnostics = (self.work / "deployment-diagnostics.txt").read_text()
        self.assertIn("phase: helm-upgrade", diagnostics)
        self.assertNotIn(self.env["HARBOR_PASS"], diagnostics)
        self.assertNotIn(self.env["TARGET_KUBECONFIG"], diagnostics)
        for call in self.calls():
            self.assertFalse(Path(call["kubeconfig"]).exists())
            self.assertNotIn("logs", call["args"])

    def test_missing_credentials_fail_before_cluster_access(self):
        self.env.pop("HARBOR_PASS")
        result = subprocess.run(
            ["sh", "deploy.sh", "--kubernetes"], cwd=self.work, env=self.env,
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("HARBOR_PASS", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_invalid_action_fails_before_cluster_access(self):
        self.run_deploy("--kubernetes", "unknown", expected=2)
        self.assertEqual(self.calls(), [])

    def test_invalid_kubeconfig_encoding_never_contacts_cluster(self):
        self.env["TARGET_KUBECONFIG"] = "not-base64!"
        self.run_deploy("--kubernetes", "deploy", expected=1)
        self.assertEqual(self.calls(), [])
        diagnostics = (self.work / "deployment-diagnostics.txt").read_text()
        self.assertIn("Kubeconfig preparation failed", diagnostics)

    def test_missing_values_fail_before_registry_mutation(self):
        self.env["VALUES"] = "missing.yaml"
        self.run_deploy("--kubernetes", "deploy", expected=1)
        self.assertFalse(any(c["args"][:2] == ["create", "secret"] for c in self.calls()))


@unittest.skipUnless(shutil.which("kubectl"), "kubectl is needed for the offline render check")
class SinglePodTests(unittest.TestCase):
    def test_renderer_replaces_rolling_strategy_and_preserves_service(self):
        manifests = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: vectorcraft
spec:
  replicas: 2
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  selector:
    matchLabels: {app: vectorcraft}
  template:
    metadata:
      labels: {app: vectorcraft}
    spec:
      containers:
        - name: web
          image: example.invalid/vectorcraft:test
---
apiVersion: v1
kind: Service
metadata:
  name: vectorcraft
spec:
  ports:
    - port: 80
      targetPort: 8080
"""
        result = subprocess.run(
            ["sh", str(ROOT / "deployment/single-pod.sh")], input=manifests,
            capture_output=True, text=True, check=True,
        )
        self.assertIn("replicas: 1", result.stdout)
        self.assertIn("type: Recreate", result.stdout)
        self.assertNotIn("RollingUpdate", result.stdout)
        self.assertNotIn("maxSurge", result.stdout)
        self.assertIn("kind: Service", result.stdout)
        self.assertIn("targetPort: 8080", result.stdout)


if __name__ == "__main__":
    unittest.main()
