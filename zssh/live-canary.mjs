import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const port = Number(process.env.PORT || 8788);
const token = process.env.ZSSH_DEV_BEARER_TOKEN || "";
if (!token) throw new Error("ZSSH_DEV_BEARER_TOKEN is required for the live canary");

const client = new Client({ name: "zssh-live-canary", version: "1.0.0" });
const transport = new StreamableHTTPClientTransport(
  new URL(`http://127.0.0.1:${port}/mcp`),
  {
    requestInit: {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    },
  },
);

try {
  await client.connect(transport);

  const listed = await client.listTools();
  const names = new Set((listed.tools || []).map(tool => tool.name));
  for (const required of ["zssh_server_info", "zssh_run_safe", "zssh_read_file", "zssh_write_file", "zssh_exec"]) {
    if (!names.has(required)) throw new Error(`missing MCP tool: ${required}`);
  }

  const infoResult = await client.callTool({ name: "zssh_server_info", arguments: {} });
  const infoText = infoResult.content?.find(part => part.type === "text")?.text;
  const info = JSON.parse(infoText || "{}");
  if (info.uid === 0) throw new Error("zSSH is running as root");
  if (info.exec_mode !== "disabled") throw new Error(`raw shell is not fail-closed: ${info.exec_mode}`);

  const safeResult = await client.callTool({
    name: "zssh_run_safe",
    arguments: { program: "whoami", args: [] },
  });
  const safeText = safeResult.content?.find(part => part.type === "text")?.text;
  const safe = JSON.parse(safeText || "{}");
  if (!safe.ok || !String(safe.stdout || "").trim()) throw new Error("safe execution canary failed");

  console.log(JSON.stringify({
    ok: true,
    service: "zssh",
    uid: info.uid,
    exec_mode: info.exec_mode,
    safe_program: safe.program,
    tool_count: names.size,
  }));
} finally {
  await client.close().catch(() => {});
}
