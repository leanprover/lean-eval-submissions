import { ContainerProxy, Sandbox } from "@cloudflare/sandbox";

import { handleReplayRequest, type ReplayRuntimeEnv } from "./replay-app";
import { ReplayTerminalReceipt } from "./replay-terminal-receipt";
import {
  historicalReceiptObjectName,
  replaySandbox,
} from "./replay-sandbox";

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
      receiptStore(runtime, runnerNonce, historicalIdentity) {
        const name = historicalIdentity === undefined
          ? `r-${runnerNonce.slice(0, 61)}`
          : historicalReceiptObjectName(
            historicalIdentity.replay_task_id,
            historicalIdentity.attempt,
          );
        return runtime.REPLAY_TERMINAL_RECEIPT.getByName(name);
      },
      recoveryStore(runtime, replayTaskId, attempt) {
        return runtime.REPLAY_TERMINAL_RECEIPT.getByName(
          historicalReceiptObjectName(replayTaskId, attempt),
        );
      },
    });
  },
} satisfies ExportedHandler<ReplayRuntimeEnv>;
