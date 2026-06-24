/**
 * Claude remaining usage in Pi status line.
 * Mirrors llm-usage-dashboard auth discovery and LiteLLM usage paths.
 */

import type {
    ExtensionAPI,
    ExtensionContext,
} from "@earendil-works/pi-coding-agent";
import { execFile } from "node:child_process";
import { createHmac, createHash } from "node:crypto";
import { access, mkdir, readFile, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

const STATUS_KEY = "claude-remaining-usage";
const DEFAULT_BASE_URL = "https://llm-dashboard.onkakao.net";
const DEFAULT_AWS_PROFILE = "bedrock-gateway";
const DEFAULT_AWS_REGION = "ap-northeast-2";
const DEFAULT_TOKEN_SERVICE_REGION = "ap-northeast-2";
const DEFAULT_TOKEN_SERVICE_URL =
    "https://0d38x9ga18.execute-api.ap-northeast-2.amazonaws.com/v1/auth/token";
const DEFAULT_TOKEN_TTL_SECONDS = 300;
const QUOTA_STATS_PATH = "/v1/quota-stats";
const KEY_INFO_PATH = "/key/info";
const USER_INFO_PATH = "/user/info";
const DAILY_ACTIVITY_PATH = "/user/daily/activity";
const REFRESH_INTERVAL_MS = 60_000;
const FETCH_TIMEOUT_MS = 10_000;
const STALE_CTX_ERROR_FRAGMENT = "This extension ctx is stale";

interface QuotaWindow {
    remaining_percent?: number;
    remainingPercent?: number;
    window?: string;
    name?: string;
    time_period?: string;
    period?: string;
    reset_time?: number | string;
    resetAt?: number | string;
    resetsAt?: number | string;
    reset_at?: number | string;
    resets_at?: number | string;
    next_reset_at?: number | string;
    expires?: number | string;
    expires_at?: number | string;
    budgetResetAt?: number | string;
    budget_reset_at?: number | string;
}

interface QuotaProvider {
    quota_groups?: QuotaWindow[];
}

interface QuotaStatsResponse {
    providers?: Record<string, QuotaProvider>;
    summary?: {
        remaining_percent?: number;
        remainingPercent?: number;
        resetAt?: number | string;
        resetsAt?: number | string;
        reset_at?: number | string;
        resets_at?: number | string;
        next_reset_at?: number | string;
        expires_at?: number | string;
        budgetResetAt?: number | string;
        budget_reset_at?: number | string;
    };
}

interface KeyInfoResponse {
    info?: {
        spend?: number;
        max_budget?: number;
        resetAt?: number | string;
        resetsAt?: number | string;
        reset_at?: number | string;
        resets_at?: number | string;
        next_reset_at?: number | string;
        expires?: number | string;
        expires_at?: number | string;
        budgetResetAt?: number | string;
        budget_reset_at?: number | string;
    };
}

interface UserInfoResponse {
    user_info?: {
        spend?: number;
        max_budget?: number;
        resetAt?: number | string;
        resetsAt?: number | string;
        reset_at?: number | string;
        resets_at?: number | string;
        next_reset_at?: number | string;
        expires_at?: number | string;
        budgetResetAt?: number | string;
        budget_reset_at?: number | string;
    };
}

interface DailyActivityResponse {
    metadata?: {
        total_spend?: number;
    };
}

interface RefreshSessionState {
    generation: number;
    active: boolean;
    ctx: ExtensionContext;
    intervalHandle: ReturnType<typeof setInterval> | null;
}

interface AwsCredentials {
    accessKey: string;
    secretKey: string;
    sessionToken: string;
}

let cachedToken: string | null = null;
let cachedTokenExpiresAt = 0;

function env(name: string, fallback: string): string {
    return process.env[name] || fallback;
}

function envInt(name: string, fallback: number): number {
    const parsed = Number.parseInt(process.env[name] || "", 10);
    return Number.isFinite(parsed) ? parsed : fallback;
}

function baseUrl(): string {
    return env(
        "LLM_DASHBOARD_URL",
        env("LITELLM_BASE_URL", DEFAULT_BASE_URL),
    ).replace(/\/+$/, "");
}

function awsProfile(): string {
    return env("AWS_PROFILE", DEFAULT_AWS_PROFILE);
}

function awsRegion(): string {
    return env("AWS_REGION", DEFAULT_AWS_REGION);
}

function tokenServiceRegion(): string {
    return env("TOKEN_SERVICE_REGION", DEFAULT_TOKEN_SERVICE_REGION);
}

function tokenServiceUrl(): string {
    return env("TOKEN_SERVICE_URL", DEFAULT_TOKEN_SERVICE_URL);
}

function tokenTtlMs(): number {
    return (
        envInt("PI_BEDROCK_TOKEN_CACHE_TTL", DEFAULT_TOKEN_TTL_SECONDS) * 1000
    );
}

function formatDate(date: Date): string {
    return date.toISOString().slice(0, 10);
}

function defaultDateRange(): { start: string; end: string } {
    const end = new Date();
    const start = new Date(end);
    start.setDate(start.getDate() - 29);
    return { start: formatDate(start), end: formatDate(end) };
}

function statusUnknown(): string {
    return "[claude usage ?]";
}

function isStaleCtxError(err: unknown): boolean {
    return (
        err instanceof Error && err.message.includes(STALE_CTX_ERROR_FRAGMENT)
    );
}

function asRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object"
        ? (value as Record<string, unknown>)
        : {};
}

