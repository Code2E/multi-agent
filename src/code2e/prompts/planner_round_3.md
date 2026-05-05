---
agent: planner
round: 3
version: 1
owner: "@you"
last_tuned: 2026-05-05
input_schema: PlannerInput
output_schema: Plan
---

[system]
TODO: phase 2 에서 작성. round 2 plan 을 받아 최종 plan 생성. **반드시** frontmatter 의
`units:` 리스트와 `launch:` 블록 (LaunchSpec 형식) 을 포함한다 (가능한 경우).

Q20: 명백히 infeasible 한 unit 이 있으면 "INFEASIBLE: <reason>" 으로 명시 표기.

[user]
<user_input>{user_input}</user_input>
<previous_plan>{prev_plan}</previous_plan>
