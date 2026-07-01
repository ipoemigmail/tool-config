import {
    getAgentDir,
    type ExtensionAPI,
    type ExtensionContext,
} from "@earendil-works/pi-coding-agent";
import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import { basename, dirname, extname, join } from "node:path";

const STATUS_KEY = "zmodel-profile";
const PROFILES_PATH = join(getAgentDir(), "model-profiles.json");
const GLOBAL_SETTINGS_PATH = join(getAgentDir(), "settings.json");
const PROJECT_SETTINGS_PATH = ".pi/settings.json";
const SESSION_SETTINGS_FALLBACK_NAME = "subagents-settings.json";
const SESSION_SETTINGS_SUFFIX = ".subagents-settings.json";
const STALE_CTX_ERROR_FRAGMENT = "This extension ctx is stale";
const JSON_INDENT_SPACES = 2;
const INVALID_PROFILE_STATUS = "(invalid)";
const INHERITED_MODEL_LABEL = "기본값 상속";
const SESSION_SCOPE_LABEL = "session";
const PROJECT_SCOPE_LABEL = "project";
const GLOBAL_SCOPE_LABEL = "global";
const GLOBAL_FLAG = "--global";
const PROJECT_FLAG = "--project";
const LOCAL_FLAG = "--local";
const VALID_THINKING_LEVELS = ["off", "minimal", "low", "medium", "high", "xhigh"] as const;
const BUILTIN_AGENT_NAMES = [
    "scout",
    "worker",
    "reviewer",
    "planner",
    "oracle",
    "context-builder",
    "researcher",
    "delegate",
] as const;

type BuiltinAgentName = (typeof BUILTIN_AGENT_NAMES)[number];
type ThinkingLevel = (typeof VALID_THINKING_LEVELS)[number];
type ProfileScope = "session" | "project" | "global";

type BuiltinEntryRaw = string | { model: string; thinking?: string };

interface BuiltinEntry {
    model: string;
    thinking?: ThinkingLevel;
}

type AgentOverrideEntry = { model: string; thinking?: ThinkingLevel };
type BuiltinProfileRaw = Record<BuiltinAgentName, BuiltinEntryRaw>;
type ProfilesByName = Record<string, BuiltinProfileRaw>;
type AgentOverrides = Record<string, AgentOverrideEntry>;

interface ModelProfilesFile {
    active: string;
    profiles: ProfilesByName;
}

interface SettingsFile {
    subagents?: {
        agentOverrides?: AgentOverrides;
        [key: string]: unknown;
    };
    [key: string]: unknown;
}

interface SessionProfileFile {
    active?: string;
    subagents?: {
        agentOverrides?: AgentOverrides;
        [key: string]: unknown;
    };
    [key: string]: unknown;
}

interface SessionState {
    generation: number;
    active: boolean;
    ctx: ExtensionContext;
}

interface SettingsReadResult<T> {
    status: "ok" | "missing" | "invalid";
    data: T;
}

interface ParsedProfileCommandArgs {
    profileName: string;
    scope: ProfileScope;
}

interface SessionSidecarPathResult {
    path: string;
    fallbackPath: string;
}

interface ProfileScopeState {
    scope: ProfileScope;
    label: string;
    active?: string;
    overrides: AgentOverrides;
}

function asRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : {};
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
    return value !== null && typeof value === "object" && !Array.isArray(value);
}

function getProjectSettingsPath(ctx: ExtensionContext): string {
    return join(ctx.cwd, PROJECT_SETTINGS_PATH);
}

function getScopeLabel(scope: ProfileScope): string {
    switch (scope) {
        case "session":
            return SESSION_SCOPE_LABEL;
        case "project":
            return PROJECT_SCOPE_LABEL;
        default:
            return GLOBAL_SCOPE_LABEL;
    }
}

