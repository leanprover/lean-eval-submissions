import { handleBrokerRequest, type BrokerRuntimeEnv } from "./github-broker";

export default {
  fetch(request: Request, env: BrokerRuntimeEnv): Promise<Response> {
    return handleBrokerRequest(request, env);
  },
} satisfies ExportedHandler<BrokerRuntimeEnv>;