function asNumber(value: unknown): number | null {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string") {
        const parsed = Number.parseFloat(value);
        return Number.isFinite(parsed) ? parsed : null;
    }
    return null;
}

function safeName(value: string): string {
    return value.replace(/[^A-Za-z0-9._-]/g, "_");
}

function expandHomePath(value: string): string {
    return value === "~" || value.startsWith("~/")
        ? join(homedir(), value.slice(2))
        : value;
}

function cacheFilePath(): string {
    return join(
        homedir(),
        ".cache",
        "llm-usage-dashboard",
        `${safeName(`${awsProfile()}_${awsRegion()}_${tokenServiceRegion()}`)}.token`,
    );
}

async function isExecutable(path: string): Promise<boolean> {
    try {
        await access(path, 0o111);
        return true;
    } catch {
        return false;
    }
}

function findHelperInJson(value: unknown): string | null {
    if (Array.isArray(value)) {
        for (const item of value) {
            const found = findHelperInJson(item);
            if (found) return found;
        }
        return null;
    }

    if (!value || typeof value !== "object") return null;
    const record = value as Record<string, unknown>;

    for (const key of [
        "apiKeyHelper",
        "inferenceCredentialHelper",
        "credentialHelper",
    ]) {
        const helper = record[key];
        if (typeof helper === "string" && helper.trim()) return helper;
    }

    for (const nested of Object.values(record)) {
        const found = findHelperInJson(nested);
        if (found) return found;
    }

    return null;
}

async function discoverHelper(): Promise<string | null> {
    const envHelper = process.env.GATEWAY_TOKEN_HELPER;
    if (envHelper) {
        const expanded = expandHomePath(envHelper);
        if (await isExecutable(expanded)) return expanded;
    }

    for (const settingsPath of [
        join(homedir(), ".claude", "settings.json"),
        join(
            homedir(),
            "Library",
            "Application Support",
            "Claude",
            "claude_desktop_config.json",
        ),
    ]) {
        try {
            const raw = await readFile(settingsPath, "utf8");
            const found = findHelperInJson(JSON.parse(raw));
            const expanded = found ? expandHomePath(found) : null;
            if (expanded && (await isExecutable(expanded))) return expanded;
        } catch {
            // ignore
        }
    }

    for (const helperPath of [
        join(
            homedir(),
            "Projects",
            "aws-claude-code-gateway",
            "bin",
            "gateway-token",
        ),
        join(homedir(), "claude-code-gateway", "gateway-token"),
    ]) {
        if (await isExecutable(helperPath)) return helperPath;
    }

    return null;
}

async function loadDiskCachedToken(): Promise<string | null> {
    try {
        const raw = await readFile(cacheFilePath(), "utf8");
        const [expiresRaw, token] = raw.split("\n");
        const expiresAt = Number.parseInt(expiresRaw || "", 10) * 1000;
        if (!token || !Number.isFinite(expiresAt) || Date.now() >= expiresAt)
            return null;
        return token.trim() || null;
    } catch {
        return null;
    }
}