function parseProfileCommandArgs(args: string): ParsedProfileCommandArgs {
    const tokens = args
        .split(/\s+/)
        .map((token) => token.trim())
        .filter(Boolean);
    const scope = tokens.includes(GLOBAL_FLAG)
        ? "global"
        : tokens.includes(PROJECT_FLAG) || tokens.includes(LOCAL_FLAG)
            ? "project"
            : "session";
    const profileName = tokens
        .filter((token) => ![GLOBAL_FLAG, PROJECT_FLAG, LOCAL_FLAG].includes(token))
        .join(" ");
    return { profileName, scope };
}

function isStaleCtxError(err: unknown): boolean {
    return (
        err instanceof Error && err.message.includes(STALE_CTX_ERROR_FRAGMENT)
    );
}

function isValidThinkingLevel(value: string): value is ThinkingLevel {
    return (VALID_THINKING_LEVELS as readonly string[]).includes(value);
}

function isBuiltinEntry(value: unknown): value is BuiltinEntryRaw {
    if (typeof value === "string") return value.trim().length > 0;
    const record = asRecord(value);
    return typeof record.model === "string" && (record.model as string).trim().length > 0;
}

function parseBuiltinEntry(raw: BuiltinEntryRaw): BuiltinEntry {
    if (typeof raw === "string") return { model: raw };
    const r = raw as { model: string; thinking?: string };
    const { model, thinking } = r;
    if (thinking !== undefined && !isValidThinkingLevel(thinking)) {
        console.warn(`[model-profile] unknown thinking level "${thinking}", ignoring`);
        return { model };
    }
    return thinking ? { model, thinking: thinking as ThinkingLevel } : { model };
}

function isBuiltinProfile(value: unknown): value is BuiltinProfileRaw {
    const record = asRecord(value);
    return BUILTIN_AGENT_NAMES.every((name) => isBuiltinEntry(record[name]));
}

function isModelProfilesFile(value: unknown): value is ModelProfilesFile {
    const record = asRecord(value);
    if (typeof record.active !== "string") return false;

    const profiles = asRecord(record.profiles);
    const names = Object.keys(profiles);
    if (names.length === 0) return false;

    return names.every((name) => isBuiltinProfile(profiles[name]));
}

function listProfileNames(profiles: ModelProfilesFile): string[] {
    return Object.keys(profiles.profiles).sort();
}

function formatProfileList(names: string[]): string {
    return names.join(", ");
}

function formatCurrentModels(overrides: AgentOverrides): string {
    const lines = BUILTIN_AGENT_NAMES.map((name) => {
        const entry = overrides[name];
        if (!entry?.model) return `  ${name}: ${INHERITED_MODEL_LABEL}`;
        const thinkingLabel = entry.thinking ? ` (thinking: ${entry.thinking})` : "";
        return `  ${name}: ${entry.model}${thinkingLabel}`;
    });
    return `빌트인 모델:\n${lines.join("\n")}`;
}

function formatStatusValue(profileName: string, scope: ProfileScope): string {
    return `${profileName} (${getScopeLabel(scope)})`;
}

function setStatus(ctx: ExtensionContext, active: string | undefined): void {
    if (!active) return;
    ctx.ui.setStatus(STATUS_KEY, `| profile: ${active}`);
}

function safeSetStatus(ctx: ExtensionContext, active: string | undefined): void {
    if (!active) return;
    try {
        setStatus(ctx, active);
    } catch (err) {
        if (isStaleCtxError(err)) return;
        console.warn("[model-profile] failed to set status:", err);
    }
}

async function notify(ctx: ExtensionContext, message: string): Promise<void> {
    try {
        await ctx.ui.notify?.(message);
    } catch (err) {
        if (isStaleCtxError(err)) return;
        console.warn("[model-profile] failed to notify:", err);
    }
}

async function readJsonFile(path: string): Promise<unknown | null> {
    try {
        const raw = await readFile(path, "utf8");
        return JSON.parse(raw) as unknown;
    } catch {
        return null;
    }
}

async function readProfiles(): Promise<ModelProfilesFile | null> {
    const parsed = await readJsonFile(PROFILES_PATH);
    return isModelProfilesFile(parsed) ? parsed : null;
}

