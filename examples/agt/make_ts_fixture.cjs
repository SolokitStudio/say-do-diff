// Regenerate ts_audit.json with the real TypeScript SDK: npm i @microsoft/agent-governance-sdk@5.0.0 && node make_ts_fixture.cjs
const { AuditLogger } = require("@microsoft/agent-governance-sdk");
const fs = require("fs");
const log = new AuditLogger();
const did = "did:agentmesh:billing-agent:4b1d";
log.log({ agentId: did, action: "stripe.refunds.create", decision: "allow" });
log.log({ agentId: did, action: "email.send", decision: "allow" });
log.log({ agentId: did, action: "crm.customers.delete", decision: "deny" });
log.log({ agentId: did, action: "ledger.post", decision: "review" });
if (!log.verify()) throw new Error("chain");
fs.writeFileSync("ts_audit.json", log.exportJSON() + "\n");