async function saveDiskCachedToken(token: string): Promise<void> {
    const ttl = tokenTtlMs();
    if (ttl <= 0) return;

    const path = cacheFilePath();
    await mkdir(join(homedir(), ".cache", "llm-usage-dashboard"), {
        recursive: true,
    });
    const expiresAtSeconds = Math.floor((Date.now() + ttl) / 1000);
    await writeFile(path, `${expiresAtSeconds}\n${token}\n`, { mode: 0o600 });
}

async function runHelper(path: string): Promise<string | null> {
    try {
        const { stdout } = await execFileAsync(path, {
            env: {
                ...process.env,
                AWS_PROFILE: awsProfile(),
                AWS_REGION: awsRegion(),
                PI_BEDROCK_TOKEN_CACHE_TTL: String(
                    Math.floor(tokenTtlMs() / 1000),
                ),
            },
        });
        const token = stdout.trim();
        return token.startsWith("sk-") ? token : null;
    } catch {
        return null;
    }
}

async function exportAwsCredentials(): Promise<AwsCredentials | null> {
    try {
        const { stdout } = await execFileAsync(
            "aws",
            ["configure", "export-credentials", "--format", "env"],
            {
                env: { ...process.env, AWS_PROFILE: awsProfile() },
            },
        );
        const vars = Object.fromEntries(
            stdout
                .split(/\r?\n/)
                .map((line) => line.trim())
                .filter(Boolean)
                .map((line) => line.replace(/^export\s+/, ""))
                .map((line) => {
                    const idx = line.indexOf("=");
                    return idx >= 0
                        ? [line.slice(0, idx), line.slice(idx + 1)]
                        : ["", ""];
                })
                .filter(([key]) => key),
        );

        const accessKey = vars.AWS_ACCESS_KEY_ID;
        const secretKey = vars.AWS_SECRET_ACCESS_KEY;
        const sessionToken = vars.AWS_SESSION_TOKEN;
        if (!accessKey || !secretKey || !sessionToken) return null;
        return { accessKey, secretKey, sessionToken };
    } catch {
        return null;
    }
}

function sha256Hex(value: string | Buffer): string {
    return createHash("sha256").update(value).digest("hex");
}

function hmac(key: Buffer | string, value: string): Buffer {
    return createHmac("sha256", key).update(value).digest();
}

function signAwsRequest(
    req: Request,
    body: string,
    creds: AwsCredentials,
): void {
    const now = new Date();
    const amzDate =
        now
            .toISOString()
            .replace(/[-:]|\.\d{3}/g, "")
            .slice(0, 15) + "Z";
    const dateStamp = amzDate.slice(0, 8);
    const payloadHash = sha256Hex(body);
    const url = new URL(req.url);

    req.headers.set("content-type", "application/json");
    req.headers.set("host", url.host);
    req.headers.set("x-amz-content-sha256", payloadHash);
    req.headers.set("x-amz-date", amzDate);
    req.headers.set("x-bedrock-region", awsRegion());
    req.headers.set("x-amz-security-token", creds.sessionToken);

    const signedHeaderNames = [
        "content-type",
        "host",
        "x-amz-content-sha256",
        "x-amz-date",
        "x-amz-security-token",
        "x-bedrock-region",
    ].sort();
    const canonicalHeaders = signedHeaderNames
        .map((name) => `${name}:${req.headers.get(name)?.trim() || ""}\n`)
        .join("");
    const canonicalRequest = [
        req.method,
        url.pathname,
        url.search.slice(1),
        canonicalHeaders,
        signedHeaderNames.join(";"),
        payloadHash,
    ].join("\n");
    const scope = `${dateStamp}/${tokenServiceRegion()}/execute-api/aws4_request`;
    const stringToSign = [
        "AWS4-HMAC-SHA256",
        amzDate,
        scope,
        sha256Hex(canonicalRequest),
    ].join("\n");
    const kDate = hmac(`AWS4${creds.secretKey}`, dateStamp);
    const kRegion = hmac(kDate, tokenServiceRegion());
    const kService = hmac(kRegion, "execute-api");
    const kSigning = hmac(kService, "aws4_request");
    const signature = createHmac("sha256", kSigning)
        .update(stringToSign)
        .digest("hex");

    req.headers.set(
        "authorization",
        `AWS4-HMAC-SHA256 Credential=${creds.accessKey}/${scope}, SignedHeaders=${signedHeaderNames.join(";")}, Signature=${signature}`,
    );
}

