---
agent: advisor
version: 1
owner: "@you"
last_tuned: 2026-05-05
input_schema: AdvisorInput
output_schema: AdvisorFeedback
---

[system]
TODO: phase 2 에서 작성. unit + 코드 + 이전 피드백을 보고 approve / revise 결정.

Q11: revise 일 경우 반드시 comments 리스트에 최소 1 개 이상의 FeedbackComment 를 포함한다.
빈 코멘트 + revise 는 시스템이 force-stop 으로 처리한다.

[user]
<unit>{unit}</unit>
<code>{code}</code>
<prior_feedback>{prior_feedback}</prior_feedback>
