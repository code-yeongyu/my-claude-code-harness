---
name: pr-creator
description: "GitHub Pull Request creation specialist. Analyzes user requirements to create PRs with structured titles and bodies matching the user's query language. Handles git change analysis, PR draft creation, user confirmation, and final PR creation via gh CLI."
---

# PR Creator

Workflow for creating GitHub Pull Requests. Analyzes changes, writes clear PRs matching the user's query language, and creates them via gh CLI after user confirmation.

**Language Policy:** Always match the user's query language. If the user writes in Korean, write the PR in Korean. If the user writes in English, write the PR in English.

## Workflow

### 0. Create TODO list for tracking

**CRITICAL: ALWAYS start by creating a TODO list using TodoWrite tool.**

Create todos for all workflow steps:

```json
{
  "todos": [
    {"content": "Gather git status", "status": "pending", "activeForm": "Gathering git status"},
    {"content": "Analyze changes deeply", "status": "pending", "activeForm": "Analyzing changes deeply"},
    {"content": "Extract or ask for ticket ID", "status": "pending", "activeForm": "Extracting or asking for ticket ID"},
    {"content": "Write PR title", "status": "pending", "activeForm": "Writing PR title"},
    {"content": "Detect PR template", "status": "pending", "activeForm": "Detecting PR template"},
    {"content": "Write PR body", "status": "pending", "activeForm": "Writing PR body"},
    {"content": "Save PR draft and get user confirmation", "status": "pending", "activeForm": "Saving PR draft and getting user confirmation"},
    {"content": "Create PR via gh CLI", "status": "pending", "activeForm": "Creating PR via gh CLI"}
  ]
}
```

Update each todo's status to "in_progress" when starting that step, and "completed" when finished.

### 1. Gather git status

Run the parallel git status script to gather all repository information:

```bash
python scripts/get_git_status.py
```

This script executes these commands in parallel using asyncio:
- `git status` - current branch and changes
- `git diff` - unstaged changes
- `git diff --staged` - staged changes
- `git log --oneline -10` - recent commit history
- `git branch --show-current` - current branch name

The script returns JSON output with all results. Parse this to understand the current state.

### 2. Analyze changes deeply

Use ultrathink to analyze:
- What problem was solved?
- What was implemented/added/modified?
- Why was this change needed?
- What technical approach was taken?
- What is the user's core intent to emphasize?

### 3. Extract or ask for ticket ID

**Try to extract ticket ID from branch name first:**

Common patterns to detect:
- `feature/TICKET-123-description` → `TICKET-123`
- `bugfix/PROJ-456-fix-something` → `PROJ-456`
- `feat/ABC-789` → `ABC-789`
- `username/ISSUE-123-description` → `ISSUE-123`

Regular expression pattern: `([A-Z]+-\d+)` or `([A-Z]+\d+)`

**If ticket ID not found in branch name OR user hasn't explicitly mentioned ticket ID:**

Use AskUserQuestion to ask:

```json
{
  "questions": [{
    "question": "이 작업과 관련된 티켓 ID가 있나요?",
    "header": "티켓 ID",
    "multiSelect": false,
    "options": [
      {"label": "있음", "description": "티켓 ID를 입력하세요 (예: TICKET-123)"},
      {"label": "없음", "description": "관련 티켓이 없습니다"}
    ]
  }]
}
```

**If user query is in English:**

```json
{
  "questions": [{
    "question": "Is there a ticket ID related to this work?",
    "header": "Ticket ID",
    "multiSelect": false,
    "options": [
      {"label": "Yes", "description": "Enter ticket ID (e.g., TICKET-123)"},
      {"label": "No", "description": "No related ticket"}
    ]
  }]
}
```

**Skip asking if:**
- User explicitly mentioned ticket ID in their request
- User explicitly said "no ticket" or "티켓 없음"

### 4. Write PR title

**Format:**
- **If ticket ID exists**: `[TICKET-ID] {descriptive title}`
- **If no ticket ID**: `{descriptive title}`

**Required rules:**
- **Ticket ID prefix**: If ticket ID was found or provided, add `[TICKET-ID]` at the start
- **Noun form ending**: End with noun form in the user's query language
- **Specific and clear**: Immediately understandable
- **Emphasize core**: Reflect what the user wants to highlight
- **Around 50-60 characters** (excluding ticket ID prefix): Keep concise but informative

