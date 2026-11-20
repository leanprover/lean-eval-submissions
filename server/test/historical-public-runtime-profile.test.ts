import { describe, expect, it } from "vitest";
import p0 from "../../evidence/public-replay/profiles/022ebbec5b4c603b012e6cc6e0d807cc56a66cbcbe9a642a4a2fe433a74ad076.json";
import p1 from "../../evidence/public-replay/profiles/0aa9fc75bf149ad09bd3e955f98200afe0817d5ba406e4585dedc1dd150b1e15.json";
import p2 from "../../evidence/public-replay/profiles/0b69e06906ec75542f987818ed311ab2b2c84db3bd94afcf55fe23ee3574d2cf.json";
import p3 from "../../evidence/public-replay/profiles/0c1acf5590b32e3027ad730a6724031110185bf2c2b6253b1d4db11b88cdfe46.json";
import p4 from "../../evidence/public-replay/profiles/133e307550d669fe50956c19e40d73f4ce2e3ed611b5b269edb050d6dfcf48e0.json";
import p5 from "../../evidence/public-replay/profiles/18d1d4ceaf56fc38d9feaba012c8e6cee23f74e8846b731266194d69583a87b2.json";
import p6 from "../../evidence/public-replay/profiles/22412e07a1a44e9b50dd93fc471cd4a1c4806dfb48ad7550b0d0f54d0efd367b.json";
import p7 from "../../evidence/public-replay/profiles/23ab47322ebc9f4cbf594f55071a241cf02bb2a617015a2c31fd115591ac9a6c.json";
import p8 from "../../evidence/public-replay/profiles/2f4a9e2aa849df17d1f1358be5ca37031da5b031a5acff9c821267fe83187788.json";
import p9 from "../../evidence/public-replay/profiles/33c220cf3dda6f573cbf1d7eeb1581dddd3ffe953c93c78bae36e68e855d11df.json";
import p10 from "../../evidence/public-replay/profiles/36a466439bb38fcabf3bbcdbf0a214cc4ee62f85130cc2bd1b8e7737eb7c8306.json";
import p11 from "../../evidence/public-replay/profiles/3e269751965844a476492e8e6ead665f10dae44569020d87578b13d691a386a8.json";
import p12 from "../../evidence/public-replay/profiles/45f0aa5c2f3a8135ea3bd61b5337f7abe7d2fe5ceaae7504822ac71aa4d14884.json";
import p13 from "../../evidence/public-replay/profiles/51c052d354ad46c5b77b7282e80c48a95d765f606c9395bd585a0028407c9823.json";
import p14 from "../../evidence/public-replay/profiles/5992fdf96e0942189b25a7ef2c32d0f6d6f2abcf834672b1fdef6777d8c8da0c.json";
import p15 from "../../evidence/public-replay/profiles/5f7a0292670762e0e5d0f22d4b678f86d51dd0ea6007e56877d8ce06d022f7fd.json";
import p16 from "../../evidence/public-replay/profiles/69cde42f0ef5a9bce5c0160d63d6c3b7447f29e571e548dcff255b357ecb080d.json";
import p17 from "../../evidence/public-replay/profiles/7556b12702b66864bacf6c1745a99834ad620e8342035e031105001d8c9b45e6.json";
import p18 from "../../evidence/public-replay/profiles/7893ced886dc2234ca4221d0855c341f2b68703dd3b756fc0b806ca7deb75f7a.json";
import p19 from "../../evidence/public-replay/profiles/7d4027e5022973640bf768facd4ac56762cc327c542c560b5cc34aa59fcd771e.json";
import p20 from "../../evidence/public-replay/profiles/7ef4ac297f4c346cef9fc4e11e64aca77bba20fa7bd4bb31469788634b7729cb.json";
import p21 from "../../evidence/public-replay/profiles/9e41d7b0a5e77d488fd023b7b3ffc2caedf72b0e0e3aea0dcb0c91da5c4bbfe5.json";
import p22 from "../../evidence/public-replay/profiles/aa77825080d98f85cce959cb48ba0bd4f2f847391a81ae3dd58c82f1e047c7f9.json";
import p23 from "../../evidence/public-replay/profiles/aed34098eb2b91bf7b63aad281a1d4ff4116744696f9885abebe0e2356bfd6f5.json";
import p24 from "../../evidence/public-replay/profiles/bb1b5e9ed2839a6ba56d2b0615be6bd8dec2d1d2546e900451b8e0d08efcc0c4.json";
import p25 from "../../evidence/public-replay/profiles/bdf83d6f0e73223fac9465557ea58122c3beb450d30a60046056011d4e014433.json";
import p26 from "../../evidence/public-replay/profiles/c0bbb84c46c4ae344fdab57e51694e9797d74d64933aa61f71db4e59c206cc42.json";
import p27 from "../../evidence/public-replay/profiles/c4eab62ea7a1933fa76b2a805a8be81636aeb7bb0a896d067352e5de53f8c326.json";
import p28 from "../../evidence/public-replay/profiles/c694c25a5e94791fa8ba4f67897ba4f19df21a8cfcf8a57654e45765988e48ce.json";
import p29 from "../../evidence/public-replay/profiles/da13bd0359a1096e546ebac746c54a7aa4e2c03a636566e3bb97e0f8a7edfb7b.json";
import p30 from "../../evidence/public-replay/profiles/db6cdc6005d534706e540018111f3fa8f3288b69be31248a76dbc8975a5951a3.json";
import p31 from "../../evidence/public-replay/profiles/dd37e9df6ff89dde2225b5fe62a833f6772c15b7ff244dcd373721bdc126bd71.json";
import p32 from "../../evidence/public-replay/profiles/e93d688a82cdcf5cca47fe82741a7d399ab47091fd09c362c5a82875bd6bb8da.json";
import p33 from "../../evidence/public-replay/profiles/ee256257689eae705cf62afcf8f43fb4e227c8e4be49cf4688a8e01c1f15d89b.json";
import p34 from "../../evidence/public-replay/profiles/f0d6806c563f1e837d85d152639bb783e971468fddfd9b1452ec0112379816c3.json";
const profiles = [p0, p1, p2, p3, p4, p5, p6, p7, p8, p9, p10, p11, p12, p13, p14, p15, p16, p17, p18, p19, p20, p21, p22, p23, p24, p25, p26, p27, p28, p29, p30, p31, p32, p33, p34].map((p) => ({ digest: p.execution_profile_digest, profile: p.execution_profile }));
import { canonicalReviewedPublicProfile } from "../src/historical-public-runtime-profile";
import { HISTORICAL_PUBLIC_COMMAND, PUBLIC_RUNTIME_GUARD } from "../src/historical-public-command";