async function readSettingsForWrite<T extends Record<string, unknown>>(
    path: string,
): Promise<SettingsReadResult<T>> {
    try {
        const raw = await readFile(path, "utf8");
        const parsed = JSON.parse(raw) as unknown;
        return {
            status: "ok",
            data: asRecord(parsed) as T,
        };
    } catch (err) {
        const code =
            err && typeof err === "object" && "code" in err
                ? err.code
                : undefined;
        if (code === "ENOENT") {
            return { status: "missing", data: {} as T };
        }
        return { status: "invalid", data: {} as T };
    }
}

async function ensureParentDirectory(path: string): Promise<void> {
    await mkdir(dirname(path), { recursive: true });
}

async function writeProfiles(profiles: ModelProfilesFile): Promise<void> {
    await ensureParentDirectory(PROFILES_PATH);
    await writeFile(
        PROFILES_PATH,
        `${JSON.stringify(profiles, null, JSON_INDENT_SPACES)}\n`,
        "utf8",
    );
}

function buildAgentOverrides(profile: BuiltinProfileRaw): AgentOverrides {
    const overrides: AgentOverrides = {};
    for (const builtin of BUILTIN_AGENT_NAMES) {
        const entry = parseBuiltinEntry(profile[builtin]);
        const override: AgentOverrideEntry = { model: entry.model };
        if (entry.thinking) override.thinking = entry.thinking;
        overrides[builtin] = override;
    }
    return overrides;
}

function matchesProfile(
    overrides: AgentOverrides,
    profile: BuiltinProfileRaw,
): boolean {
    return BUILTIN_AGENT_NAMES.every((builtin) => {
        const expected = parseBuiltinEntry(profile[builtin]);
        const current = overrides[builtin];
        return current?.model === expected.model && current?.thinking === expected.thinking;
    });
}

function findMatchingProfileName(
    profiles: ModelProfilesFile,
    overrides: AgentOverrides,
): string | undefined {
    return listProfileNames(profiles).find((name) =>
        matchesProfile(overrides, profiles.profiles[name]),
    );
}

function cloneOverrides(overrides: AgentOverrides): AgentOverrides {
    const next: AgentOverrides = {};
    for (const [name, entry] of Object.entries(overrides)) {
        if (!entry?.model) continue;
        next[name] = entry.thinking
            ? { model: entry.model, thinking: entry.thinking }
            : { model: entry.model };
    }
    return next;
}

function mergeOverrides(...sources: AgentOverrides[]): AgentOverrides {
    const merged: AgentOverrides = {};
    for (const source of sources) {
        for (const [name, entry] of Object.entries(source)) {
            if (!entry?.model) continue;
            merged[name] = entry.thinking
                ? { model: entry.model, thinking: entry.thinking }
                : { model: entry.model };
        }
    }
    return merged;
}

function hasBuiltinOverrides(overrides: AgentOverrides): boolean {
    return BUILTIN_AGENT_NAMES.some((name) => Boolean(overrides[name]?.model));
}

async function writeSettingsOverrides(
    settingsPath: string,
    profile: BuiltinProfileRaw,
): Promise<boolean> {
    const settingsResult = await readSettingsForWrite<SettingsFile>(settingsPath);
    if (settingsResult.status === "invalid") return false;

    const { data: settings } = settingsResult;
    const nextSubagents = asRecord(settings.subagents);
    const nextOverrides = asRecord(nextSubagents.agentOverrides);
    const profileOverrides = buildAgentOverrides(profile);

    for (const builtin of BUILTIN_AGENT_NAMES) {
        nextOverrides[builtin] = profileOverrides[builtin];
    }

    const nextSettings: SettingsFile = {
        ...settings,
        subagents: {
            ...nextSubagents,
            agentOverrides: nextOverrides as AgentOverrides,
        },
    };

    await ensureParentDirectory(settingsPath);
    await writeFile(
        settingsPath,
        `${JSON.stringify(nextSettings, null, JSON_INDENT_SPACES)}\n`,
        "utf8",
    );

    return true;
}

