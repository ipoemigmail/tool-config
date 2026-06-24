import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { readFile } from "node:fs/promises";
import type { ReadonlySessionManager } from "@earendil-works/pi-coding-agent";

const EXTENSION_MARKER = "[pi-extension:auto-load-worker-skills]";
const SKILL_PATH = "/Users/ben.jeong1/.pi/agent/skills/change-workers/SKILL.md";
const STATUS_KEY = "worker-skills";
const DEFAULT_WORKER = "gpt";
const WORKER_ENV_KEYS = ["PI_SELECTED_WORKER", "PI_WORKER", "WORKER"] as const;
const KNOWN_WORKERS = ["gpt", "anth", "ollama"] as const;
type KnownWorker = (typeof KNOWN_WORKERS)[number];

// Session-scoped worker state — no global persistence file
let currentWorker: string = (() => {
    for (const key of WORKER_ENV_KEYS) {
        const val = process.env[key]?.trim();
        if (val) return val;
    }
    return DEFAULT_WORKER;
})();

/** Last non-empty token after splitting by whitespace. */
function lastToken(s: string): string | undefined {
    return s.trim().split(/\s+/).filter(Boolean).pop();
}

/**
 * Detect a worker change from user-provided text (raw input or expanded prompt).
 *
 * Priority:
 * 1. Exact single known worker token (user typed just "anth").
 * 2. change-workers context:
 *    a. If </skill> present → look only at trailing text after it (actual user argument).
 *       Skill body itself is polluted with example worker names — never scan it.
 *    b. "User: <worker>" pattern as a secondary signal.
 * 3. Otherwise → undefined.
 */
function detectWorkerChange(text: string): string | undefined {
    if (!text) return undefined;
    const trimmed = text.trim();

    // 1. Exact single known worker token
    if (KNOWN_WORKERS.includes(trimmed as KnownWorker)) return trimmed;

    // 2. change-workers context
    if (!/change-workers/.test(text)) return undefined;

    // 2a. Text after </skill> is the real user argument — skill body is contaminated with examples
    if (text.includes("</skill>")) {
        const afterClose = text.split("</skill>").pop() ?? "";
        const tok = lastToken(afterClose);
        if (tok && KNOWN_WORKERS.includes(tok as KnownWorker)) return tok;
    }

    // 2b. "User: <worker>" injected pattern (outside skill body)
    const m = text.match(/\bUser:\s*(\S+)/);
    if (m && KNOWN_WORKERS.includes(m[1] as KnownWorker)) return m[1];

    // Do NOT fall back to whole-text scan — skill body examples pollute it
    return undefined;
}

/**
 * Resolve the active worker from the current session's conversation history.
 * Scans user messages latest→earliest and returns the argument of the most recent
 * change-workers skill invocation.
 * Never reads from systemPrompt (self-injected runtime-skill block is not a trust source).
 */
function resolveWorkerFromSession(
    sessionManager: ReadonlySessionManager,
): string | undefined {
    try {
        const branch = sessionManager.getBranch();
        // Iterate latest→earliest to find the most recent change-workers call
        for (let i = branch.length - 1; i >= 0; i--) {
            const entry = branch[i];
            if (entry.type !== "message") continue;
            const msg = entry.message;
            if (msg.role !== "user") continue;

            // Extract plain text from content (string or TextContent[] array)
            let text: string;
            const content = (msg as { role: string; content: unknown }).content;
            if (typeof content === "string") {
                text = content;
            } else if (Array.isArray(content)) {
                text = (content as Array<{ type?: string; text?: string }>)
                    .filter((c) => c.type === "text" && typeof c.text === "string")
                    .map((c) => c.text as string)
                    .join("\n");
            } else {
                continue;
            }

            const detected = detectWorkerChange(text);
            if (detected) return detected;
        }
    } catch (e) {
        console.warn("[auto-load-worker-skills] resolveWorkerFromSession error:", e);
    }
    return undefined;
}

type StatusCtx = { ui: { setStatus: (key: string, value: string) => void } };

function safeSetWorkerStatus(ctx: StatusCtx, worker: string): void {
    try {
        ctx.ui.setStatus(STATUS_KEY, `[worker:${worker}]`);
    } catch (e) {
        console.warn("[auto-load-worker-skills] failed to set status:", e);
    }
}

