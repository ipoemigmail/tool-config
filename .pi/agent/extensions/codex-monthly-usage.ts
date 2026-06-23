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
const STALE_CTX_ERROR_FRAGMENT = "This extension ctx is stale";

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

interface RefreshSessionState {
    generation: number;
    active: boolean;
    ctx: ExtensionContext;
    intervalHandle: ReturnType<typeof setInterval> | null;
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

function isMonthlyUsageResponse(data: unknown): data is MonthlyUsageResponse {
    if (!data || typeof data !== "object") return false;

    const usage = data as {
        effective_monthly_limit?: { limit?: unknown };
        current_month_usage?: unknown;
    };

    return (
        typeof usage.effective_monthly_limit?.limit === "number" &&
        typeof usage.current_month_usage === "number"
    );
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

        const json: unknown = await res.json();
        return isMonthlyUsageResponse(json) ? json : null;
    } catch {
        return null;
    }
}

function formatStatus(data: MonthlyUsageResponse | null): string {
    const used = data?.current_month_usage;
    const limit = data?.effective_monthly_limit?.limit;
    if (typeof used !== "number" || typeof limit !== "number") {
        return "ChatGPT usage ?";
    }

    const pct = limit > 0 ? Math.round((used / limit) * 100) : 0;
    return `ChatGPT ${used}/${limit} (${pct}%)`;
}

function isStaleCtxError(err: unknown): boolean {
    return err instanceof Error && err.message.includes(STALE_CTX_ERROR_FRAGMENT);
}

export default function (pi: ExtensionAPI) {
    let lastFetchAt = 0;
    let currentGeneration = 0;
    let currentSession: RefreshSessionState | null = null;
    const warnedKeys = new Set<string>();

    function warnOnce(key: string, message: string, err: unknown): void {
        if (warnedKeys.has(key)) return;
        warnedKeys.add(key);
        console.warn(message, err);
    }

    function isSessionActive(session: RefreshSessionState): boolean {
        return (
            session.active &&
            currentSession === session &&
            session.generation === currentGeneration
        );
    }

    function deactivateSession(session: RefreshSessionState): void {
        session.active = false;
        if (session.intervalHandle !== null) {
            clearInterval(session.intervalHandle);
            session.intervalHandle = null;
        }
        if (currentSession === session) currentSession = null;
    }

    function setStatusIfActive(
        session: RefreshSessionState,
        status: string | undefined,
    ): void {
        if (!isSessionActive(session)) return;
        try {
            session.ctx.ui.setStatus(STATUS_KEY, status);
        } catch (err) {
            if (isStaleCtxError(err)) {
                if (isSessionActive(session)) deactivateSession(session);
                return;
            }

            warnOnce(
                "setStatusIfActive",
                "[codex-monthly-usage] setStatus error:",
                err,
            );
        }
    }

    async function refresh(session: RefreshSessionState): Promise<void> {
        try {
            if (!isSessionActive(session)) return;

            const now = Date.now();
            if (now - lastFetchAt < REFRESH_INTERVAL_MS) return; // cache hit
            lastFetchAt = now;

            const tokens = await loadTokens();
            if (!isSessionActive(session)) return;

            if (!tokens) {
                setStatusIfActive(session, undefined);
                return;
            }

            const data = await fetchMonthlyUsage(tokens);
            if (!isSessionActive(session)) return;

            setStatusIfActive(session, formatStatus(data));
        } catch (err) {
            warnOnce("refresh", "[codex-monthly-usage] refresh error:", err);
        }
    }

    pi.on("session_start", async (_event: unknown, ctx: ExtensionContext) => {
        try {
            if (currentSession) deactivateSession(currentSession);

            const session: RefreshSessionState = {
                generation: ++currentGeneration,
                active: true,
                ctx,
                intervalHandle: null,
            };
            currentSession = session;
            lastFetchAt = 0;
            await refresh(session);
            if (!isSessionActive(session)) return;

            session.intervalHandle = setInterval(() => {
                if (!isSessionActive(session)) return;
                lastFetchAt = 0; // force fetch on timer tick
                void refresh(session).catch((err) =>
                    console.warn("[codex-monthly-usage] interval refresh error:", err),
                );
            }, REFRESH_INTERVAL_MS);
        } catch (err) {
            console.warn("[codex-monthly-usage] session_start error:", err);
        }
    });

    pi.on("turn_end", async (_event: unknown, ctx: ExtensionContext) => {
        try {
            const session = currentSession;
            if (!session || session.ctx !== ctx || !isSessionActive(session)) return;
            await refresh(session);
        } catch (err) {
            console.warn("[codex-monthly-usage] turn_end error:", err);
        }
    });

    pi.on("session_shutdown", async (_event: unknown, ctx: ExtensionContext) => {
        try {
            const session = currentSession;
            if (!session || session.ctx !== ctx) return;

            currentGeneration += 1;
            deactivateSession(session);
        } catch (err) {
            console.warn("[codex-monthly-usage] session_shutdown error:", err);
        }
    });
}
