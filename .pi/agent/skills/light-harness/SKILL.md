---
name: light-harness
model: haiku
description: "explore, implement, review 를 가벼운 모델을 사용하도록 변경"
---

- 코드베이스 탐색은 `subagent` 도구의 `light-explore` 에이전트를 사용
- 계획, 구현, 변경은 최소한으로 한다
- 코드수정은 `subagent` 도구의 `light-implement` 에이전트를 이용해 수정한다
- 수정방식이 정해진 여러파일을 한번에 수정할 경우 `light-implement` 를 이용해 병렬로 실행
- Plan 모드가 아닐때 구현은 `light-implement` 에 위임, Plan 모드면 구현을 위해 Build 모드로 전환 요청
- 리뷰는 `subagent` 도구의 `light-review` 에이전트에 위임

## 원본 opencode 지시

- 코드베이스 탐색은 @light-explore 를 사용
- 계획, 구현, 변경은 최소한으로 한다
- 코드수정은 @light-implement 을 이용해 수정한다
- 수정방식이 정해진 여러파일을 한번에 수정할 경우 @light-implement 를 이용해 병렬로 실행
- Plan 모드가 아닐때 구현은 @light-implement 에 위임, Plan 모드면 구현을 위해 Build 모드로 전환 요청
- 리뷰는 @light-review 에 위임
