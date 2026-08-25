import type { ModelIdentityQualificationJournal } from "../src/model-identity-qualification-journal";

declare global {
  namespace Cloudflare {
    // eslint-disable-next-line @typescript-eslint/consistent-type-definitions -- ambient binding augmentation must merge.
    interface Env {
      MODEL_IDENTITY_QUALIFICATION_EXECUTOR?: Fetcher;
      MODEL_IDENTITY_QUALIFICATION_JOURNAL?:
        DurableObjectNamespace<ModelIdentityQualificationJournal>;
    }
  }
}

export {};
