import { handleRequest, handleScheduled, type RuntimeEnv } from "./app";

export default {
  fetch(request, env, context): Promise<Response> {
    return handleRequest(request, env, context);
  },
  scheduled(controller, env): Promise<void> {
    return handleScheduled(env, controller.scheduledTime);
  },
} satisfies ExportedHandler<RuntimeEnv>;