**Prohibited:**
- NO conventional commit prefixes (`fix:`, `feat:`, `refactor:`)
- NO vague expressions like "bug fix", "improvement"
- NO technical term lists only

**Good examples:**
- "[PROJ-123] Fix dmypy incorrectly inferring all Django models as Reel type"
- "[ISSUE-456] Implement Instagram Reels bulk collection API"
- "[TICKET-789] Improve response speed by adding search result caching"
- "Fix dmypy incorrectly inferring all Django models as Reel type" (no ticket)

**Bad examples:**
- "[PROJ-123] fix: type error"
- "[ISSUE-456] bug fix"
- "performance improvement"
- "PROJ-123: Fix type error" (wrong format - must use brackets)

### 5. Detect PR template

**CRITICAL: ALWAYS run the PR template detection script. DO NOT skip this step.**

**You MUST run this Python script to detect PR templates:**

```bash
python scripts/detect_pr_template.py
```

**Why this is critical:**
- Each repository may have its own PR template structure
- Using the repository's template ensures consistency with team conventions
- The script handles git worktree environments correctly
- Skipping this step results in incorrect PR format

**What the script does:**
- Searches for PULL_REQUEST_TEMPLATE.md in standard locations (.github/, docs/, root)
- Works correctly in git worktree environments
- Returns JSON with template path and content if found

**IMPORTANT: You must parse the JSON output and follow the instructions:**
- If `"found": true` → **USE THE TEMPLATE STRUCTURE** from `"content"` field. DO NOT use fallback structure.
- If `"found": false` → Use the fallback structure below

**Example output when found:**
```json
{
  "found": true,
  "path": ".github/PULL_REQUEST_TEMPLATE.md",
  "content": "## Description\n\n## Changes\n..."
}
```

**What to do:** Extract the `"content"` field and use that exact structure for the PR body.

### 6. Write PR body

**If template found:** Follow the repository's template structure exactly. Fill in each section based on the analyzed changes.

**If template not found (fallback):** Use the structure matching the user's query language.

**Structure template:**
```markdown
## Background
{Explain why this work was needed. Describe the problem situation or requirements specifically}

## Changes
{Describe what and how things were changed. Include technical approach}
- List major changes as bullet points
- Balance technical details with understandable explanations

## Testing
{How to verify this PR works properly. Omit this section if not applicable}
1. Specific test steps
2. Expected results
3. Commands to run automated tests if available

## Review Notes
{Parts that reviewers should pay special attention to or additional context. Omit if not applicable}
- Areas of special focus
- Potential risks or trade-offs
- Future improvement opportunities

## Screenshots
{If there are UI changes or visual demonstrations. Omit if not applicable}
```

**Note:** Write the entire PR content in the same language as the user's query. If the user writes in Korean, use Korean section titles and content. If in English, use English.

### 7. Save PR draft

Save the draft to a temporary file:

```bash
/tmp/pull-request-{topic}-{timestamp}.md
```

Show the content to the user and ask for confirmation in their language.

### 8. Create PR

**Only proceed after user approval.**

1. Check for uncommitted changes:
   ```bash
   git status
   ```

2. If needed, create new branch:
   ```bash
   git checkout -b {username}/{branch-name}
   ```

3. If there are unstaged changes, create commit (use existing git commit workflow)

4. Push to remote:
   ```bash
   git push -u origin {branch-name}
   ```

5. Create PR using gh CLI:
   ```bash
   gh pr create --title "{title}" --body "$(cat /tmp/pull-request-{topic}-{timestamp}.md)" --base main
   ```

6. Return PR URL to user

## Key considerations

**User intent understanding:**
- Identify what the user wants to emphasize
- Focus on business value or problem solving over technical achievement
- Clearly communicate "why this is important"

**Balance technical accuracy and readability:**
- Use technical terms precisely, but add context
- Wrap code changes in backticks
- Add simple analogies or explanations for complex concepts

**Reviewer perspective:**
- Structure for quick understanding
- Emphasize important changes
- Provide clear testing methods

## Prohibited actions

- NO conventional commit prefixes in PR title
- NO vague or generic descriptions
- NO technical term lists only
- NO PR creation without user confirmation
- NO PR creation with uncommitted changes

## Final checklist

Before creating PR, verify:
- [ ] Title matches user's query language?
- [ ] Title is specific and clear?
- [ ] Body follows the required structure?
- [ ] Balance between technical details and explanations is appropriate?
- [ ] User's core intent is well communicated?
- [ ] All changes are committed?
- [ ] User confirmation received?
