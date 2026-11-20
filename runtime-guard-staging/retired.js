export default {
  fetch(_request, env) {
    return Response.json({ status: "retired", commit: env.DEPLOYED_COMMIT, ownership: env.EXECUTOR_OWNERSHIP_TAG }, { status: 503 });
  },
};
