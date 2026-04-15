- 코드베이스 탐색은 @fast-explorer 를 사용
- 계획, 구현, 변경은 최소한으로 한다
- 수정방식이 정해진 여러파일을 한번에 수정할 경우 @gpt-mini-general 를 이용해 병렬로 실행
- Plan 모드가 아닐때 구현은 @gpt-mini-general 에 위임, Plan 모드면 구현을 위해 Build 모드로 전환 요청
- 리뷰는 @gpt-general 에 위임
- commit description 은 한글로 작성

