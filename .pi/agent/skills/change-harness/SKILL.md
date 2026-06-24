---
name: change-harness
description: "explore, implement, review 를 지정한 모델을 사용하도록 변경 (anth, gpt, ollama)"
---

- change-harness 는 선택이 아니라 강제 지침이다.
- `{param}` 은 스킬 호출 시 전달받은 하네스 접두사다. (예: `anth`, `gpt`, `ollama`)
- `{param}` 이 전달되지 않으면 default 는 `gpt` 이다.
- 계획문서작성은 반드시 직접처리한다.
- 코드베이스 탐색은 반드시 `subagent` 도구의 `{param}-explore` 에이전트에 위임한다.
- 코드 수정/구현은 반드시 `subagent` 도구의 `{param}-implement` 에이전트에 위임한다.
- 코드 리뷰는 반드시 `subagent` 도구의 `{param}-review` 에이전트에 위임한다.
- 메인 에이전트는 코드 탐색/수정 목적으로 `read`, `bash`, `edit`, `write`를 직접 사용하지 않는다.
- 예외: skill 파일 로드, 사용자 명시적 직접 처리 요청, subagent 실패 후 사용자 승인 받은 경우.
- subagent 위임 없이 직접 처리해야 할 상황이면 먼저 사용자에게 확인한다.
