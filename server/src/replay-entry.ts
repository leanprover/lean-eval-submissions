import { ContainerProxy, Sandbox } from "@cloudflare/sandbox";

import { handleReplayRequest, type ReplayRuntimeEnv } from "./replay-app";
import { ReplayTerminalReceipt } from "./replay-terminal-receipt";
import { replaySandbox } from "./replay-sandbox";

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
        return replaySandbox(runtime, runnerNonce);
      },
      receiptStore(runtime, runnerNonce) {
        return runtime.REPLAY_TERMINAL_RECEIPT.getByName(`r-${runnerNonce.slice(0, 61)}`);
      },
    });
  },
} satisfies ExportedHandler<ReplayRuntimeEnv>;