function getSessionSidecarFallbackPath(ctx: ExtensionContext): string {
    return join(ctx.sessionManager.getSessionDir(), SESSION_SETTINGS_FALLBACK_NAME);
}

function buildSessionSidecarPathFromSessionFile(sessionFile: string): string {
    const extension = extname(sessionFile);
    const sessionBase = basename(sessionFile, extension);
    return join(dirname(sessionFile), `${sessionBase}${SESSION_SETTINGS_SUFFIX}`);
}

async function findSessionSidecarBySessionId(
    ctx: ExtensionContext,
    sessionId: string,
): Promise<string | undefined> {
    try {
        const sessionDir = ctx.sessionManager.getSessionDir();
        const suffix = `_${sessionId}${SESSION_SETTINGS_SUFFIX}`;
        const entries = await readdir(sessionDir, { withFileTypes: true });
        const matched = entries.find(
            (entry: { isFile(): boolean; name: string }) =>
                entry.isFile() && entry.name.endsWith(suffix),
        );
        return matched ? join(sessionDir, matched.name) : undefined;
    } catch {
        return undefined;
    }
}

async function resolveSessionSidecarPath(
    ctx: ExtensionContext,
    sessionIdHint?: string,
): Promise<SessionSidecarPathResult> {
    const fallbackPath = getSessionSidecarFallbackPath(ctx);

    if (sessionIdHint) {
        const hintedPath = await findSessionSidecarBySessionId(ctx, sessionIdHint);
        if (hintedPath) return { path: hintedPath, fallbackPath };
    }

    const sessionFile = ctx.sessionManager.getSessionFile();
    if (sessionFile) {
        return {
            path: buildSessionSidecarPathFromSessionFile(sessionFile),
            fallbackPath,
        };
    }

    return { path: fallbackPath, fallbackPath };
}

async function readSettingsOverrides(settingsPath: string): Promise<AgentOverrides> {
    const parsed = await readJsonFile(settingsPath);
    const settings = asRecord(parsed);
    const subagents = asRecord(settings.subagents);
    return cloneOverrides(asRecord(subagents.agentOverrides) as AgentOverrides);
}

async function readSessionProfileFile(
    ctx: ExtensionContext,
    sessionIdHint?: string,
): Promise<SessionProfileFile | null> {
    const sidecar = await resolveSessionSidecarPath(ctx, sessionIdHint);
    const parsed = await readJsonFile(sidecar.path);
    if (parsed !== null) return asRecord(parsed) as SessionProfileFile;
    return null;
}

async function readSessionOverrides(
    ctx: ExtensionContext,
    sessionIdHint?: string,
): Promise<AgentOverrides> {
    const sessionFile = await readSessionProfileFile(ctx, sessionIdHint);
    const subagents = asRecord(sessionFile?.subagents);
    return cloneOverrides(asRecord(subagents.agentOverrides) as AgentOverrides);
}

async function writeSessionProfile(
    ctx: ExtensionContext,
    profileName: string,
    profile: BuiltinProfileRaw,
): Promise<boolean> {
    const sidecar = await resolveSessionSidecarPath(ctx);
    const sidecarResult = await readSettingsForWrite<SessionProfileFile>(sidecar.path);
    if (sidecarResult.status === "invalid") return false;

    const existing = sidecarResult.data;
    const nextSubagents = asRecord(existing.subagents);
    const nextSidecar: SessionProfileFile = {
        ...existing,
        active: profileName,
        subagents: {
            ...nextSubagents,
            agentOverrides: buildAgentOverrides(profile),
        },
    };

    await ensureParentDirectory(sidecar.path);
    await writeFile(
        sidecar.path,
        `${JSON.stringify(nextSidecar, null, JSON_INDENT_SPACES)}\n`,
        "utf8",
    );

    return true;
}

function getResolvedProfileName(
    profiles: ModelProfilesFile,
    scope: ProfileScope,
    overrides: AgentOverrides,
    activeHint?: string,
): string | undefined {
    return findMatchingProfileName(profiles, overrides)
        ?? (scope === "global" && activeHint && profiles.profiles[activeHint] ? activeHint : undefined)
        ?? activeHint;
}

