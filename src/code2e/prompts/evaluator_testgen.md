---
agent: evaluator.testgen
version: 1
owner: "@you"
last_tuned: 2026-05-05
input_schema: EvaluatorTestgenInput
output_schema: TestCase
---

[system]
TODO: phase 2 에서 작성. Final Plan 을 받아 사용자 관점 (블랙박스) E2E 테스트 스위트를
생성한다. 산출은 list[TestCase] (Playwright 스크립트 포함).

[user]
<final_plan>{final_plan}</final_plan>
