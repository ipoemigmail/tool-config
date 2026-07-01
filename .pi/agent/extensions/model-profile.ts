import type {
    ExtensionAPI,
    ExtensionContext,
} from "@earendil-works/pi-coding-agent";
import type { AutocompleteItem } from "@earendil-works/pi-tui";
import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join, dirname } from "node:path";

const STATUS_KEY = "zmodel-profile";
const STALE_CTX_ERROR_FRAGMENT = "This extension ctx is stale";
const JSON_INDENT_SPACES = 2;
const PI_CONFIG_DIR = ".pi";
const AGENT_DIR_NAME = "agent";
const SETTINGS_FILE = "settings.json";
const SESSION_MODEL_LABEL = "세션모델 상속";
const PROFILE_FILE_EXT = ".json";
const GLOBAL_FLAG = "--global";
const PROJECT_FLAG = "--project";
const SCOPE_FLAGS = [GLOBAL_FLAG, PROJECT_FLAG] as const;
const INHERIT_NAME = "inherit";

const VALID_THINKING_LEVELS = [
    "off",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
] as const;
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
const PLANNER_AGENT_NAME: BuiltinAgentName = "planner";
type ThinkingLevel = (typeof VALID_THINKING_LEVELS)[number];
type ProfileScope = "project" | "global";

interface ActiveProfile {
    name: string;
    scope: ProfileScope;
}

interface AgentOverrideEntry {
    model: string;
    thinking?: ThinkingLevel;
}

/** 프로필 파일 내용 = settings.json 의 subagents 섹션 */
interface ProfileFileContent {
    subagents?: {
        agentOverrides?: Record<string, AgentOverrideEntry>;
        [key: string]: unknown;
    };
    [key: string]: unknown;
}

interface ProfileEntry {
    name: string;
    path: string;
    overrides: Record<string, AgentOverrideEntry>;
}

interface SettingsFile {
    subagents?: {
        agentOverrides?: Record<string, AgentOverrideEntry>;
        [key: string]: unknown;
    };
    [key: string]: unknown;
}

interface SettingsReadResult {
    status: "ok" | "missing" | "invalid";
    settings: SettingsFile;
}

/** 설정 읽기/쓰기 추상화 — project/global 경로 차이만 캡슐화 */
interface SettingsStore {
    readonly scope: ProfileScope;
    readonly path: string;
    read(): Promise<SettingsReadResult>;
    writeOverrides(overrides: Record<string, AgentOverrideEntry>): Promise<boolean>;
    clearOverrides(): Promise<boolean>;
}

/** 프로필 목록 조회 추상화 — 매 호출마다 디스크 재조회 */
interface ProfileRegistry {
    list(): Promise<ProfileEntry[]>;
    get(name: string): Promise<ProfileEntry | null>;
}

