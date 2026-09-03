#!/usr/bin/env python3
"""Prepare and receive one bounded Cloudflare-token environment transfer.

The source workflow receives the token only through its protected GitHub
environment and emits only RSA-OAEP ciphertext.  The receiver keeps the
private key and decrypted token off persistent storage, seals the token to
GitHub's target-environment key in-process, and gives child processes only
ciphertext.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import io
import json
import os
import pathlib
import re
import resource
import stat
import subprocess
import sys
import textwrap
import time
import zipfile

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
except ImportError:  # pragma: no cover - exercised only on an operator host.
    hashes = serialization = padding = rsa = None

try:
    from nacl.public import PublicKey, SealedBox
except ImportError:  # pragma: no cover - exercised only on an operator host.
    PublicKey = SealedBox = None


REPOSITORY = "leanprover/lean-eval-submissions"
SOURCE_ENVIRONMENT = "cloudflare-production"
TARGET_ENVIRONMENT = "replay-production"
SECRET_NAME = "CLOUDFLARE_API_TOKEN"
WORKFLOW_PATH = ".github/workflows/replay-cloudflare-token-transfer.yml"
SOURCE_SECRETS = [
    "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_API_TOKEN",
    "LIFECYCLE_CALLBACK_TOKEN",
    "READINESS_TOKEN",
]
TARGET_SECRETS_BEFORE = [
    "AUDIT_READ_KEY",
    "CLOUDFLARE_ACCOUNT_ID",
    "PRODUCTION_STATE_READ_KEY",
    "PRODUCTION_STATE_WRITE_KEY",
]
TARGET_SECRETS_AFTER = sorted([*TARGET_SECRETS_BEFORE, SECRET_NAME])
OPERATION_RE = re.compile(r"replay-token-transfer-[0-9a-f]{32}")
SHA_RE = re.compile(r"[0-9a-f]{40}")
TOKEN_RE = re.compile(rb"[A-Za-z0-9_-]{20,256}")
MAX_ARTIFACT_BYTES = 32_768
PR_GET_DUMPABLE = 3
PR_SET_DUMPABLE = 4


class TransferError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise TransferError(message)


def harden_process_against_dumps() -> None:
    """Disable every Linux core/dump path before sensitive material is read."""
    if sys.platform != "linux":
        fail("credential transfer requires Linux dump protections")
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        core_limit = resource.getrlimit(resource.RLIMIT_CORE)
    except (OSError, ValueError):
        fail("could not disable core dumps")
    if core_limit != (0, 0):
        fail("core-dump limit did not become exactly zero")
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        prctl = libc.prctl
        prctl.argtypes = [
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
        ]
        prctl.restype = ctypes.c_int
        changed = prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)
        dumpable = prctl(PR_GET_DUMPABLE, 0, 0, 0, 0)
    except (AttributeError, OSError):
        fail("could not disable process dumpability")
    if changed != 0 or dumpable != 0:
        fail("process dumpability did not become exactly zero")


def run(
    arguments: list[str],
    *,
    input_bytes: bytes | None = None,
    capture: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            arguments,
            input=input_bytes,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        fail(f"command failed without exposing its input: {arguments[0]}")


def gh_json(endpoint: str) -> object:
    result = run(["gh", "api", endpoint])
    try:
        return json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("GitHub returned a non-JSON response")


def secret_names(environment: str) -> list[str]:
    payload = gh_json(
        f"repos/{REPOSITORY}/environments/{environment}/secrets?per_page=100"
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("secrets"), list):
        fail(f"could not inspect {environment} secret inventory")
    names = [entry.get("name") for entry in payload["secrets"]]
    if not all(isinstance(name, str) for name in names):
        fail(f"invalid {environment} secret inventory")
    return sorted(names)


def require_environment(environment: str, *, reviewer: bool) -> None:
    payload = gh_json(f"repos/{REPOSITORY}/environments/{environment}")
    if not isinstance(payload, dict):
        fail(f"could not inspect {environment}")
    branch = payload.get("deployment_branch_policy")
    if branch != {"protected_branches": True, "custom_branch_policies": False}:
        fail(f"{environment} is not restricted exactly to protected branches")
    rules = payload.get("protection_rules")
    if not isinstance(rules, list):
        fail(f"invalid {environment} protection rules")
    rule_types = sorted(rule.get("type") for rule in rules if isinstance(rule, dict))
    expected = ["branch_policy", "required_reviewers"] if reviewer else ["branch_policy"]
    if rule_types != expected:
        fail(f"{environment} protection rules are not exact")
    if reviewer:
        reviewer_rule = next(
            rule for rule in rules if rule.get("type") == "required_reviewers"
        )
        reviewers = reviewer_rule.get("reviewers")
        if reviewer_rule.get("prevent_self_review") is not False or not isinstance(
            reviewers, list
        ):
            fail(f"{environment} reviewer boundary is not exact")
        identities = sorted(
            (entry.get("type"), entry.get("reviewer", {}).get("login"))
            for entry in reviewers
            if isinstance(entry, dict) and isinstance(entry.get("reviewer"), dict)
        )
        if identities != [("User", "kim-em")]:
            fail(f"{environment} reviewer identity is not exact")


def require_tmpfs_file(path: pathlib.Path, *, private: bool) -> None:
    try:
        info = path.lstat()
    except OSError:
        fail("required tmpfs file is unavailable")
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        fail("required tmpfs input is not one regular non-symlink file")
    if private and stat.S_IMODE(info.st_mode) != 0o600:
        fail("private key mode must be exactly 0600")
    resolved = path.resolve()
    if resolved.parent != pathlib.Path("/dev/shm"):
        fail("sensitive material must be a direct child of /dev/shm")


def require_cryptography() -> None:
    if any(module is None for module in (hashes, serialization, padding, rsa)):
        fail("the Python cryptography package is required on the operator host")


def require_pynacl() -> None:
    if PublicKey is None or SealedBox is None:
        fail("the Python PyNaCl package is required on the operator host")


def target_environment_public_key() -> tuple[str, bytes]:
    require_pynacl()
    payload = gh_json(
        f"repos/{REPOSITORY}/environments/{TARGET_ENVIRONMENT}/secrets/public-key"
    )
    if not isinstance(payload, dict):
        fail("GitHub target public-key response is not one object")
    key_id = payload.get("key_id")
    encoded_key = payload.get("key")
    if not isinstance(key_id, str) or not key_id or len(key_id) > 256:
        fail("GitHub target public-key response has no bounded key ID")
    if not isinstance(encoded_key, str):
        fail("GitHub target public-key response has no encoded key")
    try:
        public_key = base64.b64decode(encoded_key, validate=True)
    except (ValueError, TypeError):
        fail("GitHub target public key is not canonical base64")
    if len(public_key) != 32:
        fail("GitHub target public key is not one Curve25519 key")
    return key_id, public_key


def seal_target_secret_request(
    plaintext: bytearray, key_id: str, public_key: bytes
) -> bytes:
    require_pynacl()
    try:
        # PyNaCl 1.6 requires an immutable bytes input, even though bytearray
        # implements the buffer protocol.
        sealed = SealedBox(PublicKey(public_key)).encrypt(bytes(plaintext))
    finally:
        plaintext[:] = b"\0" * len(plaintext)
    return json.dumps(
        {
            "encrypted_value": base64.b64encode(sealed).decode("ascii"),
            "key_id": key_id,
        },
        separators=(",", ":"),
    ).encode("ascii")


def public_der(private_key: pathlib.Path) -> bytes:
    require_cryptography()
    try:
        key = serialization.load_pem_private_key(private_key.read_bytes(), password=None)
    except (OSError, TypeError, ValueError):
        fail("one-time private key is not a valid unencrypted PEM key")
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size != 4096:
        fail("one-time private key is not RSA-4096")
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def render_workflow(operation: str, recipient_der: bytes) -> str:
    if OPERATION_RE.fullmatch(operation) is None:
        fail("operation ID is not canonical")
    recipient = base64.b64encode(recipient_der).decode("ascii")
    recipient_sha256 = hashlib.sha256(recipient_der).hexdigest()
    artifact = operation
    expression_open = "${{"
    expression_close = "}}"
    return textwrap.dedent(
        f"""\
        name: One-shot replay Cloudflare token transfer

        on:
          workflow_dispatch:
            inputs:
              mode:
                description: Export the source token or verify its target installation
                required: true
                type: choice
                options:
                  - export
                  - verify
              confirm:
                description: Run the one-shot encrypted token transfer
                required: true
                type: boolean
                default: false

        permissions:
          contents: read

        concurrency:
          group: {operation}
          cancel-in-progress: false

        jobs:
          export-encrypted-token:
            if: inputs.confirm == true && inputs.mode == 'export'
            environment: {SOURCE_ENVIRONMENT}
            runs-on: ubuntu-24.04
            timeout-minutes: 10
            steps:
              - name: Require the exact protected-main boundary
                env:
                  GH_TOKEN: {expression_open} github.token {expression_close}
                run: |
                  set -euo pipefail
                  test "$GITHUB_REPOSITORY" = {REPOSITORY}
                  test "$GITHUB_REF" = refs/heads/main
                  test "$GITHUB_REF_PROTECTED" = true
                  branch=$(timeout 30s gh api "repos/$GITHUB_REPOSITORY/branches/main" \\
                    --jq '[.commit.sha, .protected] | @tsv')
                  test "$branch" = "$GITHUB_SHA"$'\\ttrue'

              - name: Encrypt the token for the one-time receiver
                env:
                  SOURCE_TOKEN: {expression_open} secrets.{SECRET_NAME} {expression_close}
                  RECIPIENT_DER_BASE64: {recipient}
                  RECIPIENT_SHA256: {recipient_sha256}
                run: |
                  set +x
                  set -euo pipefail
                  umask 077
                  public_key="$RUNNER_TEMP/recipient.der"
                  ciphertext="$RUNNER_TEMP/ciphertext.bin"
                  printf '%s' "$RECIPIENT_DER_BASE64" | base64 --decode > "$public_key"
                  test "$(sha256sum "$public_key" | cut -d' ' -f1)" = "$RECIPIENT_SHA256"
                  openssl pkey -pubin -inform DER -in "$public_key" -noout
                  [[ "$SOURCE_TOKEN" =~ ^[A-Za-z0-9_-]{{20,256}}$ ]]
                  printf '%s' "$SOURCE_TOKEN" | openssl pkeyutl -encrypt \\
                    -pubin -keyform DER -inkey "$public_key" \\
                    -pkeyopt rsa_padding_mode:oaep \\
                    -pkeyopt rsa_oaep_md:sha256 \\
                    -out "$ciphertext"
                  unset SOURCE_TOKEN
                  test -s "$ciphertext"
                  test "$(stat --format='%s' "$ciphertext")" -le 1024
                  jq -cn \\
                    --arg operation '{operation}' \\
                    --arg repository "$GITHUB_REPOSITORY" \\
                    --arg workflow_sha "$GITHUB_SHA" \\
                    --arg recipient_sha256 "$RECIPIENT_SHA256" \\
                    --arg ciphertext_sha256 "$(sha256sum "$ciphertext" | cut -d' ' -f1)" \\
                    '{{schema_version: 1, operation: $operation, repository: $repository,
                      workflow_sha: $workflow_sha, recipient_sha256: $recipient_sha256,
                      ciphertext_sha256: $ciphertext_sha256}}' \\
                    > "$RUNNER_TEMP/manifest.json"

              # actions/upload-artifact pinned to v7.0.1.
              - uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a
                with:
                  name: {artifact}
                  path: |
                    {expression_open} runner.temp {expression_close}/ciphertext.bin
                    {expression_open} runner.temp {expression_close}/manifest.json
                  if-no-files-found: error
                  retention-days: 1

          verify-target-token:
            if: inputs.confirm == true && inputs.mode == 'verify'
            environment: {TARGET_ENVIRONMENT}
            runs-on: ubuntu-24.04
            timeout-minutes: 10
            steps:
              - name: Require the exact protected-main boundary
                env:
                  GH_TOKEN: {expression_open} github.token {expression_close}
                run: |
                  set -euo pipefail
                  test "$GITHUB_REPOSITORY" = {REPOSITORY}
                  test "$GITHUB_REF" = refs/heads/main
                  test "$GITHUB_REF_PROTECTED" = true
                  branch=$(timeout 30s gh api "repos/$GITHUB_REPOSITORY/branches/main" \\
                    --jq '[.commit.sha, .protected] | @tsv')
                  test "$branch" = "$GITHUB_SHA"$'\\ttrue'

              - name: Verify the installed target token is active
                env:
                  CLOUDFLARE_ACCOUNT_ID: {expression_open} secrets.CLOUDFLARE_ACCOUNT_ID {expression_close}
                  TARGET_TOKEN: {expression_open} secrets.{SECRET_NAME} {expression_close}
                run: |
                  set +x
                  set -euo pipefail
                  python - <<'PY'
                  import json
                  import os
                  import re
                  import urllib.request

                  account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
                  token = os.environ.get("TARGET_TOKEN", "")
                  if re.fullmatch(r"[0-9a-f]{{32}}", account) is None:
                      raise SystemExit("target Cloudflare account binding is unavailable")
                  if re.fullmatch(r"[A-Za-z0-9_-]{{20,256}}", token) is None:
                      raise SystemExit("target Cloudflare token is unavailable")
                  request = urllib.request.Request(
                      f"https://api.cloudflare.com/client/v4/accounts/{{account}}/tokens/verify",
                      headers={{"Authorization": "Bearer " + token}},
                  )
                  try:
                      with urllib.request.urlopen(request, timeout=30) as response:
                          payload = json.load(response)
                  except Exception:
                      raise SystemExit("target Cloudflare token verification failed") from None
                  result = payload.get("result") if isinstance(payload, dict) else None
                  if (
                      not isinstance(payload, dict)
                      or payload.get("success") is not True
                      or not isinstance(result, dict)
                      or result.get("status") != "active"
                  ):
                      raise SystemExit("target Cloudflare token is not active")
                  print("REPLAY_CLOUDFLARE_TOKEN_VERIFIED")
                  PY
        """
    )


def prepare(arguments: argparse.Namespace) -> None:
    private_key = arguments.private_key.resolve()
    workflow = arguments.workflow.resolve()
    if private_key.parent != pathlib.Path("/dev/shm"):
        fail("the one-time private key must be a direct child of /dev/shm")
    if private_key.exists():
        fail("refusing to replace an existing private key")
    if workflow.exists():
        fail("refusing to replace an existing transfer workflow")
    private_key.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    require_cryptography()
    key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    private_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    descriptor = os.open(private_key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(private_bytes)
    private_bytes = b""
    del key
    require_tmpfs_file(private_key, private=True)
    der = public_der(private_key)
    if len(der) < 500 or len(der) > 600:
        fail("generated recipient key is not the expected RSA-4096 shape")
    workflow.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(workflow, flags, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(render_workflow(arguments.operation, der))
    print(f"PREPARED_OPERATION={arguments.operation}")
    print(f"PRIVATE_KEY={private_key}")


def require_run(run_id: int, expected_head: str) -> None:
    payload = gh_json(f"repos/{REPOSITORY}/actions/runs/{run_id}")
    if not isinstance(payload, dict):
        fail("could not inspect source run")
    expected = {
        "event": "workflow_dispatch",
        "head_branch": "main",
        "head_sha": expected_head,
        "status": "completed",
        "conclusion": "success",
        "path": WORKFLOW_PATH,
    }
    actual = {key: payload.get(key) for key in expected}
    if actual != expected:
        fail("source run is not the exact successful protected-main transfer run")
    repository = payload.get("repository")
    if not isinstance(repository, dict) or repository.get("full_name") != REPOSITORY:
        fail("source run repository mismatch")
    jobs = gh_json(f"repos/{REPOSITORY}/actions/runs/{run_id}/jobs?per_page=100")
    if not isinstance(jobs, dict) or not isinstance(jobs.get("jobs"), list):
        fail("could not inspect source transfer jobs")
    conclusions = {
        job.get("name"): job.get("conclusion")
        for job in jobs["jobs"]
        if isinstance(job, dict)
    }
    if conclusions != {
        "export-encrypted-token": "success",
        "verify-target-token": "skipped",
    }:
        fail("source transfer job conclusions are not exact")


def download_artifact(
    run_id: int, operation: str
) -> tuple[int, bytes, dict[str, object]]:
    payload = gh_json(f"repos/{REPOSITORY}/actions/runs/{run_id}/artifacts?per_page=100")
    if not isinstance(payload, dict) or not isinstance(payload.get("artifacts"), list):
        fail("could not inspect source artifacts")
    matches = [item for item in payload["artifacts"] if item.get("name") == operation]
    if len(matches) != 1 or matches[0].get("expired") is not False:
        fail("exactly one live transfer artifact is required")
    artifact_id = matches[0].get("id")
    if not isinstance(artifact_id, int):
        fail("transfer artifact has no canonical ID")
    archive = run(
        ["gh", "api", f"repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip"]
    ).stdout
    if len(archive) > MAX_ARTIFACT_BYTES:
        fail("transfer artifact exceeds its strict size limit")
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            if sorted(zipped.namelist()) != ["ciphertext.bin", "manifest.json"]:
                fail("transfer artifact file inventory is not exact")
            for info in zipped.infolist():
                if info.is_dir() or info.file_size > 8_192:
                    fail("transfer artifact member is not bounded")
                mode = (info.external_attr >> 16) & 0o170000
                if mode not in (0, stat.S_IFREG):
                    fail("transfer artifact member is not regular")
            ciphertext = zipped.read("ciphertext.bin")
            manifest_bytes = zipped.read("manifest.json")
    except (zipfile.BadZipFile, KeyError, OSError):
        fail("transfer artifact is not a valid bounded ZIP")
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("transfer manifest is not valid JSON")
    if not isinstance(manifest, dict):
        fail("transfer manifest is not one object")
    return artifact_id, ciphertext, manifest


def dispatch_target_verification(expected_head: str) -> int:
    branch = gh_json(f"repos/{REPOSITORY}/branches/main")
    if not isinstance(branch, dict) or branch.get("protected") is not True:
        fail("could not verify protected main before target verification")
    commit = branch.get("commit")
    if not isinstance(commit, dict) or commit.get("sha") != expected_head:
        fail("protected main moved before target verification")
    payload = json.dumps(
        {
            "ref": "main",
            "inputs": {"mode": "verify", "confirm": True},
            "return_run_details": True,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    response = run(
        [
            "gh",
            "api",
            "--method",
            "POST",
            "-H",
            "X-GitHub-Api-Version: 2026-03-10",
            f"repos/{REPOSITORY}/actions/workflows/{pathlib.Path(WORKFLOW_PATH).name}/dispatches",
            "--input",
            "-",
        ],
        input_bytes=payload,
    ).stdout
    try:
        details = json.loads(response)
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("target-verification dispatch did not return run details")
    run_id = details.get("workflow_run_id") if isinstance(details, dict) else None
    if not isinstance(run_id, int):
        fail("target-verification dispatch returned no exact run ID")
    return run_id


def wait_for_target_verification(run_id: int, expected_head: str) -> None:
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        payload = gh_json(f"repos/{REPOSITORY}/actions/runs/{run_id}")
        if not isinstance(payload, dict):
            fail("could not inspect target-verification run")
        identity = {
            "event": payload.get("event"),
            "head_branch": payload.get("head_branch"),
            "head_sha": payload.get("head_sha"),
            "path": payload.get("path"),
        }
        expected = {
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": expected_head,
            "path": WORKFLOW_PATH,
        }
        if identity != expected:
            fail("target-verification run identity mismatch")
        status = payload.get("status")
        if status == "completed":
            if payload.get("conclusion") != "success":
                fail("target-verification run did not succeed")
            jobs = gh_json(f"repos/{REPOSITORY}/actions/runs/{run_id}/jobs?per_page=100")
            if not isinstance(jobs, dict) or not isinstance(jobs.get("jobs"), list):
                fail("could not inspect target-verification jobs")
            conclusions = {
                job.get("name"): job.get("conclusion")
                for job in jobs["jobs"]
                if isinstance(job, dict)
            }
            if conclusions != {
                "export-encrypted-token": "skipped",
                "verify-target-token": "success",
            }:
                fail("target-verification job conclusions are not exact")
            return
        if status not in ("queued", "in_progress", "pending", "requested", "waiting"):
            fail("target-verification run entered an unexpected state")
        time.sleep(5)
    fail("target-verification run did not complete within ten minutes")


def delete_artifact(artifact_id: int) -> None:
    run(
        [
            "gh",
            "api",
            "--method",
            "DELETE",
            f"repos/{REPOSITORY}/actions/artifacts/{artifact_id}",
        ],
        capture=False,
    )


def receive(arguments: argparse.Namespace) -> None:
    if OPERATION_RE.fullmatch(arguments.operation) is None:
        fail("operation ID is not canonical")
    if SHA_RE.fullmatch(arguments.expected_head) is None:
        fail("expected workflow head is not a full SHA")
    private_key = arguments.private_key.resolve()
    require_tmpfs_file(private_key, private=True)
    require_environment(SOURCE_ENVIRONMENT, reviewer=True)
    require_environment(TARGET_ENVIRONMENT, reviewer=False)
    if secret_names(SOURCE_ENVIRONMENT) != SOURCE_SECRETS:
        fail("source environment secret inventory is not exact")
    if secret_names(TARGET_ENVIRONMENT) != TARGET_SECRETS_BEFORE:
        fail("target environment is not ready; refusing to replace any secret")
    branch = gh_json(f"repos/{REPOSITORY}/branches/main")
    if not isinstance(branch, dict):
        fail("could not inspect protected main")
    commit = branch.get("commit")
    if (
        not isinstance(commit, dict)
        or commit.get("sha") != arguments.expected_head
        or branch.get("protected") is not True
    ):
        fail("protected main moved after the reviewed transfer workflow")
    require_run(arguments.run_id, arguments.expected_head)
    artifact_id, ciphertext, manifest = download_artifact(
        arguments.run_id, arguments.operation
    )
    target_key_id, target_public_key = target_environment_public_key()
    recipient_sha256 = hashlib.sha256(public_der(private_key)).hexdigest()
    expected_manifest = {
        "schema_version": 1,
        "operation": arguments.operation,
        "repository": REPOSITORY,
        "workflow_sha": arguments.expected_head,
        "recipient_sha256": recipient_sha256,
        "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
    }
    if manifest != expected_manifest:
        fail("transfer manifest does not bind the exact run, key, and ciphertext")
    require_cryptography()
    try:
        key = serialization.load_pem_private_key(private_key.read_bytes(), password=None)
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size != 4096:
            fail("one-time private key is not RSA-4096")
        plaintext = bytearray(
            key.decrypt(
                ciphertext,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None,
                ),
            )
        )
        del key
    except (OSError, TypeError, ValueError):
        fail("ciphertext did not decrypt with the one-time key")
    if TOKEN_RE.fullmatch(plaintext) is None:
        plaintext[:] = b"\0" * len(plaintext)
        fail("decrypted token does not have the strict expected shape")
    request = seal_target_secret_request(plaintext, target_key_id, target_public_key)
    del plaintext
    try:
        run(
            [
                "gh",
                "api",
                "--method",
                "PUT",
                f"repos/{REPOSITORY}/environments/{TARGET_ENVIRONMENT}/secrets/{SECRET_NAME}",
                "--input",
                "-",
            ],
            input_bytes=request,
            capture=False,
        )
        request = b""
        if secret_names(TARGET_ENVIRONMENT) != TARGET_SECRETS_AFTER:
            fail("target secret installation did not verify exactly")
    except TransferError:
        request = b""
        fail("TARGET_INSTALLATION_STATE_INDETERMINATE; inspect before retrying")
    finally:
        request = b""
    try:
        private_key.unlink()
    except OSError:
        fail("transfer succeeded but one-time private-key destruction failed")
    if private_key.exists():
        fail("transfer succeeded but the one-time private key remains")
    delete_artifact(artifact_id)
    verification_run_id = dispatch_target_verification(arguments.expected_head)
    wait_for_target_verification(verification_run_id, arguments.expected_head)
    print("REPLAY_CLOUDFLARE_TOKEN_INSTALLED")
    print(f"TARGET_VERIFICATION_RUN={verification_run_id}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    subparsers = result.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--operation", required=True)
    prepare_parser.add_argument("--private-key", required=True, type=pathlib.Path)
    prepare_parser.add_argument(
        "--workflow", type=pathlib.Path, default=pathlib.Path(WORKFLOW_PATH)
    )
    prepare_parser.set_defaults(function=prepare)
    receive_parser = subparsers.add_parser("receive")
    receive_parser.add_argument("--operation", required=True)
    receive_parser.add_argument("--private-key", required=True, type=pathlib.Path)
    receive_parser.add_argument("--run-id", required=True, type=int)
    receive_parser.add_argument("--expected-head", required=True)
    receive_parser.set_defaults(function=receive)
    return result


def main() -> None:
    try:
        harden_process_against_dumps()
        arguments = parser().parse_args()
        arguments.function(arguments)
    except TransferError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
