import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { readFile } from "node:fs/promises";
import type { ReadonlySessionManager } from "@earendil-works/pi-coding-agent";

const EXTENSION_MARKER = "[pi-extension:auto-load-team-command]";
const PROMPT_PATH = "/Users/ben.jeong1/.pi/agent/prompts/change-team.md";
const STATUS_KEY = "team-command";
const DEFAULT_TEAM = "gpt";
const TEAM_ENV_KEYS = ["PI_SELECTED_TEAM", "PI_TEAM", "TEAM"] as const;
const KNOWN_TEAMS = ["gpt", "claude", "ollama"] as const;
type KnownTeam = (typeof KNOWN_TEAMS)[number];

// Session-scoped team state — no global persistence file
let currentTeam: string = (() => {
    for (const key of TEAM_ENV_KEYS) {
        const val = process.env[key]?.trim();
        if (val) return val;
    }
    return DEFAULT_TEAM;
})();

/**
 * Detect a team change from user-provided text (raw input or session history).
 *
 * Two patterns only — must not be polluted by prompt template body examples:
 * 1. Exact single known team token (user typed just "claude").
 * 2. `/change-team <team>` slash command — first known team token
 *    immediately following the command name. This form appears verbatim in
 *    raw user input and session history, so it is safe to match.
 *
 * Whole-text / last-token scans are intentionally absent: the expanded
 * prompt body contains example team names (e.g. "예: `claude`, `gpt`, `ollama`")
 * that would cause false positives.
 */
function detectTeamChange(text: string): string | undefined {
    if (!text) return undefined;
    const trimmed = text.trim();

    // 1. Exact single known team token
    if (KNOWN_TEAMS.includes(trimmed as KnownTeam)) return trimmed;

    // 2. /change-team <team> — first argument of the slash command only
    const m = text.match(/\/change-team\s+(\S+)/);
    if (m && KNOWN_TEAMS.includes(m[1] as KnownTeam)) return m[1];

    return undefined;
}

/**
 * Resolve the active team from the current session's conversation history.
 * Scans user messages latest→earliest and returns the argument of the most recent
 * change-team command invocation.
 */
function resolveTeamFromSession(
    sessionManager: ReadonlySessionManager,
): string | undefined {
    try {
        const branch = sessionManager.getBranch();
        for (let i = branch.length - 1; i >= 0; i--) {
            const entry = branch[i];
            if (entry.type !== "message") continue;
            const msg = entry.message;
            if (msg.role !== "user") continue;

            let text: string;
            const content = (msg as { role: string; content: unknown }).content;
            if (typeof content === "string") {
                text = content;
            } else if (Array.isArray(content)) {
                text = (content as Array<{ type?: string; text?: string }>)
                    .filter(
                        (c) => c.type === "text" && typeof c.text === "string",
                    )
                    .map((c) => c.text as string)
                    .join("\n");
            } else {
                continue;
            }

            const detected = detectTeamChange(text);
            if (detected) return detected;
        }
    } catch (e) {
        console.warn(
            "[auto-load-team-command] resolveTeamFromSession error:",
            e,
        );
    }
    return undefined;
}

/**
 * Strip YAML frontmatter (--- ... ---) from a markdown file and return body only.
 */
function stripFrontmatter(raw: string): string {
    const match = raw.match(/^---[\s\S]*?---\n?([\s\S]*)$/);
    return match ? match[1] : raw;
}

/**
 * Replace prompt template placeholders with the current team value.
 * Handles: ${1:-default}, $1, $@, $ARGUMENTS, ${@:N}
 */
function applyTemplateSubstitution(body: string, team: string): string {
    return body
        // ${1:-default} → team
        .replace(/\$\{1:-[^}]*\}/g, team)
        // ${@:N} → team
        .replace(/\$\{@:[^}]*\}/g, team)
        // $1 → team
        .replace(/\$1\b/g, team)
        // $@ → team
        .replace(/\$@/g, team)
        // $ARGUMENTS → team
        .replace(/\$ARGUMENTS\b/g, team);
}

type StatusCtx = { ui: { setStatus: (key: string, value: string) => void } };

function safeSetTeamStatus(ctx: StatusCtx, team: string): void {
    try {
        ctx.ui.setStatus(STATUS_KEY, `[team:${team}]`);
    } catch (e) {
        console.warn("[auto-load-team-command] failed to set status:", e);
    }
}

