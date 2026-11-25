import { loadConfig } from "./config.js";
import { createRailwayProvider } from "./railway.js";
import { createBroker } from "./server.js";

const log = (entry: Record<string, unknown>) => {
  process.stdout.write(`${JSON.stringify({ time: new Date().toISOString(), ...entry })}\n`);
};

const config = loadConfig(process.env);
const provider = createRailwayProvider(config);
const server = createBroker({ brokerToken: config.brokerToken, provider, log });

// "::" accepts IPv4 and IPv6: Railway's private network is IPv6.
server.listen(config.port, "::", () => log({ event: "listening", port: config.port }));

// Build the template ahead of the first request. A failure is retried by the next create.
provider.prepare().then(
  () => log({ event: "template_ready" }),
  (error: unknown) =>
    log({ event: "template_failed", error: error instanceof Error ? error.constructor.name : "unknown" }),
);

for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => {
    log({ event: "stopping" });
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 10_000).unref();
  });
}
