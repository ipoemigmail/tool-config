---
name: jira-sync
model: haiku
harness: .agents/skills/jira-sync/HARNESS.md
description: "MD 파일과 JIRA 이슈를 JIRA DSL로 변환한 뒤 결정적 스크립트로 병합/렌더링/반영한다. Jira 환경 설정, 토큰 문제도 이 스킬이 담당한다. 'JIRA 동기화', 'jira sync', 'KCDL-XXXX 동기화', Jira URL + 동기화 요청, 'jira 설정', 'jira 토큰', 'jira 401' 시 사용."
---

# jira-sync - MD <-> JIRA via Jira DSL

MD 파일(EPIC.md, .tasks/*.md)과 JIRA 이슈를 직접 서로 변환하지 않는다.
항상 중간 고정 포맷인 `Jira DSL`로 변환한 뒤, 결정적 스크립트로 병합/렌더링/반영한다.

TRIGGER when: 'JIRA 동기화', 'jira sync', 'jira 동기화', 'KCDL-XXXX 동기화', 'https://jira.daumkakao.com/browse/KCDL-XXXX 동기화해줘' 등 Jira 이슈 키나 URL과 함께 동기화를 요청할 때. 또한 'jira 설정', 'jira 토큰', 'jira 401', 'jira setup' 등 Jira 환경 설정·트러블슈팅 요청 시에도 이 스킬을 사용한다.

프로세스 개요, 템플릿 준수 규칙, evaluation 기준, 가드레일은 **HARNESS.md**를 참조한다.

## Core Idea

표준 경로에서는 LLM을 끼우지 않는다. 파싱·병합·렌더링·반영은 모두 스크립트로 수행한다.

1. MD → Jira DSL: `scripts/md_to_jira_dsl.py`
2. Jira raw → Jira DSL: `scripts/jira_raw_to_jira_dsl.py`
3. Jira DSL merge: `scripts/merge_jira_dsl.py`
4. Jira DSL → MD: `scripts/render_md_from_jira_dsl.py`
5. Jira DSL → Jira: `scripts/sync_jira_from_jira_dsl.py`
6. End-to-end: `scripts/run_jira_sync.py`

## Jira DSL

Jira DSL은 Jira issue model의 서브셋이며 shape는 고정이다.

- Schema: `reference/jira_dsl.schema.json`
- Full example: `reference/jira_dsl.example.full.json`
- Version: `1`
- Root keys: `version`, `issue`, `description_markdown`, `md_force_fields`, `checklists`
- Checklist sections: `todo`, `acceptance_criteria`

Example:

```json
{
  "version": 1,
  "issue": {
    "key": "KCDL-5192",
    "url": "https://jira.daumkakao.com/browse/KCDL-5192",
    "summary": "Gift first-purchase logging",
    "epic_name": "Gift first-purchase logging",
    "issue_type": "Epic",
    "status": "Open",
    "priority": "Medium",
    "created_at": "2026-03-17",
    "assignee": {
      "username": "ben.jeong1",
      "display_name": "ben.jeong1(정병준)/kakao"
    },
    "reporter": {
      "username": "ben.jeong1",
      "display_name": "ben.jeong1(정병준)/kakao"
    },
    "parent_key": "KCDL-4991",
    "end_date": null,
    "due_date": null,
    "labels": ["광고", "선물하기"],
    "components": ["ActionBase", "kc-spark"],
    "links": []
  },
  "md_force_fields": ["status", "links"],
  "description_markdown": "## 배경\\n...",
  "checklists": {
    "todo": [
      {
        "id": 9,
        "name": "테스트 필드3",
        "checked": false,
        "completed_date": null,
        "linked_issue_key": null,
        "assignee_username": "eden.yoon",
        "status_name": "In Progress",
        "status_id": "inProgress"
      }
    ],
    "acceptance_criteria": []
  }
}
```

## 템플릿

- SSOT: `reference/kcdl_issue_templates.md` — 스크립트 로드 + MD 포맷 예시 + 변환 규칙을 1개 파일에 통합
- 포맷 규칙, 변환 규칙, 필수 섹션: **HARNESS.md 참조**

## Deterministic Rules

### Metadata ownership

- `description_markdown`: Jira 우선, Jira에 값이 없으면 MD 사용
- MD 본문 최상단에 `((force))` 한 줄을 두면 `description_markdown`은 MD 우선이다. 마커는 파싱 시 제거되고 DSL의 `md_force_fields`에 `description_markdown`으로 기록된다
- frontmatter **필드명** 끝에 `((force))`를 붙이면 해당 필드는 MD 우선이다. 예: `status ((force)): In Progress`
- `((force))`는 MD 파싱 시 필드명에서 제거되고 DSL의 `md_force_fields`에 기록된다. Markdown 렌더링 시 `((force))`는 붙지 않는다
- **((force)) 자동 클린업**: MD에 `((force))`가 남아 있어도 Jira에 반영할 변경이 없으면 `run_jira_sync.py`가 소스 MD에서 마커를 물리적으로 제거한다. preview-only 모드에서도 동작
- `labels`, `components`, `links`: 배열 전체가 하나의 값으로 취급. Jira 우선, `((force))`가 있으면 MD 우선
- `status` 포함 나머지: Jira 우선. Jira에 값이 없으면 `null` 유지

### New markdown bootstrap

- 신규 MD는 Jira raw → DSL 변환 결과를 최대한 렌더링
- 최소 포함: `summary`, `url`, `issue_type`, `status`, `priority`, `assignee`, `reporter`, `created_at`, `parent_key`, `end_date`, `due_date`, `labels`, `components`, `links`, `description_markdown`, `checklists`
- `epic_name`은 DSL/Jira에만 존재, MD frontmatter에는 렌더링하지 않는다

### Checklist merge

- `todo` → Jira `customfield_11250`, `acceptance_criteria` → Jira `customfield_11251`
- 머지는 각 섹션 내부에서만 수행
- **항목 매칭 키는 `id`** (Jira checklist item ID)
- 모든 필드는 **Jira 우선**
- MD에만 있는 항목 중 id가 없는 항목은 삭제
- Jira에만 있는 항목은 MD에 추가

### Checklist Jira payload

- `assignee_username` → `assigneeIds: [username]`
- `linked_issue_key` → `linkedIssueKey: "KCDL-XXXX"`
- `status_id == "none"` 이면 `status` 객체를 보내지 않는다
- `status_id != "none"` 이면 반드시 `status`와 `statusId`를 함께 보낸다
- checked item은 `statusId: "none"`으로 보낸다
- 유효한 status: `inProgress`만 (`toDo`, `done`은 미설정)

### Issue status transition

- status는 필드 PUT이 아닌 **Transitions API**로 변경
- `sync_jira_from_jira_dsl.py`가 자동 처리: 현재 status 조회 → transition 목록 조회 → 실행
- **Status resolve**: transition 정의의 `to.name`으로 resolved status 결정. merged DSL의 status와 다르면 DSL 파일을 갱신하여 MD 렌더링에 반영
- Jira 응답으로 DSL을 되돌리지 않는다 — 스크립트의 의도가 우선

### Post-sync verification

- `verify_jira_sync()`가 Jira 실제 상태와 merged DSL을 비교
- 검증 필드: `status`, `summary`, `priority`, `assignee`
- 결과: `/tmp/{issue_key}.verify.json`
- mismatch는 리포트 목적. DSL을 되돌리지 않는다

### parent_key Jira payload

- `Task`/`Story` → `customfield_10350` (Epic Link)
- 그 외 → `customfield_12287` (Parent Link)
- 생성 시 Epic Link는 create payload에서 무시될 수 있으므로 **별도 PUT**으로 설정

## MD 파일 형식

YAML frontmatter + 본문(Description).

```yaml
---
title: Gift first-purchase logging
jira: KCDL-5192
url: https://jira.daumkakao.com/browse/KCDL-5192
issue_type: Epic
status: In Progress
priority: Medium
assignee: ben.jeong1(정병준)/kakao
reporter: ben.jeong1(정병준)/kakao
created_at: 2026-03-17
parent_key: KCDL-4991
end_date: null
due_date: null
labels:
  - 광고
  - 선물하기
components:
  - ActionBase
  - kc-spark
links:
  - direction: inward
    relationship: blocks
    key: KCDL-5000
    summary: Some issue
todo:
  - id: 9
    name: 테스트 필드3
    checked: false
    assignee_username: eden.yoon
    status_name: In Progress
    status_id: inProgress
acceptance_criteria: []
---

## 배경
...
```

- `title` → `issue.summary`, `jira` → `issue.key`
- 체크리스트 항목: `id`, `name`, `checked` (필수), `completed_date`, `linked_issue_key`, `assignee_username`, `status_name`, `status_id` (optional)
- 본문은 `description_markdown` 내용만
- `epic_name`은 MD에 렌더링하지 않는다

## Workflow

HARNESS.md의 Process A/B/C를 따른다. 아래는 스크립트 호출의 상세 규칙.

### Phase 0: Jira 환경 확인

1. `$JIRA_API_TOKEN` 환경변수 확인. 없으면 사용자에게 쉘 환경변수 파일에서 로드할지 확인
2. 토큰이 없으면 https://jira.daumkakao.com/secure/ViewProfile.jspa 에서 발급 안내

### Phase 0: Collect raw sources

1. Load target MD file
2. Extract Jira key from frontmatter
3. Fetch Jira raw issue JSON → `/tmp/{ticket}.raw.json`
4. Epic이면 child issues JQL: `'Epic Link' = {KEY} OR parent = {KEY}`
5. Collect local `.tasks/*.md`

### Phase 1: Source conversion → Jira DSL

```bash
python3 .agents/skills/jira-sync/scripts/md_to_jira_dsl.py \
  --input "/absolute/path/to/EPIC.md" \
  --output /tmp/spec.md.dsl.json

python3 .agents/skills/jira-sync/scripts/jira_raw_to_jira_dsl.py \
  --input /tmp/spec.raw.json \
  --output /tmp/spec.jira.dsl.json
```

- Jira wiki markup → Markdown 변환은 스크립트 내부 변환기 사용 (LLM 배제)
- 2개 DSL에 차이가 없으면 종료

### Phase 2: Deterministic merge

```bash
python3 .agents/skills/jira-sync/scripts/merge_jira_dsl.py \
  --md /tmp/spec.md.dsl.json \
  --jira /tmp/spec.jira.dsl.json \
  --output /tmp/spec.merged.dsl.json
```

### Phase 3: Approval

- **반드시 `/tmp/jira_sync_summary.json`을 읽고 `fields_to_write_to_md`, `fields_to_sync_to_jira`를 사용자에게 전달**
- `fields_to_sync_to_jira`가 비어 있지 않으면 "Jira 변경사항 없음"이라고 잘못 말하지 않는다
- 승인 전에 `--apply` 실행 금지

### Phase 4: Deterministic write

**Jira 반영을 먼저** 수행 → status resolve 후 MD 렌더링.

```bash
python3 .agents/skills/jira-sync/scripts/sync_jira_from_jira_dsl.py \
  --input /tmp/spec.merged.dsl.json --apply

python3 .agents/skills/jira-sync/scripts/render_md_from_jira_dsl.py \
  --input /tmp/spec.merged.dsl.json --output "/absolute/path/to/EPIC.md"
```

### Phase 5: Create issue

```bash
python3 .agents/skills/jira-sync/scripts/sync_jira_from_jira_dsl.py \
  --input /tmp/task.merged.dsl.json --create --project-key KCDL --apply
```

생성된 key/url은 즉시 merged DSL에 반영.

## Epic orchestration

### 파이프라인

1. 메인 Epic DSL 파이프라인 수행
2. Jira child issues와 local `.tasks/*.md`를 key/summary 기준 매칭
3. child issue마다 동일 DSL pipeline 반복
4. Jira에만 있는 child issue → 파일 생성
5. child issue 병렬 처리: `--max-workers N`

진입점:
```bash
python3 .agents/skills/jira-sync/scripts/run_jira_sync.py \
  --md "/absolute/path/to/EPIC.md"
```

- 기본: preview-only (`/tmp/jira_sync_summary.json` + `/tmp/{ticket}.preview.md`)
- `--write-md`: MD 저장
- `--apply-jira`: Jira 반영 + MD 저장

## Task 파일명 규칙

상태 이모지 + `(JIRA-KEY)` + 제목:
- `Holding` → ⏸️, `In Progress` → 🔄, `Resolved` → ⏹️, `Open` → 🆕, `Closed` → ☑️, `Reopened` → ⏏️, `기타/미지정` → *️⃣

## Guardrails

- curl로만 Jira와 통신한다
- 승인 없이 Jira update/create 금지
- 승인 없이 MD overwrite 금지
- Jira raw → DSL 변환은 반드시 스크립트 산출물 사용
- Jira wiki → MD 변환은 스크립트 결과 사용
- merged DSL은 반드시 파일로 남기고, 단일 진실 공급원으로 사용
- 체크리스트 payload는 반드시 array
- `statusId`만 보내고 `status`를 누락하지 않는다
- Epic child JQL 생략 금지
- 템플릿/렌더링 수정 시 반드시 테스트 실행 (HARNESS.md 참조)
- sync 후 evaluation 실행 (HARNESS.md 참조)
