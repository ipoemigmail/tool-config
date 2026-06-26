import type {
    ExtensionAPI,
    ExtensionContext,
} from "@earendil-works/pi-coding-agent";
import { readFile, writeFile } from "node:fs/promises";

const STATUS_KEY = "zmodel-profile";
const PROFILES_PATH = "/Users/ben.jeong1/.pi/agent/model-profiles.json";
const SETTINGS_PATH = "/Users/ben.jeong1/.pi/agent/settings.json";
const STALE_CTX_ERROR_FRAGMENT = "This extension ctx is stale";
const JSON_INDENT_SPACES = 2;
const INVALID_PROFILE_STATUS = "(invalid)";
const SESSION_MODEL_LABEL = "세션모델 상속";
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

/** Raw JSON value: 하위호환 문자열 또는 객체 */
type BuiltinEntryRaw = string | { model: string; thinking?: string };

interface BuiltinEntry {
    model: string;
    thinking?: ThinkingLevel;
}

type AgentOverrideEntry = { model: string; thinking?: ThinkingLevel };
type BuiltinProfileRaw = Record<BuiltinAgentName, BuiltinEntryRaw>;
type ProfilesByName = Record<string, BuiltinProfileRaw>;

interface ModelProfilesFile {
    active: string;
    profiles: ProfilesByName;
}

interface SettingsFile {
    subagents?: {
        agentOverrides?: Record<string, AgentOverrideEntry>;
        [key: string]: unknown;
    };
    [key: string]: unknown;
}

interface SessionState {
    generation: number;
    active: boolean;
    ctx: ExtensionContext;
}

interface SettingsReadResult {
    status: "ok" | "missing" | "invalid";
    settings: SettingsFile;
}

function asRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : {};
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

/** 문자열 | 객체 형태의 raw 값을 BuiltinEntry로 파싱. 잘못된 thinking 레벨은 경고 후 무시. */
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

function formatCurrentModels(overrides: Record<string, AgentOverrideEntry>): string {
    const lines = BUILTIN_AGENT_NAMES.map((name) => {
        const entry = overrides[name];
        if (!entry?.model) return `  ${name}: ${SESSION_MODEL_LABEL}`;
        const thinkingLabel = entry.thinking ? ` (thinking: ${entry.thinking})` : "";
        return `  ${name}: ${entry.model}${thinkingLabel}`;
    });
    return `빌트인 모델:\n${lines.join("\n")}`;
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

async function readCurrentOverrides(): Promise<Record<string, AgentOverrideEntry>> {
    const parsed = await readJsonFile(SETTINGS_PATH);
    const settings = asRecord(parsed);
    const subagents = asRecord(settings.subagents);
    return asRecord(subagents.agentOverrides) as Record<string, AgentOverrideEntry>;
}

async function readSettingsForWrite(): Promise<SettingsReadResult> {
    try {
        const raw = await readFile(SETTINGS_PATH, "utf8");
        const parsed = JSON.parse(raw) as unknown;
        return {
            status: "ok",
            settings: asRecord(parsed) as SettingsFile,
        };
    } catch (err) {
        const code =
            err && typeof err === "object" && "code" in err
                ? err.code
                : undefined;
        if (code === "ENOENT") {
            return { status: "missing", settings: {} };
        }
        return { status: "invalid", settings: {} };
    }
}

async function writeProfiles(profiles: ModelProfilesFile): Promise<void> {
    await writeFile(
        PROFILES_PATH,
        `${JSON.stringify(profiles, null, JSON_INDENT_SPACES)}\n`,
        "utf8",
    );
}

function buildAgentOverrides(profile: BuiltinProfileRaw): Record<string, AgentOverrideEntry> {
    const overrides: Record<string, AgentOverrideEntry> = {};
    for (const builtin of BUILTIN_AGENT_NAMES) {
        const entry = parseBuiltinEntry(profile[builtin]);
        const override: AgentOverrideEntry = { model: entry.model };
        if (entry.thinking) override.thinking = entry.thinking;
        overrides[builtin] = override;
    }
    return overrides;
}

async function writeSettingsOverrides(profile: BuiltinProfileRaw): Promise<boolean> {
    const settingsResult = await readSettingsForWrite();
    if (settingsResult.status === "invalid") return false;

    const { settings } = settingsResult;
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
            agentOverrides: nextOverrides as Record<string, AgentOverrideEntry>,
        },
    };

    await writeFile(
        SETTINGS_PATH,
        `${JSON.stringify(nextSettings, null, JSON_INDENT_SPACES)}\n`,
        "utf8",
    );

    return true;
}

async function applyProfile(
    profileName: string,
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
        const didWriteSettings = await writeSettingsOverrides(profile);
        if (!didWriteSettings) {
            await notify(ctx, "settings.json 파싱 실패로 프로필 적용 취소");
            return;
        }
        await writeProfiles({ ...profiles, active: profileName });
    } catch (err) {
        console.warn("[model-profile] failed to apply profile:", err);
        await notify(ctx, `프로필 적용 실패: ${profileName}`);
        return;
    }

    safeSetStatus(ctx, profileName);
    const overrides = await readCurrentOverrides();
    await notify(
        ctx,
        [`프로필 바꿨어: ${profileName}`, formatCurrentModels(overrides)].join("\n"),
    );
}

async function syncStatus(ctx: ExtensionContext): Promise<void> {
    try {
        const profiles = await readProfiles();
        if (!profiles) return;

        const active = profiles.profiles[profiles.active]
            ? profiles.active
            : INVALID_PROFILE_STATUS;
        safeSetStatus(ctx, active);
    } catch (err) {
        console.warn("[model-profile] failed to sync status:", err);
    }
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
        description: "빌트인 subagent 모델 프로필 전환",
        handler: async (args: string, ctx: ExtensionContext) => {
            const profileName = args.trim();
            const profiles = await readProfiles();

            if (!profiles) {
                await notify(ctx, "프로필 정의를 읽지 못했어.");
                return;
            }

            if (!profileName) {
                const overrides = await readCurrentOverrides();
                await notify(
                    ctx,
                    [
                        `현재 프로필: ${profiles.active}`,
                        `사용가능: ${formatProfileList(listProfileNames(profiles))}`,
                        formatCurrentModels(overrides),
                    ].join("\n"),
                );
                return;
            }

            await applyProfile(profileName, ctx);
        },
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
