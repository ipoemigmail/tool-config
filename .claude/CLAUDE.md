- 한글로 얘기한다
- plan mode 에서도 한글로 얘기한다
- 수정방식이 정해진 여러파일을 한번에 수정할 경우 sonnet-ge 서브 에이전트를 이용해 병렬로 실행
- Plan 모드가 아닐때 구현은 sonnet-ge 서브 에이전트에 위임, Plan 모드면 구현을 위해 Build 모드로 전환 요청
- 리뷰는 opus-ge 에 위임
- commit description 은 한글로 작성