function buildInjectedBlock(skillBody: string, worker: string, warning?: string): string {
    const parts = [
        "",
        EXTENSION_MARKER,
        "- Default response style: caveman lite. Be brief, direct, and complete. Avoid fluff.",
        `- Runtime-loaded skill instruction follows. Apply it with argument \`${worker}\` (equivalent to \`User: ${worker}\`).`,
        `<runtime-skill path="${SKILL_PATH}">`,
        `User: ${worker}`,
        skillBody.trim(),
        "</runtime-skill>",
    ];
    if (warning) parts.push(`- Warning: ${warning}`);
    parts.push(EXTENSION_MARKER);
    return parts.join("\n");
}

function escapeRegex(s: string): string {
    return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Replace the existing injected block in systemPrompt with an updated worker. */
function replaceInjectedBlock(prompt: string, skillBody: string, worker: string): string {
    const pattern = new RegExp(
        escapeRegex(EXTENSION_MARKER) + "[\\s\\S]*?" + escapeRegex(EXTENSION_MARKER),
    );
    return prompt.replace(pattern, buildInjectedBlock(skillBody, worker).trim());
}

/** Update currentWorker + setStatus if changed. */
function applyWorkerChange(detected: string, ctx: StatusCtx): void {
    if (detected === currentWorker) return;
    currentWorker = detected;
    safeSetWorkerStatus(ctx, currentWorker);
}

export default function (pi: ExtensionAPI) {
    // Show status at session start; restore worker from history for resume/reload/fork
    pi.on("session_start", (_event, ctx) => {
        try {
            const fromHistory = resolveWorkerFromSession(ctx.sessionManager);
            if (fromHistory) currentWorker = fromHistory;
        } catch (e) {
            console.warn("[auto-load-worker-skills] session_start resolution error:", e);
        }
        safeSetWorkerStatus(ctx as StatusCtx, currentWorker);
    });

    // Primary detection: raw user input fires before skill/template expansion
    pi.on("input", (event, ctx) => {
        try {
            const detected = detectWorkerChange(event.text);
            if (detected) applyWorkerChange(detected, ctx as StatusCtx);
        } catch (e) {
            console.warn("[auto-load-worker-skills] input handler error:", e);
        }
    });

    pi.on("before_agent_start", async (event, ctx) => {
        // Secondary detection: expanded prompt (covers /skill: expansion producing skill body).
        // event.prompt is user input only — does NOT contain self-injected runtime-skill block.
        const detected = detectWorkerChange(event.prompt);
        if (detected) applyWorkerChange(detected, ctx as StatusCtx);

        safeSetWorkerStatus(ctx as StatusCtx, currentWorker);

        const systemPrompt = event.systemPrompt ?? "";

        if (!systemPrompt.includes(EXTENSION_MARKER)) {
            // First injection this session
            let skillBody = "";
            let warning: string | undefined;
            try {
                skillBody = await readFile(SKILL_PATH, "utf8");
            } catch (error) {
                const detail = error instanceof Error ? error.message : String(error);
                warning = `Failed to read change-workers skill at ${SKILL_PATH}: ${detail}`;
                try {
                    await (ctx.ui as { notify?: (message: string) => unknown }).notify?.(warning);
                } catch (notifyError) {
                    console.warn("[auto-load-worker-skills] failed to send UI notification:", notifyError);
                }
            }
            const block = buildInjectedBlock(skillBody, currentWorker, warning);
            const sep = systemPrompt.endsWith("\n") || systemPrompt.length === 0 ? "" : "\n\n";
            return { systemPrompt: `${systemPrompt}${sep}${block}` };
        }

        // Already injected — check if worker changed; update block if needed.
        // Read existingWorker from block only to decide re-injection (NOT as source of truth).
        const existingMatch = systemPrompt.match(
            /<runtime-skill path="[^"]*change-workers\/SKILL\.md">\s*\nUser:\s*(\S+)/,
        );
        const existingWorker = existingMatch?.[1];
        if (existingWorker === currentWorker) {
            return { systemPrompt };
        }

        // Worker changed — update the injected block
        let skillBody = "";
        try {
            skillBody = await readFile(SKILL_PATH, "utf8");
        } catch {
            // Use empty body; worker reference still updates
        }
        return { systemPrompt: replaceInjectedBlock(systemPrompt, skillBody, currentWorker) };
    });
}
