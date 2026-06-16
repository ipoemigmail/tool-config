---
name: general-harness
model: haiku
description: "explore, implement, review 를 일반 모델을 사용하도록 변경"
---

- general-harness는 선택이 아니라 강제 지침이다.
- 코드 수정/구현 계획은 반드시 `subagent` 에게 위임하지 않고 직접처리한다.
- 코드베이스 탐색은 반드시 `subagent` 도구의 `general-explore` 에이전트에 위임한다.
- 코드 수정/구현은 반드시 `subagent` 도구의 `general-implement` 에이전트에 위임한다.
- 코드 리뷰는 반드시 `subagent` 도구의 `general-review` 에이전트에 위임한다.
- 메인 에이전트는 코드 탐색/수정 목적으로 `read`, `bash`, `edit`, `write`를 직접 사용하지 않는다.
- 예외: skill 파일 로드, 사용자 명시적 직접 처리 요청, subagent 실패 후 사용자 승인 받은 경우.
- subagent 위임 없이 직접 처리해야 할 상황이면 먼저 사용자에게 확인한다.
