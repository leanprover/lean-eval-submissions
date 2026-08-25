import { handleRequest, handleScheduled, type RuntimeEnv } from "./app";
export { ModelIdentityQualificationJournal } from "./model-identity-qualification-journal";

export default {
  fetch(request, env, context): Promise<Response> {
    return handleRequest(request, env, context);
  },
  scheduled(controller, env): Promise<void> {
    return handleScheduled(env, controller.scheduledTime);
  },
} satisfies ExportedHandler<RuntimeEnv>;
