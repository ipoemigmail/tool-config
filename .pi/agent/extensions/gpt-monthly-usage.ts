/**
 * ChatGPT monthly usage in Pi status line.
 * Reads Pi agent auth.json openai-codex.{accountId, access}.
 */

import type {
    ExtensionAPI,
    ExtensionContext,
} from "@earendil-works/pi-coding-agent";
import { getAgentDir } from "@earendil-works/pi-coding-agent";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

const STATUS_KEY = "zgpt-usage";
const AUTH_PATH = join(getAgentDir(), "auth.json");
const API_BASE = "https://chatgpt.com/backend-api";
const REFRESH_INTERVAL_MS = 1 * 60 * 1000; // 1 minute
const FETCH_TIMEOUT_MS = 10_000;
const STALE_CTX_ERROR_FRAGMENT = "This extension ctx is stale";
const MONTHLY_RESET_DAY = 4;

interface CodexTokens {
    account_id: string;
    access_token: string;
    refresh_token?: string;
}

interface OpenAICodexAuth {
    access: string;
    accountId: string;
    refresh?: string;
    [key: string]: unknown;
}

interface PiAgentAuth {
    "openai-codex"?: OpenAICodexAuth;
    [key: string]: unknown;
}

interface MonthlyUsageResponse {
    effective_monthly_limit: {
        limit: number;
        enforcement_mode: string;
        budgetResetAt?: number | string;
        budget_reset_at?: number | string;
        resetAt?: number | string;
        resetsAt?: number | string;
        reset_at?: number | string;
        resets_at?: number | string;
        next_reset_at?: number | string;
        expires_at?: number | string;
    };
    current_month_usage: number;
    budgetResetAt?: number | string;
    budget_reset_at?: number | string;
    resetAt?: number | string;
    resetsAt?: number | string;
    reset_at?: number | string;
    resets_at?: number | string;
    next_reset_at?: number | string;
    expires_at?: number | string;
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
        const parsed = JSON.parse(raw) as PiAgentAuth;
        const auth = parsed?.["openai-codex"];
        if (!auth?.accountId || !auth?.access) return null;

        return {
            account_id: auth.accountId,
            access_token: auth.access,
            ...(auth.refresh ? { refresh_token: auth.refresh } : {}),
        };
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

function formatDisplayNumber(value: number): string {
    if (!Number.isFinite(value)) return "0";
    const rounded = Math.round(value * 100) / 100;
    return rounded
        .toFixed(2)
        .replace(/\.0+$/, "")
        .replace(/(\.\d*[1-9])0+$/, "$1");
}

function formatPercent(value: number): string {
    return `${formatDisplayNumber(Math.max(0, Math.min(100, value)))}%`;
}

function parseResetDate(value: unknown): Date | null {
    if (value instanceof Date) {
        return Number.isFinite(value.getTime()) ? value : null;
    }

    if (typeof value === "number" && Number.isFinite(value)) {
        const millis = value >= 1_000_000_000_000 ? value : value * 1000;
        const date = new Date(millis);
        return Number.isFinite(date.getTime()) ? date : null;
    }

    if (typeof value === "string") {
        const trimmed = value.trim();
        if (!trimmed) return null;
        if (/^\d+(?:\.\d+)?$/.test(trimmed))
            return parseResetDate(Number(trimmed));
        const date = new Date(trimmed);
        return Number.isFinite(date.getTime()) ? date : null;
    }

    return null;
}

function extractResetDate(data: MonthlyUsageResponse | null): Date | null {
    const candidates = [data, data?.effective_monthly_limit] as Array<
        Record<string, unknown> | null | undefined
    >;

    for (const candidate of candidates) {
        if (!candidate) continue;
        for (const key of [
            "budgetResetAt",
            "budget_reset_at",
            "resetAt",
            "resetsAt",
            "reset_at",
            "resets_at",
            "next_reset_at",
            "expires_at",
        ]) {
            const parsed = parseResetDate(candidate[key]);
            if (parsed) return parsed;
        }
    }

    const now = new Date();
    const thisMonthReset = new Date(
        now.getFullYear(),
        now.getMonth(),
        MONTHLY_RESET_DAY,
    );
    if (now.getTime() < thisMonthReset.getTime()) return thisMonthReset;
    return new Date(now.getFullYear(), now.getMonth() + 1, MONTHLY_RESET_DAY);
}

function formatRemainingDuration(resetAt: Date | null): string | null {
    if (!resetAt) return null;
    const diffMs = resetAt.getTime() - Date.now();
    if (!Number.isFinite(diffMs) || diffMs <= 0) return null;

    const hourMs = 60 * 60 * 1000;
    const dayMs = 24 * hourMs;
    const minuteMs = 60 * 1000;

    if (diffMs >= dayMs) return `${Math.ceil(diffMs / dayMs)}d remain`;
    if (diffMs >= hourMs) return `${Math.ceil(diffMs / hourMs)}h remain`;
    return `${Math.max(1, Math.ceil(diffMs / minuteMs))}m remain`;
}

function formatStatus(data: MonthlyUsageResponse | null): string {
    const used = data?.current_month_usage;
    const limit = data?.effective_monthly_limit?.limit;
    if (typeof used !== "number" || typeof limit !== "number") {
        return "| gpt usage ?";
    }

    const pct = limit > 0 ? (used / limit) * 100 : 0;
    const remaining = formatRemainingDuration(extractResetDate(data));
    const detail = remaining
        ? `${formatPercent(pct)}, ${remaining}`
        : formatPercent(pct);
    return `| gpt ${formatDisplayNumber(used)}/${formatDisplayNumber(limit)} (${detail})`;
}

function isStaleCtxError(err: unknown): boolean {
    return (
        err instanceof Error && err.message.includes(STALE_CTX_ERROR_FRAGMENT)
    );
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
                "[gpt-monthly-usage] setStatus error:",
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
            warnOnce("refresh", "[gpt-monthly-usage] refresh error:", err);
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
                    console.warn(
                        "[gpt-monthly-usage] interval refresh error:",
                        err,
                    ),
                );
            }, REFRESH_INTERVAL_MS);
        } catch (err) {
            console.warn("[gpt-monthly-usage] session_start error:", err);
        }
    });

    pi.on("turn_end", async (_event: unknown, ctx: ExtensionContext) => {
        try {
            const session = currentSession;
            if (!session || session.ctx !== ctx || !isSessionActive(session))
                return;
            await refresh(session);
        } catch (err) {
            console.warn("[gpt-monthly-usage] turn_end error:", err);
        }
    });

    pi.on(
        "session_shutdown",
        async (_event: unknown, ctx: ExtensionContext) => {
            try {
                const session = currentSession;
                if (!session || session.ctx !== ctx) return;

                currentGeneration += 1;
                deactivateSession(session);
            } catch (err) {
                console.warn(
                    "[gpt-monthly-usage] session_shutdown error:",
                    err,
                );
            }
        },
    );
}