async function readScopeState(
    profiles: ModelProfilesFile,
    scope: ProfileScope,
    ctx: ExtensionContext,
    sessionIdHint?: string,
): Promise<ProfileScopeState> {
    if (scope === "session") {
        const sessionFile = await readSessionProfileFile(ctx, sessionIdHint);
        const sessionOverrides = await readSessionOverrides(ctx, sessionIdHint);
        const activeHint = typeof sessionFile?.active === "string" ? sessionFile.active : undefined;
        return {
            scope,
            label: getScopeLabel(scope),
            active: getResolvedProfileName(profiles, scope, sessionOverrides, activeHint),
            overrides: sessionOverrides,
        };
    }

    const settingsPath = scope === "project" ? getProjectSettingsPath(ctx) : GLOBAL_SETTINGS_PATH;
    const overrides = await readSettingsOverrides(settingsPath);
    const activeHint = scope === "global" ? profiles.active : undefined;
    return {
        scope,
        label: getScopeLabel(scope),
        active: getResolvedProfileName(profiles, scope, overrides, activeHint),
        overrides,
    };
}

async function readResolvedScopeState(
    profiles: ModelProfilesFile,
    ctx: ExtensionContext,
): Promise<ProfileScopeState> {
    const sessionState = await readScopeState(profiles, "session", ctx);
    if (hasBuiltinOverrides(sessionState.overrides)) return sessionState;

    const projectState = await readScopeState(profiles, "project", ctx);
    if (hasBuiltinOverrides(projectState.overrides)) return projectState;

    const globalState = await readScopeState(profiles, "global", ctx);
    if (hasBuiltinOverrides(globalState.overrides) || globalState.active) return globalState;

    return {
        scope: "global",
        label: getScopeLabel("global"),
        active: profiles.profiles[profiles.active] ? profiles.active : undefined,
        overrides: globalState.overrides,
    };
}

async function readEffectiveOverrides(
    ctx: ExtensionContext,
    parentSessionId?: string,
): Promise<AgentOverrides> {
    const globalOverrides = await readSettingsOverrides(GLOBAL_SETTINGS_PATH);
    const projectOverrides = await readSettingsOverrides(getProjectSettingsPath(ctx));
    const sessionOverrides = await readSessionOverrides(ctx, parentSessionId);
    return mergeOverrides(globalOverrides, projectOverrides, sessionOverrides);
}

async function applyProfile(
    profileName: string,
    scope: ProfileScope,
    ctx: ExtensionContext,
): Promise<void> {
    const profiles = await readProfiles();
    if (!profiles) {
        await notify(ctx, "프로필 정의를 읽지 못했어.");
        return;
    }

    const profile = profiles.profiles[profileName];
    if (!profile) {
        await notify(
            ctx,
            `없는 프로필이야: ${profileName} (가능: ${formatProfileList(listProfileNames(profiles))})`,
        );
        return;
    }

    try {
        const didWrite = scope === "session"
            ? await writeSessionProfile(ctx, profileName, profile)
            : await writeSettingsOverrides(
                scope === "global" ? GLOBAL_SETTINGS_PATH : getProjectSettingsPath(ctx),
                profile,
            );
        if (!didWrite) {
            await notify(ctx, "설정 파일 파싱 실패로 프로필 적용 취소");
            return;
        }
        if (scope === "global") {
            await writeProfiles({ ...profiles, active: profileName });
        }
    } catch (err) {
        console.warn("[model-profile] failed to apply profile:", err);
        await notify(ctx, `프로필 적용 실패: ${profileName}`);
        return;
    }

    const changedScopeState = await readScopeState(profiles, scope, ctx);
    const resolvedScopeState = await readResolvedScopeState(profiles, ctx);
    if (resolvedScopeState.active) {
        safeSetStatus(ctx, formatStatusValue(resolvedScopeState.active, resolvedScopeState.scope));
    }
    await notify(
        ctx,
        [
            `프로필 바꿨어: ${profileName} (${changedScopeState.label})`,
            `현재 적용: ${resolvedScopeState.active ?? INVALID_PROFILE_STATUS} (${resolvedScopeState.label})`,
            formatCurrentModels(changedScopeState.overrides),
        ].join("\n"),
    );
}

