---
agent: planner
round: 2
version: 1
owner: "@you"
last_tuned: 2026-05-05
input_schema: PlannerInput
output_schema: Plan
---

[system]
TODO: phase 2 에서 작성. round 1 plan 을 받아 정교화 (수렴, temp 0.3).

[user]
<user_input>{user_input}</user_input>
<previous_plan>{prev_plan}</previous_plan>