describe("closed historical execution profile", () => {
  it("matches all 35 committed Python profile digests and writes exactly the hashed bytes", async () => {
    expect(profiles).toHaveLength(35);
    for (const { digest, profile } of profiles) {
      const canonical = await canonicalReviewedPublicProfile(profile, digest, profile.vm_image_digest);
      const preimage = new TextEncoder().encode("lean-eval-replay-execution-profile-v1\0" + canonical);
      const bytes = new Uint8Array(await crypto.subtle.digest("SHA-256", preimage));
      expect([...bytes].map((n) => n.toString(16).padStart(2, "0")).join("")).toBe(digest);
      expect(JSON.parse(canonical)).toEqual(profile);
    }
  });

  it("rejects tampering, extra fields, missing runtime, invalid Unicode, unavailable CPU and numeric substitutions", async () => {
    const original = profiles[0];
    if (original === undefined) throw new Error("missing committed profile");
    const bad: Record<string, unknown>[] = [
      { ...original.profile, kernel_release: "changed" },
      { ...original.profile, extra: true },
      { ...original.profile, kernel_release: "" },
      { ...original.profile, kernel_release: "\ud800" },
      { ...original.profile, cpu_model: "unavailable" },
      { ...original.profile, schema_version: true },
      { ...original.profile, architecture: "unknown" },
      { ...original.profile, vm_image_digest: `sha256:${"0".repeat(64)}` },
    ];
    for (const value of bad) {
      await expect(canonicalReviewedPublicProfile(value, original.digest, original.profile.vm_image_digest)).rejects.toThrow();
    }
  });

  it("preserves valid Unicode exactly and fails closed if its digest differs", async () => {
    const original = profiles[0];
    if (original === undefined) throw new Error("missing committed profile");
    const profile = { ...original.profile, kernel_release: "Linux-é-𝄞" };
    // Independent sorted compact JSON over this known closed structure.
    const sorted = (v: unknown): unknown => Array.isArray(v) ? v.map(sorted)
      : v !== null && typeof v === "object" ? Object.fromEntries(Object.entries(v).sort(([a], [b]) => a.localeCompare(b)).map(([k, x]) => [k, sorted(x)])) : v;
    const json = JSON.stringify(sorted(profile));
    const bytes = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode("lean-eval-replay-execution-profile-v1\0" + json)));
    const digest = [...bytes].map((n) => n.toString(16).padStart(2, "0")).join("");
    expect(await canonicalReviewedPublicProfile(profile, digest, profile.vm_image_digest)).toBe(json);
    await expect(canonicalReviewedPublicProfile(profile, original.digest, profile.vm_image_digest)).rejects.toThrow();
  });

  it("pins the isolated constant guard ahead of source decoding without request interpolation", async () => {
    const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(HISTORICAL_PUBLIC_COMMAND)));
    expect([...hash].map((n) => n.toString(16).padStart(2, "0")).join("")).toBe("25976a016a2c0c8f4ec4d6cbf712f8a36632e66d9d1dc189f16b04bd28befb4a");
    expect(PUBLIC_RUNTIME_GUARD).not.toContain("'");
    expect(HISTORICAL_PUBLIC_COMMAND.startsWith("/usr/bin/python3 -I -S -B -c '")).toBe(true);
    expect(HISTORICAL_PUBLIC_COMMAND).toBe(`/usr/bin/python3 -I -S -B -c '${PUBLIC_RUNTIME_GUARD}' && base64 --decode /workspace/historical-public-source.tar.gz.b64 > /workspace/historical-public-source.tar.gz && rm /workspace/historical-public-source.tar.gz.b64 && /opt/lean-eval/historical-public-runner`);
    expect(PUBLIC_RUNTIME_GUARD).toContain("loader.exec_module(runtime)");
    expect(PUBLIC_RUNTIME_GUARD).toContain("runtime.validate_runtime_profile");
    expect(PUBLIC_RUNTIME_GUARD).not.toContain("__main__");
  });
});
