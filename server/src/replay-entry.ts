import { ContainerProxy, getSandbox, Sandbox } from "@cloudflare/sandbox";

import { handleReplayRequest, type ReplayRuntimeEnv } from "./replay-app";
import { ReplayTerminalReceipt } from "./replay-terminal-receipt";

export { ContainerProxy, ReplayTerminalReceipt };

export class ReplaySandbox extends Sandbox {
  override enableInternet = false;
}

export default {
  fetch(request, env): Promise<Response> {
    return handleReplayRequest(request, env, {
      authenticate: async (incoming, runtime) => {
        const { verifyGithubOidc } = await import("./replay-auth");
        await verifyGithubOidc(incoming, runtime);
      },
      sandbox(runtime, runnerNonce) {
        // Sandbox IDs are DNS labels capped at 63 characters. A 244-bit
        // prefix remains collision-resistant while the full nonce stays in
        // the authenticated request/evidence contract.
        return getSandbox(runtime.REPLAY_SANDBOX, `r-${runnerNonce.slice(0, 61)}`, {
          enableDefaultSession: false,
          keepAlive: false,
          sleepAfter: "5m",
          containerTimeouts: {
            instanceGetTimeoutMS: 120_000,
            portReadyTimeoutMS: 180_000,
          },
        });
      },
      receiptStore(runtime, runnerNonce) {
        return runtime.REPLAY_TERMINAL_RECEIPT.getByName(`r-${runnerNonce.slice(0, 61)}`);
      },
    });
  },
} satisfies ExportedHandler<ReplayRuntimeEnv>;
