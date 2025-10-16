#!/bin/bash

# Store executor string as variable for reuse
EXECUTOR="executor"

# Main script
hook_filename=$(basename "$0" | sed 's/\.[^.]*$//' | tr '_' '-')

# Capture input to a variable first
INPUT_DATA=$(cat)

# Extract subagent_type
SUBAGENT_TYPE=$(echo "$INPUT_DATA" | jq -r '.tool_input.subagent_type // empty')

# Check if not executor - exit silently with 0
if [ "$SUBAGENT_TYPE" != "$EXECUTOR" ]; then
    echo "Skipping: Not an executor agent (subagent_type: $SUBAGENT_TYPE)" >&2
    exit 0  # Not an executor, exit quietly
fi

# From here on, we know it's an executor agent

# Get current system time with timezone
CURRENT_TIME=$(date '+%Y-%m-%dT%H:%M:%S%z' | sed 's/\([0-9][0-9]\)$/:\1/')

# Time info
echo "" >&2
echo "[SYSTEM TIME]" >&2
echo "Current system time: $CURRENT_TIME" >&2
echo "" >&2

# Git info (if in git repo)
if git rev-parse --git-dir > /dev/null 2>&1; then
    echo "[GIT INFO]" >&2
    BRANCH=$(git branch --show-current 2>/dev/null || echo "detached")
    echo "Git branch: $BRANCH" >&2
    
    # Latest commit info
    COMMIT_HASH=$(git rev-parse --short HEAD 2>/dev/null)
    COMMIT_MESSAGE=$(git log -1 --pretty=%s 2>/dev/null)
    COMMIT_AUTHOR=$(git log -1 --pretty=%an 2>/dev/null)
    COMMIT_TIME=$(git log -1 --pretty=%ar 2>/dev/null)
    echo "Git commit: [$COMMIT_HASH] \"$COMMIT_MESSAGE\" by $COMMIT_AUTHOR ($COMMIT_TIME)" >&2
    
    # Unstaged changes count (only if there are changes)
    UNSTAGED=$(git status --porcelain 2>/dev/null | wc -l | xargs)
    if [[ "$UNSTAGED" -gt 0 ]]; then
        echo "Git unstaged: $UNSTAGED files" >&2
    fi
    
    # Git root path (only if different from current directory)
    GIT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
    CURRENT_DIR=$(pwd)
    if [[ "$GIT_ROOT" != "$CURRENT_DIR" ]]; then
        echo "Git root: $GIT_ROOT" >&2
    fi
    echo "" >&2
fi

# Use rg to find uncompleted todo items (search current dir and subdirs for ai-todolist.md)
CWD=$(echo "$INPUT_DATA" | jq -r '.cwd')
UNCOMPLETED_TODOS=$(cd "$CWD" 2>/dev/null && find . -name "ai-todolist.md" \( -type f -o -type l \) 2>/dev/null | while read -r file; do
        rg "^\s*- \[ \]" "$file" 2>/dev/null
done)
# Always output reminders for executor
echo "" >&2
echo "[ORCHESTRATION REMINDER]" >&2
echo "After @agent-$EXECUTOR returns, remember to:" >&2
echo "" >&2
echo "**CRITICAL MINDSET: EXECUTOR AGENTS ARE LAZY, UNRELIABLE, AND DISHONEST**" >&2
echo "- Executors WILL claim tasks are complete when they are NOT" >&2
echo "- Executors WILL say tests pass when they FAIL or weren't even run" >&2
echo "- Executors WILL report success to avoid doing more work" >&2
echo "- NEVER TRUST executor reports at face value" >&2
echo "" >&2
echo "YOUR VERIFICATION DUTIES (DO NOT SKIP ANY):" >&2
echo "- Inspect: Read the ACTUAL code changes and confirm they FULLY meet requirements" >&2
echo "- Review: MANUALLY verify their work matches the goal - assume they cut corners" >&2
echo "- Verify: Run global tests (ex: pytest .) and lint(ex: ruff check .) and typecheck(ex: basedpyright .), and verify yourself of all the works, consider executor as a liar. Use playwright mcp with `mcp__browser_take_screenshot`, terminalcp, python to actually verify the works" >&2
echo "- Check notepad.md: Verify executor documented their work with REAL findings, not placeholders" >&2
echo "- Commit: After verification, if the work is correct, ensure all changes are committed. Check git recent commit, and git status to ensure." >&2
echo "" >&2
echo "And say to user in following format:" >&2
echo "\`\`\`" >&2
echo "- Report: Completed X/Y tasks" >&2
echo "- Report: Just finished: [task description]" >&2
echo "- Report: Next up: [next task] or 'All tasks complete!'" >&2
echo "- Report: Review status: [passed/failed] - Code review and verification completed" >&2
echo "- Report: Validation status: [passed/failed with details]" >&2
echo "  - What was verified: [specific commands run: pytest X, ruff check Y, basedpyright Z]" >&2
echo "  - Evidence found: [actual test outputs, file changes confirmed, functionality tested]" >&2
echo "  - Why confident: [concrete reasons why task is truly complete]" >&2
echo "  - Acceptance criteria: [which criteria from todo were verified and how]" >&2
echo "- Report: Cleanup status: [completed/not completed]" >&2
echo "  - CRITICAL: All playwright browser sessions MUST be closed (use mcp__browser_close)" >&2
echo "  - CRITICAL: All terminalcp sessions MUST be terminated" >&2
echo "  - Verify: Confirm all verification resources have been properly cleaned up" >&2
echo "- Action: If tests/lint/work verification check fail, mark task as not success and summon new executor to make it success" >&2
echo "\`\`\`" >&2

