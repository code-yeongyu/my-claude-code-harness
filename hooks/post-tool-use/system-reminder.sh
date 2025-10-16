#!/usr/bin/env bash

# Get current system time with timezone
CURRENT_TIME=$(date '+%Y-%m-%dT%H:%M:%S%z' | sed 's/\([0-9][0-9]\)$/:\1/')

# Print the system reminder
echo "" >&2
echo "[system-reminder]" >&2
echo "CURRENT SYSTEM TIME: $CURRENT_TIME (IT IS NOT 2024)" >&2
echo "User Lanuage: 한국어 (Korean)" >&2
echo "**사용자에게 항상, 무조건 한국어로 답변하세요.**" >&2
echo "Claude Language: English - make sure you think, and sequential think in English" >&2

# Git branch info (if in git repo)
if git rev-parse --git-dir > /dev/null 2>&1; then
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
fi

# Python info
if command -v python > /dev/null 2>&1; then
    PYTHON_VERSION=$(python --version 2>&1 | cut -d' ' -f2)
    if [[ -n "$VIRTUAL_ENV" ]]; then
        VENV_NAME=$(basename "$VIRTUAL_ENV")
        echo "Python: $PYTHON_VERSION (venv: $VENV_NAME)" >&2
    else
        echo "Python: $PYTHON_VERSION (global)" >&2
    fi
fi

# Virtual Environment Warning
CURRENT_DIR=$(pwd)
LOCAL_VENV=""
if [[ -d "$CURRENT_DIR/.venv" ]]; then
    LOCAL_VENV="$CURRENT_DIR/.venv"
    elif [[ -d "$CURRENT_DIR/venv" ]]; then
    LOCAL_VENV="$CURRENT_DIR/venv"
fi

# Resolve symbolic links for accurate comparison
REAL_VIRTUAL_ENV=""
REAL_LOCAL_VENV=""
if [[ -n "$VIRTUAL_ENV" ]]; then
    REAL_VIRTUAL_ENV=$(realpath "$VIRTUAL_ENV" 2>/dev/null || echo "$VIRTUAL_ENV")
fi
if [[ -n "$LOCAL_VENV" ]]; then
    REAL_LOCAL_VENV=$(realpath "$LOCAL_VENV" 2>/dev/null || echo "$LOCAL_VENV")
fi

if [[ -n "$LOCAL_VENV" && -n "$VIRTUAL_ENV" && "$REAL_VIRTUAL_ENV" != "$REAL_LOCAL_VENV" ]]; then
    echo "[WARNING] Virtual environment mismatch detected!" >&2
    echo "Current active venv: $VIRTUAL_ENV" >&2
    echo "Local project venv: $LOCAL_VENV" >&2
    echo "Consider using 'uv run' or 'poetry run' for project-specific commands." >&2
    echo "Or activate the correct venv: source $LOCAL_VENV/bin/activate" >&2
    elif [[ -n "$LOCAL_VENV" && -z "$VIRTUAL_ENV" ]]; then
    echo "[NOTICE] Local virtual environment found but not activated" >&2
    echo "Local project venv: $LOCAL_VENV" >&2
    echo "Consider using 'uv run' or 'poetry run' for project-specific commands." >&2
    echo "Or activate the venv: source $LOCAL_VENV/bin/activate" >&2
fi

echo "" >&2
echo "[CRITICAL SYSTEM CHECK]" >&2
echo "If you received ANY hook messages above, you MUST:" >&2
echo "1. ACKNOWLEDGE each hook message explicitly" >&2
echo "2. RESPOND to each hook's requirements SEPARATELY" >&2
echo "3. EXECUTE the required actions for EACH hook" >&2
echo "4. NEVER ignore or skip hook requirements" >&2
echo "Each hook message is MANDATORY and requires immediate action." >&2
echo "You must address EVERY hook message in priority order." >&2
echo "[TASK COMPLETION CHECKPOINT]" >&2
echo "Ask yourself RIGHT NOW:" >&2
echo "- Did I compromise on any user requirements? If YES, GO BACK and fix it." >&2
echo "- Did tests fail and I gave up? If YES, GO BACK and make them pass." >&2
echo "- Did I say 'I cannot' or 'This is difficult'? If YES, TRY HARDER." >&2
echo "- Did I skip any requested feature? If YES, IMPLEMENT IT NOW." >&2
echo "REMEMBER: Your job is to COMPLETE tasks, not to explain why they're hard." >&2
echo "If something didn't work, that's NOT acceptable. Make it work." >&2
echo "Tests MUST pass. Features MUST work. No exceptions." >&2
echo "" >&2
echo "Remember: Hook compliance is NON-NEGOTIABLE." >&2
echo "Your response must demonstrate clear understanding and execution of each hook's requirements." >&2

exit 2