async function syncStatus(ctx: ExtensionContext): Promise<void> {
    try {
        const profiles = await readProfiles();
        if (!profiles) return;

        const resolved = await readResolvedScopeState(profiles, ctx);
        if (resolved.active) {
            safeSetStatus(ctx, formatStatusValue(resolved.active, resolved.scope));
            return;
        }

        safeSetStatus(ctx, INVALID_PROFILE_STATUS);
    } catch (err) {
        console.warn("[model-profile] failed to sync status:", err);
    }
}

function isSubagentChildProcess(): boolean {
    return process.env.PI_SUBAGENT_CHILD === "1" && typeof process.env.PI_SUBAGENT_CHILD_AGENT === "string";
}

export default function (pi: ExtensionAPI) {
    let currentGeneration = 0;
    let currentSession: SessionState | null = null;

    function isSessionActive(session: SessionState): boolean {
        return (
            session.active &&
            currentSession === session &&
            session.generation === currentGeneration
        );
    }

    function deactivateSession(session: SessionState): void {
        session.active = false;
        if (currentSession === session) currentSession = null;
    }

    pi.registerCommand("profile", {
        description: "빌트인 subagent 모델 프로필 전환 (기본: session, --project: 프로젝트, --global: 전역)",
        handler: async (args: string, ctx: ExtensionContext) => {
            const parsedArgs = parseProfileCommandArgs(args);
            const profiles = await readProfiles();

            if (!profiles) {
                await notify(ctx, "프로필 정의를 읽지 못했어.");
                return;
            }

            if (!parsedArgs.profileName) {
                const resolved = await readResolvedScopeState(profiles, ctx);
                await notify(
                    ctx,
                    [
                        `현재 범위: ${resolved.label}`,
                        `현재 프로필: ${resolved.active ?? INVALID_PROFILE_STATUS}`,
                        `사용가능: ${formatProfileList(listProfileNames(profiles))}`,
                        formatCurrentModels(resolved.overrides),
                    ].join("\n"),
                );
                return;
            }

            await applyProfile(parsedArgs.profileName, parsedArgs.scope, ctx);
        },
    });

    pi.on("before_provider_request", async (event: { payload: unknown }, ctx: ExtensionContext) => {
        try {
            if (!isSubagentChildProcess()) return;

            const agentName = process.env.PI_SUBAGENT_CHILD_AGENT;
            if (!agentName) return;

            const parentSessionId = process.env.PI_SUBAGENT_PARENT_SESSION;
            const overrides = await readEffectiveOverrides(ctx, parentSessionId);
            const override = overrides[agentName];
            if (!override?.model) return;

            if (!isPlainObject(event.payload)) return;
            if (typeof event.payload.model !== "string") return;
            if (event.payload.model === override.model) return;

            return {
                ...event.payload,
                model: override.model,
            };
        } catch (err) {
            console.warn("[model-profile] before_provider_request error:", err);
            return;
        }
    });

    pi.on("session_start", async (_event: unknown, ctx: ExtensionContext) => {
        try {
            if (currentSession) deactivateSession(currentSession);

            const session: SessionState = {
                generation: ++currentGeneration,
                active: true,
                ctx,
            };
            currentSession = session;
            await syncStatus(ctx);
        } catch (err) {
            console.warn("[model-profile] session_start error:", err);
        }
    });

    pi.on("turn_end", async (_event: unknown, ctx: ExtensionContext) => {
        try {
            const session = currentSession;
            if (!session || session.ctx !== ctx || !isSessionActive(session)) {
                return;
            }
            await syncStatus(ctx);
        } catch (err) {
            console.warn("[model-profile] turn_end error:", err);
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
                console.warn("[model-profile] session_shutdown error:", err);
            }
        },
    );
}
