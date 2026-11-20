export const PUBLIC_RUNTIME_MISMATCH_EXIT = 42;
export const PUBLIC_RUNTIME_GUARD_EXIT = 43;
export const PUBLIC_RUNTIME_MISMATCH_STDERR = "historical-public-runtime: runtime_profile_mismatch\n";
export const PUBLIC_RUNTIME_GUARD_STDERR = "historical-public-runtime: runtime_profile_guard_failed\n";
export const PUBLIC_RUNTIME_PROFILE_PATH = "/workspace/historical-public-runtime-profile.json";

// Constant trusted Python only. No request or source text enters this command.
// -I excludes cwd/PYTHONPATH/user site, -S .pth processing, -B bytecode writes.
export const PUBLIC_RUNTIME_GUARD = `import importlib.machinery, importlib.util, json, pathlib, sys
try:
 p = pathlib.Path("/workspace/historical-public-runtime-profile.json")
 with p.open("rb") as f:
  raw = f.read(16385)
 if not raw or len(raw) > 16384:
  raise ValueError()
 profile = json.loads(raw)
 if not isinstance(profile, dict) or any(not isinstance(profile.get(k), str) or not profile[k] for k in ("architecture", "kernel_release", "cpu_model")) or profile["cpu_model"] == "unavailable":
  raise ValueError()
 loader = importlib.machinery.SourceFileLoader("lean_eval_runtime_guard", "/opt/lean-eval/replay-authoritative")
 spec = importlib.util.spec_from_loader(loader.name, loader)
 runtime = importlib.util.module_from_spec(spec)
 loader.exec_module(runtime)
 actual = runtime.platform_evidence()
except BaseException:
 sys.stderr.write("historical-public-runtime: runtime_profile_guard_failed\\n")
 sys.exit(43)
try:
 runtime.validate_runtime_profile({"execution_profile": profile}, *actual)
except runtime.AuthoritativeReplayError:
 sys.stderr.write("historical-public-runtime: runtime_profile_mismatch\\n")
 sys.exit(42)
except BaseException:
 sys.stderr.write("historical-public-runtime: runtime_profile_guard_failed\\n")
 sys.exit(43)
`;

export const HISTORICAL_PUBLIC_COMMAND =
  `/usr/bin/python3 -I -S -B -c '${PUBLIC_RUNTIME_GUARD}' `
  + "&& base64 --decode /workspace/historical-public-source.tar.gz.b64 "
  + "> /workspace/historical-public-source.tar.gz "
  + "&& rm /workspace/historical-public-source.tar.gz.b64 "
  + "&& /opt/lean-eval/historical-public-runner";
