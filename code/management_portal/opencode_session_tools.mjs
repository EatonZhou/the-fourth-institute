// OpenCode supplies the actual calling session and call ID, including for subagents.
// The bridge checks the session's ancestry before handing a call to a Portal Worker.
import { createHmac } from "node:crypto"

export const PortalSessionTools = async () => ({
  "tool.execute.before": async ({ tool, sessionID, callID }, { args }) => {
    if (/^ptl[0-9a-f]{12}_/.test(tool)) {
      const proof = createHmac("sha256", process.env.OPENCODE_SERVER_PASSWORD)
        .update(JSON.stringify([tool, sessionID, callID])).digest("hex")
      args.__portal_origin = { sessionID, callID, proof }
    }
  },
})
