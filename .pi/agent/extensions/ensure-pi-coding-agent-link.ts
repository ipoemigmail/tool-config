/**
 * ensure-pi-coding-agent-link
 *
 * async 서브에이전트 러너는 별도 node 프로세스로 떠서 peer 패키지
 * @earendil-works/pi-coding-agent 를 직접 해석해야 한다. 이 패키지는
 * 확장 npm 트리(.pi/agent/npm/node_modules)에 sibling 으로 링크돼 있어야
 * 하지만, pi/pi-extensions 업데이트 시 npm install 이 미선언 항목으로 보고
 * prune 한다 → async 가 모듈 미해석으로 즉시 크래시.
 *
 * 시작 시 1회 심링크 존재를 보장(자가복구)해 업데이트 후에도 복원되게 한다.
 * 버전/홈 경로는 하드코딩하지 않고 런타임에서 해석한다.
 */

import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const PKG = "@earendil-works/pi-coding-agent";

/** 주어진 파일 경로에서 상위로 올라가며 name 이 PKG 인 package.json 루트를 찾는다. */
function findPackageRootFromEntry(entry: string): string | undefined {
	let dir = path.dirname(entry);
	while (dir !== path.dirname(dir)) {
		const pj = path.join(dir, "package.json");
		if (fs.existsSync(pj)) {
			try {
				if ((JSON.parse(fs.readFileSync(pj, "utf-8")) as { name?: string }).name === PKG) return dir;
			} catch {
				// 손상된 package.json 은 무시하고 계속 탐색
			}
		}
		dir = path.dirname(dir);
	}
	return undefined;
}

/** 호스트가 로드한 pi-coding-agent 패키지 루트를 여러 경로로 해석한다. */
function resolveHostPackageRoot(): string | undefined {
	// 1) pi CLI 진입점(argv[1])은 패키지 내부 → 위로 올라가면 루트
	try {
		const argv1 = process.argv[1];
		if (argv1) {
			const root = findPackageRootFromEntry(fs.realpathSync(argv1));
			if (root) return root;
		}
	} catch {
		// best-effort
	}
	// 2) ESM resolver 로 메인 엔트리 해석 후 루트 역추적
	try {
		return findPackageRootFromEntry(fileURLToPath(import.meta.resolve(PKG)));
	} catch {
		// 3) CJS resolver 폴백
		try {
			return findPackageRootFromEntry(createRequire(import.meta.url).resolve(PKG));
		} catch {
			return undefined;
		}
	}
}

/** dest 가 target 을 가리키는 유효한 심링크인지 확인 후, 아니면 재생성한다. */
function ensureSymlink(target: string, dest: string): "ok" | "created" | "failed" {
	try {
		if (fs.realpathSync(dest) === fs.realpathSync(target)) return "ok";
	} catch {
		// dest 부재/깨진 링크 → 아래에서 재생성
	}
	try {
		fs.mkdirSync(path.dirname(dest), { recursive: true });
		fs.rmSync(dest, { recursive: true, force: true });
		fs.symlinkSync(target, dest);
		return "created";
	} catch {
		return "failed";
	}
}

export default function (_pi: ExtensionAPI) {
	try {
		const root = resolveHostPackageRoot();
		if (!root) return;
		// 이 확장 파일: .pi/agent/extensions/* → npm 트리는 ../npm/node_modules
		const extDir = path.dirname(fileURLToPath(import.meta.url));
		const dest = path.join(extDir, "..", "npm", "node_modules", "@earendil-works", "pi-coding-agent");
		ensureSymlink(root, dest);
	} catch {
		// 시작 자가복구 실패가 pi 기동을 막지 않도록 무음 처리
	}
}
