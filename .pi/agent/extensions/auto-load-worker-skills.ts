import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { readFile } from "node:fs/promises";

const EXTENSION_MARKER = "[pi-extension:auto-load-worker-skills]";
const SKILL_PATH = "/Users/ben.jeong1/.pi/agent/skills/change-workers/SKILL.md";

function buildInjectedPrompt(skillBody: string, warning?: string): string {
    const parts = [
        "",
        EXTENSION_MARKER,
        "- Default response style: caveman lite. Be brief, direct, and complete. Avoid fluff.",
        "- Runtime-loaded skill instruction follows. Apply it with argument `gpt` (equivalent to `User: gpt`).",
        "<runtime-skill path=\"/Users/ben.jeong1/.pi/agent/skills/change-workers/SKILL.md\">",
        "User: gpt",
        skillBody.trim(),
        "</runtime-skill>",
    ];

    if (warning) {
        parts.push(`- Warning: ${warning}`);
    }

    parts.push(EXTENSION_MARKER);
    return parts.join("\n");
}

export default function (pi: ExtensionAPI) {
    pi.on("before_agent_start", async (event, ctx) => {
        const currentPrompt = event.systemPrompt ?? "";
        if (currentPrompt.includes(EXTENSION_MARKER)) {
            return { systemPrompt: currentPrompt };
        }

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
                console.warn(
                    "[auto-load-worker-skills] failed to send UI notification:",
                    notifyError,
                );
            }
        }

        const injectedPrompt = buildInjectedPrompt(skillBody, warning);
        const separator = currentPrompt.endsWith("\n") || currentPrompt.length === 0 ? "" : "\n\n";

        return {
            systemPrompt: `${currentPrompt}${separator}${injectedPrompt}`,
        };
    });
}
