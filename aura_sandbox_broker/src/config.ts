export interface Config {
  /** The secret callers of this service must present. */
  brokerToken: string;
  /** A project token for the sandbox project. Only this service holds it. */
  railwayToken: string;
  /** The environment sandboxes are created in. Never the one this service runs in. */
  environmentId: string;
  port: number;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Read only the `AURA_*` names. The SDK would fall back to `RAILWAY_TOKEN`, `RAILWAY_API_TOKEN`
 * and `RAILWAY_ENVIRONMENT_ID`, and Railway injects the last one into every service, naming the
 * service's own environment: an unset value here would create sandboxes beside production.
 */
export function loadConfig(env: Record<string, string | undefined>): Config {
  const brokerToken = env.AURA_BROKER_TOKEN ?? "";
  if (brokerToken.length < 32) throw new Error("AURA_BROKER_TOKEN must be at least 32 characters");

  const railwayToken = (env.AURA_SANDBOX_RAILWAY_TOKEN ?? "").trim();
  if (!railwayToken) {
    throw new Error("AURA_SANDBOX_RAILWAY_TOKEN is required: a project token for the sandbox project");
  }
  if (railwayToken === brokerToken) {
    throw new Error("AURA_BROKER_TOKEN and AURA_SANDBOX_RAILWAY_TOKEN must be different");
  }

  const environmentId = (env.AURA_SANDBOX_ENVIRONMENT_ID ?? "").trim();
  if (!UUID.test(environmentId)) {
    throw new Error("AURA_SANDBOX_ENVIRONMENT_ID must be the sandbox environment's id");
  }
  if (environmentId.toLowerCase() === env.RAILWAY_ENVIRONMENT_ID?.toLowerCase()) {
    throw new Error("refusing to create sandboxes in the broker's own environment");
  }

  const port = Number(env.PORT ?? "8080");
  if (!Number.isInteger(port) || port < 1 || port > 65_535) throw new Error("PORT must be 1-65535");

  return { brokerToken, railwayToken, environmentId, port };
}
