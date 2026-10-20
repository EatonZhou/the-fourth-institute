const fs = require("fs");
const { schemaMatches } = require('./site_compatibility.js');

const path = require("node:path");
const config = JSON.parse(fs.readFileSync(path.join(__dirname, "../../config/portal_instances.json"), "utf8"));
const root = process.env.PORTAL_INSTANCES_ROOT ?? config.root;
if (typeof root !== "string" || !path.isAbsolute(root) || /[\0\r\n]/.test(root)) {
  throw new Error("Portal instances root must be an absolute directory");
}
const DEPLOYMENT_REGISTRY = process.env.PORTAL_DEPLOYMENT_REGISTRY_PATH || path.join(root, "deployments.json");
const DEV_GENERATION_ENV = path.join(root, "dev/source-generation.env");

function resolveDevTarget() {
  const payload = JSON.parse(fs.readFileSync(DEPLOYMENT_REGISTRY, "utf8"));
  if (!schemaMatches(payload.schema, "tfi.portal-deployments.v1") || !Array.isArray(payload.instances)) {
    throw new Error("Portal deployment registry is invalid");
  }
  const matches = payload.instances.filter(item => (
    item && item.enabled !== false && item.id === "dev" && item.kind === "dev"
  ));
  if (matches.length !== 1) {
    throw new Error("Portal deployment registry must contain exactly one enabled dev instance");
  }
  const generation = fs.readFileSync(DEV_GENERATION_ENV, "utf8");
  const expectedBuildId = generation.split(/\r?\n/)
    .map(line => line.match(/^PORTAL_BUILD_ID=(.+)$/))
    .find(Boolean)?.[1]?.trim() || "";
  if (!expectedBuildId) throw new Error("Portal DEV generation has no expected build ID");
  return {
    instance: "dev",
    origin: String(matches[0].origin || "").replace(/\/$/, ""),
    expectedBuildId,
  };
}

function resolveDevOrigin() {
  return resolveDevTarget().origin;
}

module.exports = { DEPLOYMENT_REGISTRY, DEV_GENERATION_ENV, resolveDevOrigin, resolveDevTarget };
