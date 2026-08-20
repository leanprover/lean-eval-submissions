import { handleRequest, type RuntimeEnv } from "./app";

export default {
  fetch(request, env, context): Promise<Response> {
    return handleRequest(request, env, context);
  },
} satisfies ExportedHandler<RuntimeEnv>;