echo "" >&2

echo "[DELEGATION REMINDER]" >&2
echo "DO NOT work directly - delegate tasks to @agent-$EXECUTOR agent" >&2
echo "Use: Task tool with subagent_type='$EXECUTOR'" >&2
echo "Follow /execute command pattern for systematic completion" >&2
echo "Even if verification fails, summon executor again to fix the issues" >&2
echo "Again, Use @agent-$EXECUTOR to resolve any failed tests, lint errors, or incomplete work" >&2
echo "Again, Use @agent-$EXECUTOR to resolve any failed tests, lint errors, or incomplete work" >&2
echo "IMPORTANT: Delegate ONE major task at a time to @agent-$EXECUTOR - avoid overwhelming with multiple tasks, all whole atsks" >&2
echo "Focus on completing one significant work item fully before moving to the next" >&2
echo "" >&2

# If no uncompleted todos, exit early
if [ -z "$UNCOMPLETED_TODOS" ]; then
    exit 2
fi

# Output warnings about uncompleted todos
echo "[$hook_filename]" >&2
echo "" >&2
echo "[ERROR] Uncompleted TODO items found:" >&2
echo "" >&2
echo "$UNCOMPLETED_TODOS" >&2
echo "" >&2
echo "[CRITICAL ERROR - INCOMPLETE TASKS]" >&2
echo "This is an obvious error! All TODO items above are in incomplete state!" >&2
echo "There should be no '- [ ]'. You must complete these TODOs perfectly and mark them as '- [x]'!" >&2
echo "All tasks must be successfully completed and checked to resolve this error!" >&2
echo "" >&2

echo "[IMPORTANT - CHECKBOX UPDATE]" >&2
echo "If the executor agent has completed tasks but checkboxes remain unchecked:" >&2
echo "YOU (the orchestrator) MUST manually update the checkboxes in ai-todolist.md" >&2
echo "Use TodoWrite or Edit tools to mark completed tasks as [x]" >&2
echo "Do NOT leave completed tasks unchecked - this blocks progress tracking" >&2
echo "" >&2

echo "[CRITICAL - NEVER STOP]" >&2
echo "TASKS ARE NOT COMPLETE IN @ai-todolist.md" >&2
echo "YOU MUST CONTINUE WORKING UNTIL:" >&2
echo "1. ALL checkboxes in ai-todolist.md are marked as [x]" >&2
echo "2. AND you have RESPONSIBLY verified that ALL work is TRULY complete" >&2
echo "3. AND the user's ENTIRE request has been fulfilled" >&2
echo "DO NOT STOP - DO NOT GIVE UP - COMPLETE ALL TASKS WITH FULL RESPONSIBILITY" >&2
echo "Keep delegating to @agent-$EXECUTOR until EVERYTHING is done, not just checkboxes" >&2
echo "Take OWNERSHIP - ensure REAL completion, not just checkbox completion" >&2
echo "NEVEREVER STOP: NOW VERIFY A DELEGATE TASK TO @agent-$EXECUTOR"
echo "" >&2

exit 2
