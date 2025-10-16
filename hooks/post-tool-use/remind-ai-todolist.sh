#!/bin/bash

# ai-todolist.md 파일에서 is_execution_started = TRUE 체크
# rg가 있으면 rg 사용, 없으면 grep 사용
if command -v rg >/dev/null 2>&1; then
    # rg 사용 (대소문자 무시, 공백 유연하게 매칭)
    if ! rg -qi 'is_execution_started\s*=\s*TRUE' ai-todolist.md 2>/dev/null; then
        echo "[INFO] Execution not started yet (is_execution_started != TRUE). Skipping hook." >&2
        exit 0
    fi
else
    # grep 사용 (대소문자 무시, 공백 유연하게 매칭)
    if ! grep -Eiq 'is_execution_started[[:space:]]*=[[:space:]]*TRUE' ai-todolist.md 2>/dev/null; then
        echo "[INFO] Execution not started yet (is_execution_started != TRUE). Skipping hook." >&2
        exit 0
    fi
fi

# 로그 파일 경로
LOG_FILE="/tmp/ai-todolist-hook.log"

# 현재 시간
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# stdin으로 들어온 입력을 읽어서 무시 (훅이 정상 작동하려면 필요)
cat > /dev/null

echo "**당신이 executor agent 라면, @notepad.md 에 메모하며 진행하는것을 잊지마세요!** - 아니라면 이 메시지를 무시하세요." >&2
echo "" >&2
echo "메모할 내용:" >&2
echo "- 작업 시점에 요구받은 내용" >&2
echo "- 작성 시점의 시간" >&2
echo "- 상세한 작업 로그" >&2
echo "- 작업 전 만들었던 가설" >&2
echo "- 작업 이후 가설과 달랐던 부분" >&2
echo "- 작업하며 드는 생각들 메모" >&2
echo "- 작업하며 얻은 인사이트" >&2
echo "- 작업 도중 예상과 달랐던 부분" >&2
echo "- 아무것도 모르는 다른 새로운 작업자를 위한 팁 및 조언" >&2
echo "- 발견한 문제" >&2
echo "" >&2
echo "메모 추가 방법:" >&2
echo "echo '## $current_time - 작업 제목' >> @notepad.md" >&2
echo "echo '- 작업 내용 및 메모' >> @notepad.md" >&2
echo "echo '' >> @notepad.md" >&2
echo "" >&2
echo "[COMMIT REMINDER]" >&2
echo "After modifying notepad.md, don't forget to commit your notepad.md changes!" >&2
echo "" >&2

echo ""
echo "[DELEGATION REMINDER]" >&2
echo "DO NOT work directly - delegate tasks to @agent-executor agent" >&2
echo "Use: Task tool with subagent_type='executor'" >&2
echo "Follow /execute command pattern for systematic completion" >&2
echo "" >&2
echo "사용자가 제시한, 그리고 계획서 속의 요구사항과 지시사항을 어떠한 게으름도 없이 정직하게 이행하세요." >&2
echo "작업해야 할 내용을 TODO 로 코드 내에 남기는것, 테스트가 온전하고 완전하게 통과하지 않았는데 넘어가는것은 엄격히 금지됩니다." >&2
echo "어떠한 경우에서도 작업 계획서 속 내용을 모두 구현해야하고, 테스트가 모두 통과해야 합니다 - 어떠한 이유도 허용되지 않습니다. (일시적인 세팅 오류라도 넘어가서는 안되고 성공하는것을 명시적으로 확인하여야 합니다.)" >&2

exit 2