function buildInjectedBlock(
    promptBody: string,
    team: string,
    warning?: string,
): string {
    const substituted = applyTemplateSubstitution(promptBody, team);
    const parts = [
        "",
        EXTENSION_MARKER,
        "- Default response style: caveman lite. Be brief, direct, and complete. Avoid fluff.",
        `- Runtime-loaded command instruction follows. Apply it with argument \`${team}\` (equivalent to \`User: ${team}\`).`,
        `<runtime-skill path="${PROMPT_PATH}">`,
        `User: ${team}`,
        substituted.trim(),
        "</runtime-skill>",
    ];
    if (warning) parts.push(`- Warning: ${warning}`);
    parts.push(EXTENSION_MARKER);
    return parts.join("\n");
}

function escapeRegex(s: string): string {
    return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Replace the existing injected block in systemPrompt with an updated team. */
function replaceInjectedBlock(
    prompt: string,
    promptBody: string,
    team: string,
): string {
    const pattern = new RegExp(
        escapeRegex(EXTENSION_MARKER) +
            "[\\s\\S]*?" +
            escapeRegex(EXTENSION_MARKER),
    );
    return prompt.replace(pattern, buildInjectedBlock(promptBody, team).trim());
}

/** Update currentTeam + setStatus if changed. */
function applyTeamChange(detected: string, ctx: StatusCtx): void {
    if (detected === currentTeam) return;
    currentTeam = detected;
    safeSetTeamStatus(ctx, currentTeam);
}

export default function (pi: ExtensionAPI) {
    // Show status at session start; restore team from history for resume/reload/fork
    pi.on("session_start", (_event, ctx) => {
        try {
            const fromHistory = resolveTeamFromSession(ctx.sessionManager);
            if (fromHistory) currentTeam = fromHistory;
        } catch (e) {
            console.warn(
                "[auto-load-team-command] session_start resolution error:",
                e,
            );
        }
        safeSetTeamStatus(ctx as StatusCtx, currentTeam);
    });

    // Primary detection: raw user input fires before template expansion
    pi.on("input", (event, ctx) => {
        try {
            const detected = detectTeamChange(event.text);
            if (detected) applyTeamChange(detected, ctx as StatusCtx);
        } catch (e) {
            console.warn("[auto-load-team-command] input handler error:", e);
        }
    });

    pi.on("before_agent_start", async (event, ctx) => {
        // Secondary detection: expanded prompt (covers /change-team <team> expansion)
        const detected = detectTeamChange(event.prompt);
        if (detected) applyTeamChange(detected, ctx as StatusCtx);

        safeSetTeamStatus(ctx as StatusCtx, currentTeam);

        const systemPrompt = event.systemPrompt ?? "";

        if (!systemPrompt.includes(EXTENSION_MARKER)) {
            // First injection this session
            let promptBody = "";
            let warning: string | undefined;
            try {
                const raw = await readFile(PROMPT_PATH, "utf8");
                promptBody = stripFrontmatter(raw);
            } catch (error) {
                const detail =
                    error instanceof Error ? error.message : String(error);
                warning = `Failed to read change-team prompt at ${PROMPT_PATH}: ${detail}`;
                try {
                    await (
                        ctx.ui as { notify?: (message: string) => unknown }
                    ).notify?.(warning);
                } catch (notifyError) {
                    console.warn(
                        "[auto-load-team-command] failed to send UI notification:",
                        notifyError,
                    );
                }
            }
            const block = buildInjectedBlock(promptBody, currentTeam, warning);
            const sep =
                systemPrompt.endsWith("\n") || systemPrompt.length === 0
                    ? ""
                    : "\n\n";
            return { systemPrompt: `${systemPrompt}${sep}${block}` };
        }

        // Already injected — check if team changed; update block if needed.
        const existingMatch = systemPrompt.match(
            /<runtime-skill path="[^"]*change-team\.md">\s*\nUser:\s*(\S+)/,
        );
        const existingTeam = existingMatch?.[1];
        if (existingTeam === currentTeam) {
            return { systemPrompt };
        }

        // Team changed — update the injected block
        let promptBody = "";
        try {
            const raw = await readFile(PROMPT_PATH, "utf8");
            promptBody = stripFrontmatter(raw);
        } catch {
            // Use empty body; team reference still updates
        }
        return {
            systemPrompt: replaceInjectedBlock(
                systemPrompt,
                promptBody,
                currentTeam,
            ),
        };
    });
}