interface ParsedArgs {
    profileName: string;
    scope: ProfileScope;
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

/** agentOverrideEntry 유효성: model 필수, thinking은 known 레벨만 */
function isValidOverrideEntry(value: unknown): value is AgentOverrideEntry {
    const record = asRecord(value);
    if (typeof record.model !== "string" || !record.model.trim()) {
        return false;
    }
    if (record.thinking !== undefined) {
        return (
            typeof record.thinking === "string" &&
            isValidThinkingLevel(record.thinking)
        );
    }
    return true;
}

function extractOverrides(
    content: ProfileFileContent,
): Record<string, AgentOverrideEntry> | null {
    const subagents = asRecord(content.subagents);
    const raw = asRecord(subagents.agentOverrides);
    const overrides: Record<string, AgentOverrideEntry> = {};
    for (const name of BUILTIN_AGENT_NAMES) {
        const entry = raw[name];
        if (!isValidOverrideEntry(entry)) return null;
        overrides[name] = entry;
    }
    return overrides;
}

/** 프로필 파일이 빌트인 8개 에이전트를 모두 유효하게 정의하는지 검증 */
function isValidProfileFile(value: unknown): value is ProfileFileContent {
    const record = asRecord(value);
    return extractOverrides(record as ProfileFileContent) !== null;
}

function formatProfileList(names: string[]): string {
    return names.length > 0 ? names.join(", ") : "(없음)";
}

function formatCurrentModels(
    overrides: Record<string, AgentOverrideEntry>,
): string {
    const lines = BUILTIN_AGENT_NAMES.map((name) => {
        const entry = overrides[name];
        if (!entry?.model) return `  ${name}: ${SESSION_MODEL_LABEL}`;
        const thinkingLabel = entry.thinking
            ? ` (thinking: ${entry.thinking})`
            : "";
        return `  ${name}: ${entry.model}${thinkingLabel}`;
    });
    return `빌트인 모델:\n${lines.join("\n")}`;
}

function setStatus(
    ctx: ExtensionContext,
    active: ActiveProfile | undefined,
): void {
    if (!active) return;
    ctx.ui.setStatus(
        STATUS_KEY,
        `| profile: ${active.name} (${active.scope})`,
    );
}

function safeSetStatus(
    ctx: ExtensionContext,
    active: ActiveProfile | undefined,
): void {
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

function buildAgentOverrides(
    overrides: Record<string, AgentOverrideEntry>,
): Record<string, AgentOverrideEntry> {
    const result: Record<string, AgentOverrideEntry> = {};
    for (const name of BUILTIN_AGENT_NAMES) {
        const entry = overrides[name];
        if (!entry) continue;
        const override: AgentOverrideEntry = { model: entry.model };
        if (entry.thinking) override.thinking = entry.thinking;
        result[name] = override;
    }
    return result;
}

/** 디렉토리 기반 프로필 레지스트리 — list/get 매번 디스크 재조회 */
function createProfileRegistry(dir: string): ProfileRegistry {
    async function loadEntry(
        name: string,
        path: string,
    ): Promise<ProfileEntry | null> {
        const parsed = await readJsonFile(path);
        if (!isValidProfileFile(parsed)) return null;
        const overrides = extractOverrides(parsed as ProfileFileContent);
        return overrides ? { name, path, overrides } : null;
    }

    return {
        async list() {
            let files: string[];
            try {
                files = await readdir(dir);
            } catch {
                return [];
            }
            const entries: ProfileEntry[] = [];
            for (const file of files.sort()) {
                if (!file.endsWith(PROFILE_FILE_EXT)) continue;
                const name = file.slice(0, -PROFILE_FILE_EXT.length);
                const entry = await loadEntry(name, join(dir, file));
                if (entry) entries.push(entry);
            }
            return entries;
        },
        async get(name) {
            const path = join(dir, `${name}${PROFILE_FILE_EXT}`);
            return loadEntry(name, path);
        },
    };
}

/** SettingsStore 구현 — 경로에 따라 project/global */
function createSettingsStore(
    scope: ProfileScope,
    path: string,
): SettingsStore {
    return {
        scope,
        path,
        async read() {
            try {
                const raw = await readFile(path, "utf8");
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
        },
        async writeOverrides(overrides) {
            const result = await this.read();
            if (result.status === "invalid") return false;

            const { settings } = result;
            const nextSubagents = asRecord(settings.subagents);
            const nextOverrides = asRecord(
                nextSubagents.agentOverrides,
            ) as Record<string, AgentOverrideEntry>;
            const profileOverrides = buildAgentOverrides(overrides);

            for (const builtin of BUILTIN_AGENT_NAMES) {
                nextOverrides[builtin] = profileOverrides[builtin];
            }

            const nextSettings: SettingsFile = {
                ...settings,
                subagents: {
                    ...nextSubagents,
                    agentOverrides: nextOverrides,
                },
            };

            await mkdir(dirname(path), { recursive: true });
            await writeFile(
                path,
                `${JSON.stringify(nextSettings, null, JSON_INDENT_SPACES)}\n`,
                "utf8",
            );
            return true;
        },
        async clearOverrides() {
            const result = await this.read();
            if (result.status === "invalid") return false;

            const { settings } = result;
            const subagents = asRecord(settings.subagents);
            const nextSubagents = { ...subagents };
            delete nextSubagents.agentOverrides;

            const nextSettings: SettingsFile =
                Object.keys(nextSubagents).length > 0
                    ? { ...settings, subagents: nextSubagents }
                    : { ...settings, subagents: undefined };

            await mkdir(dirname(path), { recursive: true });
            await writeFile(
                path,
                `${JSON.stringify(nextSettings, null, JSON_INDENT_SPACES)}\n`,
                "utf8",
            );
            return true;
        },
    };
}

function resolveStore(
    scope: ProfileScope,
    ctx: ExtensionContext,
    agentDir: string,
): SettingsStore {
    const path =
        scope === "global"
            ? join(agentDir, SETTINGS_FILE)
            : join(ctx.cwd, PI_CONFIG_DIR, SETTINGS_FILE);
    return createSettingsStore(scope, path);
}

/** /profile <name> [--global|--project] 파싱. 기본 scope = project */
function parseArgs(args: string): ParsedArgs {
    const tokens = args.trim().split(/\s+/).filter(Boolean);
    let scope: ProfileScope = "project";
    const positional: string[] = [];
    for (const token of tokens) {
        if (token === GLOBAL_FLAG) scope = "global";
        else if (token === PROJECT_FLAG) scope = "project";
        else positional.push(token);
    }
    return { profileName: positional[0] ?? "", scope };
}

async function applyProfile(
    pi: ExtensionAPI,
    profileName: string,
    scope: ProfileScope,
    registry: ProfileRegistry,
    ctx: ExtensionContext,
    agentDir: string,
    current: { active: ActiveProfile | undefined },
): Promise<void> {
    const profile = await registry.get(profileName);
    if (!profile) {
        const names = (await registry.list()).map((e) => e.name);
        await notify(
            ctx,
            `없는 프로필이야: ${profileName} (가능: ${formatProfileList(names)})`,
        );
        return;
    }

    const store = resolveStore(scope, ctx, agentDir);
    try {
        const didWrite = await store.writeOverrides(profile.overrides);
        if (!didWrite) {
            await notify(
                ctx,
                `${store.path} 파싱 실패로 프로필 적용 취소`,
            );
            return;
        }
    } catch (err) {
        console.warn("[model-profile] failed to apply profile:", err);
        await notify(ctx, `프로필 적용 실패: ${profileName}`);
        return;
    }

    current.active = { name: profileName, scope };
    safeSetStatus(ctx, current.active);
    const result = await store.read();
    const overrides =
        result.status === "ok"
            ? (asRecord(asRecord(result.settings.subagents).agentOverrides) as Record<
                  string,
                  AgentOverrideEntry
              >)
            : {};
    await notify(
        ctx,
        [
            `프로필 바꿨어: ${profileName} (${scope})`,
            formatCurrentModels(overrides),
        ].join("\n"),
    );

    // effective scope(project 변경, 또는 project 미설정 상태의 global 변경)면 현재 모델도 planner 와 동일하게
    if (await isEffectiveScope(scope, ctx, agentDir)) {
        await syncCurrentModelToPlanner(
            pi,
            ctx,
            profile.overrides[PLANNER_AGENT_NAME],
        );
    }
}

async function clearProfile(
    scope: ProfileScope,
    ctx: ExtensionContext,
    agentDir: string,
    current: { active: ActiveProfile | undefined },
): Promise<void> {
    const store = resolveStore(scope, ctx, agentDir);
    try {
        const didWrite = await store.clearOverrides();
        if (!didWrite) {
            await notify(
                ctx,
                `${store.path} 파싱 실패로 inherit 적용 취소`,
            );
            return;
        }
    } catch (err) {
        console.warn("[model-profile] failed to clear profile:", err);
        await notify(ctx, "inherit 적용 실패");
        return;
    }

    current.active = undefined;
    safeSetStatus(ctx, undefined);
    await notify(ctx, `inherit 적용: ${scope} settings 의 agentOverrides 제거`);
}

function readOverridesFrom(
    result: SettingsReadResult,
): Record<string, AgentOverrideEntry> {
    if (result.status !== "ok") return {};
    return asRecord(
        asRecord(result.settings.subagents).agentOverrides,
    ) as Record<string, AgentOverrideEntry>;
}

/** 두 override 집합이 빌트인 8개 모두 동일한지 비교 */
function overridesMatch(
    a: Record<string, AgentOverrideEntry>,
    b: Record<string, AgentOverrideEntry>,
): boolean {
    return BUILTIN_AGENT_NAMES.every((name) => {
        const x = a[name];
        const y = b[name];
        return (
            !!x &&
            !!y &&
            x.model === y.model &&
            (x.thinking ?? undefined) === (y.thinking ?? undefined)
        );
    });
}

/** 현재 세션 모델을 planner override 모델과 동일하게 맞춤 */
async function syncCurrentModelToPlanner(
    pi: ExtensionAPI,
    ctx: ExtensionContext,
    planner: AgentOverrideEntry,
): Promise<void> {
    const model = ctx.modelRegistry
        .getAll()
        .find((m) => `${m.provider}/${m.id}` === planner.model);
    if (!model) {
        await notify(ctx, `planner 모델을 못 찾음: ${planner.model}`);
        return;
    }
    const ok = await pi.setModel(model);
    if (!ok) {
        await notify(ctx, `현재 모델 변경 실패(인증 없음): ${planner.model}`);
        return;
    }
    if (planner.thinking) pi.setThinkingLevel(planner.thinking);
}

/** 적용 scope 가 실제 유효한지 — project 는 항상, global 은 project override 없을 때만 */
async function isEffectiveScope(
    scope: ProfileScope,
    ctx: ExtensionContext,
    agentDir: string,
): Promise<boolean> {
    if (scope === "project") return true;
    const projectOverrides = readOverridesFrom(
        await resolveStore("project", ctx, agentDir).read(),
    );
    return Object.keys(projectOverrides).length === 0;
}

/** 현재 적용된 settings(project 우선, 없으면 global)와 일치하는 프로필명 추론 */
async function detectActiveProfile(
    registry: ProfileRegistry,
    ctx: ExtensionContext,
    agentDir: string,
): Promise<ActiveProfile | undefined> {
    const projectOverrides = readOverridesFrom(
        await resolveStore("project", ctx, agentDir).read(),
    );
    const useProject = Object.keys(projectOverrides).length > 0;
    const scope: ProfileScope = useProject ? "project" : "global";
    const effective = useProject
        ? projectOverrides
        : readOverridesFrom(
              await resolveStore("global", ctx, agentDir).read(),
          );
    if (Object.keys(effective).length === 0) return undefined;

    for (const profile of await registry.list()) {
        if (overridesMatch(effective, profile.overrides)) {
            return { name: profile.name, scope };
        }
    }
    return undefined;
}

export default function (pi: ExtensionAPI) {
    const agentDir = join(homedir(), PI_CONFIG_DIR, AGENT_DIR_NAME);
    const registry = createProfileRegistry(
        join(agentDir, "profiles", "pi-subagents"),
    );
    const current = { active: undefined as ActiveProfile | undefined };

    pi.registerCommand("profile", {
        description: "빌트인 subagent 모델 프로필 전환 (기본 project, --global 시 global)",
        getArgumentCompletions: async (
            argumentPrefix: string,
        ): Promise<AutocompleteItem[] | null> => {
            const prefix = argumentPrefix.trim().toLowerCase();
            const names = (await registry.list()).map((e) => e.name);
            const candidates = [...names, INHERIT_NAME, ...SCOPE_FLAGS];
            const items = candidates
                .filter((c) => c.toLowerCase().startsWith(prefix))
                .map((c) => ({
                    value: c,
                    label: c,
                    description: names.includes(c)
                        ? "프로필"
                        : c === INHERIT_NAME
                          ? "기본값 복귀 (override 삭제)"
                          : "적용 대상",
                }));
            return items.length > 0 ? items : null;
        },
        handler: async (args: string, ctx: ExtensionContext) => {
            const { profileName, scope } = parseArgs(args);
            const entries = await registry.list();

            if (!profileName) {
                const names = entries.map((e) => e.name);
                if (names.length === 0) {
                    await notify(ctx, "사용가능한 프로필이 없어.");
                    return;
                }
                await notify(
                    ctx,
                    `프로필 ${names.length}개: ${formatProfileList(names)}`,
                );
                let choice: string | undefined;
                try {
                    choice = await ctx.ui.select("프로필 선택:", names);
                } catch (err) {
                    if (isStaleCtxError(err)) return;
                    console.warn("[model-profile] select failed:", err);
                }
                if (!choice) return;
                await applyProfile(pi, choice, scope, registry, ctx, agentDir, current);
                return;
            }

            if (profileName === INHERIT_NAME) {
                await clearProfile(scope, ctx, agentDir, current);
            } else {
                await applyProfile(pi, profileName, scope, registry, ctx, agentDir, current);
            }
        },
    });

    pi.on("session_start", async (_event: unknown, ctx: ExtensionContext) => {
        try {
            if (!current.active) {
                current.active = await detectActiveProfile(
                    registry,
                    ctx,
                    agentDir,
                );
            }
            safeSetStatus(ctx, current.active);
        } catch (err) {
            console.warn("[model-profile] session_start error:", err);
        }
    });

    pi.on(
        "session_shutdown",
        async (_event: unknown, ctx: ExtensionContext) => {
            try {
                if (ctx.hasUI) ctx.ui.setStatus(STATUS_KEY, "");
            } catch (err) {
                if (isStaleCtxError(err)) return;
                console.warn("[model-profile] session_shutdown error:", err);
            }
        },
    );
}
