/**
 * ChatGPT monthly usage in Pi status line.
 * Reads ~/.codex/auth.json tokens.{account_id, access_token}.
 */

import type {
    ExtensionAPI,
    ExtensionContext,
} from "@earendil-works/pi-coding-agent";
import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

const STATUS_KEY = "codex-usage";
const AUTH_PATH = join(homedir(), ".codex", "auth.json");
const API_BASE = "https://chatgpt.com/backend-api";
const REFRESH_INTERVAL_MS = 1 * 60 * 1000; // 1 minute
const FETCH_TIMEOUT_MS = 10_000;

interface CodexTokens {
    account_id: string;
    access_token: string;
    [key: string]: unknown;
}

interface MonthlyUsageResponse {
    effective_monthly_limit: {
        limit: number;
        enforcement_mode: string;
    };
    current_month_usage: number;
}

async function loadTokens(): Promise<CodexTokens | null> {
    try {
        const raw = await readFile(AUTH_PATH, "utf8");
        const parsed = JSON.parse(raw) as { tokens?: CodexTokens };
        const t = parsed?.tokens;
        if (t?.account_id && t?.access_token) return t;
        return null;
    } catch {
        return null;
    }
}

async function fetchMonthlyUsage(
    tokens: CodexTokens,
): Promise<MonthlyUsageResponse | null> {
    const url = `${API_BASE}/accounts/${tokens.account_id}/spend-controls/current-user/monthly-usage`;
    try {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
        const res = await fetch(url, {
            headers: { Authorization: `Bearer ${tokens.access_token}` },
            signal: controller.signal,
        }).finally(() => clearTimeout(timer));
        if (!res.ok) return null;
        return (await res.json()) as MonthlyUsageResponse;
    } catch {
        return null;
    }
}

function formatStatus(data: MonthlyUsageResponse | null): string {
    if (!data) return "ChatGPT usage ?";
    const used = data.current_month_usage;
    const limit = data.effective_monthly_limit.limit;
    const pct = limit > 0 ? Math.round((used / limit) * 100) : 0;
    return `ChatGPT ${used}/${limit} (${pct}%)`;
}

export default function (pi: ExtensionAPI) {
    let lastFetchAt = 0;
    let intervalHandle: ReturnType<typeof setInterval> | null = null;

    async function refresh(ctx: ExtensionContext): Promise<void> {
        const now = Date.now();
        if (now - lastFetchAt < REFRESH_INTERVAL_MS) return; // cache hit
        lastFetchAt = now;

        const tokens = await loadTokens();
        if (!tokens) {
            ctx.ui.setStatus(STATUS_KEY, undefined);
            return;
        }

        const data = await fetchMonthlyUsage(tokens);
        ctx.ui.setStatus(STATUS_KEY, formatStatus(data));
    }

    pi.on("session_start", async (_event, ctx) => {
        try {
            lastFetchAt = 0;
            await refresh(ctx);

            intervalHandle = setInterval(() => {
                lastFetchAt = 0; // force fetch on timer tick
                void refresh(ctx).catch((err) =>
                    console.warn("[codex-monthly-usage] interval refresh error:", err),
                );
            }, REFRESH_INTERVAL_MS);
        } catch (err) {
            console.warn("[codex-monthly-usage] session_start error:", err);
        }
    });

    pi.on("turn_end", async (_event, ctx) => {
        try {
            await refresh(ctx);
        } catch (err) {
            console.warn("[codex-monthly-usage] turn_end error:", err);
        }
    });

    pi.on("session_shutdown", async (_event, _ctx) => {
        try {
            if (intervalHandle !== null) {
                clearInterval(intervalHandle);
                intervalHandle = null;
            }
        } catch (err) {
            console.warn("[codex-monthly-usage] session_shutdown error:", err);
        }
    });
}
