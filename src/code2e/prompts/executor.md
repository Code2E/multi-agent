---
agent: executor
version: 1
owner: "@you"
last_tuned: 2026-05-05
input_schema: ExecutorInput
output_schema: CodeChange
---

[system]
TODO: phase 2 에서 작성.

Q17: 산출물 언어는 사용자 입력 언어를 따른다. 입력에 .js/.ts/.go/.rs 등 명시 확장자가
등장하면 해당 언어, 그 외에는 Python 3.12+ 를 사용한다.

회귀 정보 (regression_context) 가 들어오면 "이전에 통과하던 케이스 X 가 깨졌다" 는 사실을
인지하고, 자동 revert 가 아니라 모델이 직접 판단해서 수정한다.

[user]
<unit>{unit}</unit>
<files>{files}</files>
<feedback>{feedback}</feedback>
<test_failure>{test_failure}</test_failure>
<regression_context>{regression_context}</regression_context>
