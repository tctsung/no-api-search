import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function (pi: ExtensionAPI) {
  pi.registerMcpServer("web", {
    command: "uvx",
    args: ["--from", dirname(fileURLToPath(import.meta.url)), "no-api-search", "mcp"],
    exposure: "direct",
  });
}
