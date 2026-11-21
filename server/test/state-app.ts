let appCount = 0;

function base64(bytes: ArrayBuffer): string {
  let binary = "";
  for (const byte of new Uint8Array(bytes)) binary += String.fromCharCode(byte);
  return btoa(binary);
}

/**
 * State App secrets for a test, under an App ID no other test uses, so the
 * Worker's per-isolate token cache cannot carry a token between tests.
 */
export async function stateAppEnv(): Promise<{ STATE_APP_ID: string; STATE_APP_PRIVATE_KEY: string }> {
  const pair = await crypto.subtle.generateKey(
    { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: Uint8Array.of(1, 0, 1), hash: "SHA-256" },
    true,
    ["sign", "verify"],
  );
  if (!("privateKey" in pair)) throw new TypeError("RSA key generation did not return a key pair");
  const pkcs8 = await crypto.subtle.exportKey("pkcs8", pair.privateKey);
  if (!(pkcs8 instanceof ArrayBuffer)) throw new TypeError("RSA private key export was not binary");
  appCount += 1;
  return {
    STATE_APP_ID: String(900000 + appCount),
    STATE_APP_PRIVATE_KEY: `-----BEGIN PRIVATE KEY-----\n${base64(pkcs8)}\n-----END PRIVATE KEY-----`,
  };
}

/** GitHub's answers to the installation lookup and token exchange, in order. */
export function stateAppTokenReplies(): Response[] {
  return [
    Response.json({ id: 77 }),
    Response.json({
      token: "ghs_state-installation-token",
      expires_at: new Date(Date.now() + 3_600_000).toISOString(),
      permissions: { contents: "write", metadata: "read" },
    }),
  ];
}