async function fetchTokenFromService(): Promise<string | null> {
    const creds = await exportAwsCredentials();
    if (!creds) return null;

    try {
        const body = "{}";
        const req = new Request(tokenServiceUrl(), { method: "POST", body });
        signAwsRequest(req, body, creds);

        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
        const res = await fetch(req, { signal: controller.signal }).finally(
            () => clearTimeout(timer),
        );
        if (!res.ok) return null;

        const json = asRecord((await res.json()) as unknown);
        const token = typeof json.token === "string" ? json.token : "";
        return token.startsWith("sk-") ? token : null;
    } catch {
        return null;
    }
}

async function loadToken(): Promise<string | null> {
    if (cachedToken && Date.now() < cachedTokenExpiresAt) return cachedToken;

    for (const candidate of [
        process.env.AWS_BEARER_TOKEN_BEDROCK,
        process.env.ANTHROPIC_API_KEY,
    ]) {
        if (candidate?.startsWith("sk-")) {
            cachedToken = candidate;
            cachedTokenExpiresAt = Date.now() + tokenTtlMs();
            return candidate;
        }
    }

    const helper = await discoverHelper();
    if (helper) {
        const token = await runHelper(helper);
        if (token) {
            cachedToken = token;
            cachedTokenExpiresAt = Date.now() + tokenTtlMs();
            await saveDiskCachedToken(token).catch(() => undefined);
            return token;
        }
    }

    const diskToken = await loadDiskCachedToken();
    if (diskToken?.startsWith("sk-")) {
        cachedToken = diskToken;
        cachedTokenExpiresAt = Date.now() + tokenTtlMs();
        return diskToken;
    }

    const serviceToken = await fetchTokenFromService();
    if (serviceToken) {
        cachedToken = serviceToken;
        cachedTokenExpiresAt = Date.now() + tokenTtlMs();
        await saveDiskCachedToken(serviceToken).catch(() => undefined);
        return serviceToken;
    }

    return null;
}

async function fetchJson<T>(url: URL, token: string): Promise<T | null> {
    try {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
        const res = await fetch(url, {
            headers: { Authorization: `Bearer ${token}` },
            signal: controller.signal,
        }).finally(() => clearTimeout(timer));
        if (!res.ok) return null;
        return (await res.json()) as T;
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

function extractIsoResetDate(text: string): Date | null {
    const match = text.match(
        /\breset\b[^\d]*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2}))\b/i,
    );
    if (!match) return null;
    const date = new Date(match[1]);
    return Number.isFinite(date.getTime()) ? date : null;
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
        if (/^\d+(?:\.\d+)?$/.test(trimmed)) {
            return parseResetDate(Number(trimmed));
        }
        const date = new Date(trimmed);
        if (Number.isFinite(date.getTime())) return date;
        return extractIsoResetDate(trimmed);
    }

    return null;
}

function extractResetDateFromKeys(value: unknown, keys: string[]): Date | null {
    const record = asRecord(value);
    for (const key of keys) {
        const parsed = parseResetDate(record[key]);
        if (parsed) return parsed;
    }
    return null;
}

function extractResetDate(value: unknown): Date | null {
    const record = asRecord(value);
    const parsed = extractResetDateFromKeys(record, [
        "reset_time",
        "budgetResetAt",
        "budget_reset_at",
        "resetAt",
        "resetsAt",
        "reset_at",
        "resets_at",
        "nextResetAt",
        "next_reset_at",
        "reset",
        "expires",
        "expires_at",
    ]);
    if (parsed) return parsed;

    for (const candidate of Object.values(record)) {
        if (typeof candidate !== "string") continue;
        const extracted = extractIsoResetDate(candidate);
        if (extracted) return extracted;
    }

    return null;
}

function extractDashboardFallbackResetDate(
    keyInfo: KeyInfoResponse["info"] | null | undefined,
    userInfo: UserInfoResponse["user_info"] | null | undefined,
): Date | null {
    return (
        extractResetDateFromKeys(userInfo, [
            "budgetResetAt",
            "budget_reset_at",
        ]) ??
        extractResetDateFromKeys(keyInfo, [
            "budgetResetAt",
            "budget_reset_at",
            "expires",
            "expires_at",
        ]) ??
        null
    );
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

function formatClaudeUsageStatus(
    used: number,
    limit: number,
    resetAt?: Date | null,
): string {
    const safeUsed = Math.max(0, used);
    const safeLimit = Math.max(0, limit);
    const pct = safeLimit > 0 ? (safeUsed / safeLimit) * 100 : 0;
    const remaining = formatRemainingDuration(resetAt ?? null);
    const detail = remaining
        ? `${formatPercent(pct)}, ${remaining}`
        : formatPercent(pct);
    return `[claude ${formatDisplayNumber(safeUsed)}/${formatDisplayNumber(safeLimit)} (${detail})]`;
}

function quotaWindowLabel(window: QuotaWindow): string {
    for (const value of [
        window.window,
        window.name,
        window.time_period,
        window.period,
    ]) {
        if (typeof value === "string" && value.trim()) return value.trim();
    }
    return "";
}

function quotaWindowPercent(window: QuotaWindow): number | null {
    return (
        asNumber(window.remaining_percent) ?? asNumber(window.remainingPercent)
    );
}

function formatQuotaStatus(data: QuotaStatsResponse | null): string | null {
    if (!data?.providers || typeof data.providers !== "object") return null;

    const providers = Object.values(data.providers);
    const provider =
        providers.find((item) => Array.isArray(item?.quota_groups)) ||
        providers[0];
    const windows = Array.isArray(provider?.quota_groups)
        ? provider.quota_groups
        : [];

    const preferred =
        windows.find((window) =>
            quotaWindowLabel(window).toLowerCase().includes("5h"),
        ) || windows[0];
    const preferredRemainingPct = preferred
        ? quotaWindowPercent(preferred)
        : null;
    if (preferredRemainingPct !== null) {
        return formatClaudeUsageStatus(
            100 - preferredRemainingPct,
            100,
            extractResetDate(preferred),
        );
    }

    const summaryRemainingPct =
        asNumber(data.summary?.remaining_percent) ??
        asNumber(data.summary?.remainingPercent);
    return summaryRemainingPct !== null
        ? formatClaudeUsageStatus(
              100 - summaryRemainingPct,
              100,
              extractResetDate(data.summary),
          )
        : null;
}

function formatUsageFallback(
    keyInfo: KeyInfoResponse | null,
    userInfo: UserInfoResponse | null,
    activity: DailyActivityResponse | null,
): string | null {
    const keySpend = asNumber(keyInfo?.info?.spend) ?? 0;
    const userSpend = asNumber(userInfo?.user_info?.spend) ?? 0;
    const rangeSpend = asNumber(activity?.metadata?.total_spend) ?? 0;
    const budget =
        asNumber(keyInfo?.info?.max_budget) ??
        asNumber(userInfo?.user_info?.max_budget);
    if (budget === null || budget <= 0) return null;

    const conservativeSpend = Math.max(keySpend, userSpend, rangeSpend);
    const resetAt = extractDashboardFallbackResetDate(
        keyInfo?.info,
        userInfo?.user_info,
    );
    return formatClaudeUsageStatus(conservativeSpend, budget, resetAt);
}

async function fetchClaudeUsageStatus(): Promise<string> {
    const token = await loadToken();
    if (!token) return statusUnknown();

    const dashboardBaseUrl = baseUrl();
    const quotaStatsUrl = new URL(QUOTA_STATS_PATH, dashboardBaseUrl);
    const quotaStats = await fetchJson<QuotaStatsResponse>(
        quotaStatsUrl,
        token,
    );
    const quotaStatus = formatQuotaStatus(quotaStats);
    if (quotaStatus) return quotaStatus;

    const { start, end } = defaultDateRange();
    const activityUrl = new URL(DAILY_ACTIVITY_PATH, dashboardBaseUrl);
    activityUrl.searchParams.set("start_date", start);
    activityUrl.searchParams.set("end_date", end);

    const [keyInfo, userInfo, activity] = await Promise.all([
        fetchJson<KeyInfoResponse>(
            new URL(KEY_INFO_PATH, dashboardBaseUrl),
            token,
        ),
        fetchJson<UserInfoResponse>(
            new URL(USER_INFO_PATH, dashboardBaseUrl),
            token,
        ),
        fetchJson<DailyActivityResponse>(activityUrl, token),
    ]);

    return formatUsageFallback(keyInfo, userInfo, activity) || statusUnknown();
}

export default function (pi: ExtensionAPI) {
    try {
        let lastFetchAt = 0;
        let currentGeneration = 0;
        let currentSession: RefreshSessionState | null = null;
        let lastStatus = statusUnknown();
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

        function safeSetStatus(ctx: ExtensionContext, status: string): void {
            try {
                ctx.ui.setStatus(STATUS_KEY, status);
            } catch (err) {
                if (isStaleCtxError(err)) throw err;
                warnOnce(
                    "safeSetStatus",
                    "[claude-remaining-usage] setStatus error:",
                    err,
                );
            }
        }

        function setStatusIfActive(
            session: RefreshSessionState,
            status: string,
        ): void {
            if (!isSessionActive(session)) return;
            try {
                safeSetStatus(session.ctx, status);
            } catch (err) {
                if (isStaleCtxError(err)) {
                    if (isSessionActive(session)) deactivateSession(session);
                    return;
                }
                warnOnce(
                    "setStatusIfActive",
                    "[claude-remaining-usage] setStatusIfActive error:",
                    err,
                );
            }
        }

        async function refresh(session: RefreshSessionState): Promise<void> {
            try {
                if (!isSessionActive(session)) return;

                const now = Date.now();
                if (now - lastFetchAt < REFRESH_INTERVAL_MS) {
                    setStatusIfActive(session, lastStatus);
                    return;
                }
                lastFetchAt = now;

                lastStatus = await fetchClaudeUsageStatus().catch((err) => {
                    warnOnce(
                        "fetchClaudeUsageStatus",
                        "[claude-remaining-usage] fetch usage error:",
                        err,
                    );
                    return statusUnknown();
                });
                if (!isSessionActive(session)) return;
                setStatusIfActive(session, lastStatus);
            } catch (err) {
                warnOnce(
                    "refresh",
                    "[claude-remaining-usage] refresh error:",
                    err,
                );
                if (isSessionActive(session))
                    setStatusIfActive(session, lastStatus || statusUnknown());
            }
        }

        function registerHandler(
            event: "session_start" | "turn_end" | "session_shutdown",
            handler: (_event: unknown, ctx: ExtensionContext) => Promise<void>,
        ): void {
            try {
                pi.on(
                    event,
                    (_event: unknown, ctx: ExtensionContext) =>
                        void handler(_event, ctx).catch((err) => {
                            console.warn(
                                `[claude-remaining-usage] ${event} error:`,
                                err,
                            );
                            try {
                                safeSetStatus(ctx, statusUnknown());
                            } catch {
                                // ignore
                            }
                        }),
                );
            } catch (err) {
                warnOnce(
                    `register:${event}`,
                    `[claude-remaining-usage] register ${event} error:`,
                    err,
                );
            }
        }

        registerHandler(
            "session_start",
            async (_event: unknown, ctx: ExtensionContext) => {
                if (currentSession) deactivateSession(currentSession);

                const session: RefreshSessionState = {
                    generation: ++currentGeneration,
                    active: true,
                    ctx,
                    intervalHandle: null,
                };
                currentSession = session;
                lastFetchAt = 0;
                lastStatus = statusUnknown();
                setStatusIfActive(session, lastStatus);
                await refresh(session);
                if (!isSessionActive(session)) return;

                session.intervalHandle = setInterval(() => {
                    try {
                        if (!isSessionActive(session)) return;
                        lastFetchAt = 0;
                        void refresh(session).catch((err) =>
                            console.warn(
                                "[claude-remaining-usage] interval refresh error:",
                                err,
                            ),
                        );
                    } catch (err) {
                        warnOnce(
                            "interval",
                            "[claude-remaining-usage] interval error:",
                            err,
                        );
                    }
                }, REFRESH_INTERVAL_MS);
            },
        );

        registerHandler(
            "turn_end",
            async (_event: unknown, ctx: ExtensionContext) => {
                const session = currentSession;
                if (
                    !session ||
                    session.ctx !== ctx ||
                    !isSessionActive(session)
                )
                    return;
                await refresh(session);
            },
        );

        registerHandler(
            "session_shutdown",
            async (_event: unknown, ctx: ExtensionContext) => {
                const session = currentSession;
                if (!session || session.ctx !== ctx) return;

                currentGeneration += 1;
                deactivateSession(session);
            },
        );
    } catch (err) {
        console.warn("[claude-remaining-usage] extension init error:", err);
    }
}
