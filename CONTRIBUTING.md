# Contributing

## 1. Introduction

Thank you for your interest in contributing to this project.

This repository maintains a Streamlit-based auditing tool for validating `ads.txt` and `app-ads.txt` partner declarations. Contributions of all sizes are welcome, including bug fixes, documentation improvements, test coverage, UX refinements, and carefully scoped feature work.

The goal of this guide is to make the contribution process predictable, reviewable, and low-friction. Please read it before opening an issue or pull request so your contribution can move through triage and review efficiently.

## 2. I Have a Question

Please do **not** use the GitHub issue tracker for general usage questions, setup support, debugging help for local environments, or broad discussion topics.

The issue tracker is reserved for:

- Reproducible bugs
- Concrete feature requests
- Clearly scoped maintenance work

If you have a question, use one of these channels instead:

- **GitHub Discussions** for project-specific usage questions, design discussions, and troubleshooting
- **Stack Overflow** for framework or language questions related to Python, Streamlit, `pandas`, or `requests`
- Any dedicated community or team communication channel used by maintainers for contributor support

When asking a question outside the issue tracker, include enough context for someone else to help you effectively:

- What you are trying to achieve
- What you expected to happen
- What actually happened
- Your operating system and Python version
- Any relevant logs, screenshots, or sample input

If, after discussion, it becomes clear that you have found a reproducible bug or identified a worthwhile enhancement, you can then open a formal issue with the details outlined below.

## 3. Reporting Bugs

High-quality bug reports are essential. A good report should allow a maintainer to reproduce the problem quickly without needing multiple rounds of clarification.

Before opening a bug report:

- Search existing open issues and recently closed issues for duplicates
- Confirm the problem still occurs on the latest version of the default branch
- Verify the issue is reproducible with minimal, representative input
- Remove secrets, customer data, and private endpoints from any logs or screenshots

When filing a bug report, include all of the following.

### Search Duplicates

Check whether the same bug has already been reported. If you find a related issue:

- Add any missing reproduction details there instead of opening a new issue
- Reference similar issues if you believe your report is related but not identical

Duplicate issues slow down triage and fragment debugging history.

### Environment

Include a concise environment block, for example:

- Operating system and version
- Python version
- Streamlit version
- `pandas` version
- `requests` version
- Browser and version, if the issue is UI-related
- Project revision, release tag, or commit SHA

For this repository, the following command is a good starting point for environment details:

```bash
python --version
python -m pip show streamlit pandas requests
```

### Steps to Reproduce

Provide a deterministic, step-by-step algorithm that another contributor can execute verbatim.

A strong reproduction section usually includes:

1. Exact setup conditions
2. Exact commands that were run
3. Exact sample `ads.txt` or `app-ads.txt` reference lines used
4. Exact target domains or mocked fixtures used for validation
5. Any configuration choices made in the UI
6. The point at which the failure becomes visible

Keep the reproduction as small as possible while still triggering the bug.

### Expected vs. Actual Behavior

Always separate the expected outcome from the observed outcome.

Use a structure like:

- **Expected behavior:** The application should mark the partner declaration as `Valid` when domain, publisher ID, and account type match.
- **Actual behavior:** The application marks the declaration as `Not found` even though the fetched file contains the exact record.

Also include:

- Error messages
- Tracebacks
- Screenshots, if the problem affects rendering or interaction flow
- Sanitized response payloads or excerpts, when network behavior is involved

### Bug Report Quality Checklist

Before submitting, make sure your report answers these questions:

- Can a maintainer reproduce the issue from the information provided?
- Is the problem isolated to a single concern?
- Did you remove credentials and sensitive publisher data?
- Did you verify that the report is not a duplicate?
- Did you explain why the current behavior is incorrect?

## 4. Suggesting Enhancements

Enhancement proposals are welcome, but they need to be concrete.

Use enhancement issues for:

- New end-user capabilities
- Improvements to validation accuracy or diagnostics
- Better export workflows
- Testing or maintainability improvements
- Architectural changes that materially improve reliability, performance, or contributor experience

Avoid vague requests such as “improve performance” or “make the UI better” without specific scope.

### Justification

Explain the exact problem the enhancement solves.

Good justification answers questions like:

- What workflow is currently blocked or inefficient?
- What is difficult, ambiguous, or error-prone today?
- Why is the existing behavior insufficient?
- Why should this change live in the project instead of an external wrapper or deployment-specific customization?

### Use Cases

Provide concrete, real-world usage scenarios.

Examples of strong use-case framing:

- An AdOps analyst needs to validate hundreds of domains and export only mismatched reseller declarations.
- A maintainer needs deterministic fixtures to validate the HTTP fallback logic without relying on live publisher endpoints.
- A contributor needs modular validation helpers so unit tests can be added without exercising the full Streamlit UI.

The more specific your use case is, the easier it is to evaluate scope, impact, and priority.

### What to Include in an Enhancement Proposal

A strong feature request should include:

- A short summary of the proposed change
- The problem statement
- Why the change is valuable
- Constraints, tradeoffs, or risks
- Example input and expected output
- Whether the change affects UI behavior, exported data, or validation logic
- Whether backward compatibility should be preserved

If the proposal is architectural in nature, describe:

- Which modules or responsibilities would change
- Whether the change is incremental or requires a larger refactor
- How the proposal will be tested

## 5. Local Development / Setup

This repository is currently a Python application built around Streamlit, with primary logic in `app.py`.

### Fork and Clone

1. Fork the repository to your own GitHub account.
2. Clone your fork locally.
3. Add the upstream remote so you can sync with the main project.

```bash
git clone https://github.com/<your-user>/Ads.txt-App-ads.txt-line-Checker.git
cd Ads.txt-App-ads.txt-line-Checker
git remote add upstream https://github.com/<upstream-owner>/Ads.txt-App-ads.txt-line-Checker.git
```

### Dependencies

Create a virtual environment and install the runtime dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If you use Windows PowerShell, activate the environment with:

```powershell
.\.venv\Scripts\Activate.ps1
```

### Environment Variables

This repository does **not** currently require a committed `.env.example` file or mandatory runtime environment variables for basic local development.

If future changes introduce configuration through environment variables:

- Document each variable in `README.md`
- Add a `.env.example` template when appropriate
- Never commit secrets, tokens, or internal endpoint credentials

If your contribution depends on environment-based configuration, include setup instructions in your pull request and update the documentation as part of the same change.

### Running Locally

Start the application locally with:

```bash
streamlit run app.py
```

By default, Streamlit prints a local development URL, typically:

```text
http://localhost:8501
```

Recommended local verification steps:

1. Launch the application
2. Paste sample target domains
3. Paste representative reference lines
4. Run both `ads.txt` and `app-ads.txt` validation modes
5. Verify filtering and CSV export behavior

## 6. Pull Request Process

Pull requests should be small, focused, and easy to review.

Before opening a pull request:

- Rebase or merge the latest upstream default branch into your work
- Ensure your branch contains only relevant changes
- Run the local validation steps in the Testing section
- Update documentation when behavior, setup, or workflows change

### Branching Strategy

Use descriptive branch names with one of the following prefixes:

- `feature/<short-description>` for new features
- `bugfix/<issue-number-or-short-description>` for defect fixes
- `docs/<short-description>` for documentation-only changes
- `refactor/<short-description>` for internal restructuring with no intended behavior change
- `test/<short-description>` for test-only changes

Examples:

- `feature/horizontal-export-filtering`
- `bugfix/123-soft-404-detection`
- `docs/contributing-guide`

### Commit Messages

This project expects **Conventional Commits**.

Use formats such as:

- `feat: add account-type mismatch summary`
- `fix: handle HTML response served from ads.txt endpoint`
- `docs: add contribution workflow guide`
- `refactor: extract fetch retry helper`
- `test: add parser regression coverage`

Keep commit messages imperative, specific, and scoped to the actual change.

### Upstream Synchronization

Before submitting your PR, synchronize with the upstream default branch:

```bash
git fetch upstream
git checkout main
git pull --ff-only upstream main
git checkout <your-branch>
git rebase main
```

If the project uses a different default branch name, replace `main` accordingly.

### PR Description

Your PR body should include:

- A concise summary of the change
- The problem it solves
- Links to related issues or discussions
- Implementation notes or tradeoffs
- Evidence of testing performed
- Screenshots or short recordings for UI changes, when applicable
- Any follow-up work that is intentionally out of scope

A strong PR description should make it obvious:

- Why the change is needed
- How it was implemented
- How reviewers should verify it
- What risks or edge cases deserve attention

## 7. Styleguides

Contributors are expected to keep changes consistent with the current codebase and avoid unrelated cleanup.

### Code Quality Expectations

- Prefer small, composable functions when touching validation logic
- Keep naming explicit and domain-oriented
- Avoid broad rewrites unless the pull request is explicitly a refactor
- Preserve user-visible behavior unless the PR intentionally changes it
- Update documentation when setup, behavior, or workflows change

### Linters and Formatters

This repository does **not** currently commit project-level configuration for dedicated linters or formatters such as `Black`, `Ruff`, `Flake8`, or `isort`.

Because no formatter is enforced in-repo at this time:

- Follow existing Python style already present in the codebase
- Keep imports, naming, and spacing consistent within the touched file
- Avoid drive-by reformatting unrelated sections
- If you use local tooling, ensure it does not introduce unnecessary churn

If maintainers later add formatter or linter configuration, contributors should treat that configuration as authoritative and update this document accordingly.

### Architectural Patterns and Naming Conventions

The current application follows a pragmatic, UI-first structure:

- `app.py` contains both Streamlit UI code and core validation logic
- Helper functions are used for fetch, parsing, and validation steps
- Status values such as `Valid`, `Partially matched`, `Not found`, and `Error` are part of the user-facing result model

When contributing:

- Reuse existing status vocabulary unless a change explicitly evolves the result model
- Keep parsing and network behavior deterministic where possible
- Isolate new logic into helper functions when it materially improves testability or readability
- Prefer clear function names that reflect domain behavior, not implementation trivia

## 8. Testing

All new behavior changes and bug fixes must be accompanied by relevant verification.

At minimum, contributors should validate:

- The application still starts successfully
- The modified code is syntactically valid
- Any changed parsing, matching, or fetch logic behaves as intended
- New user-visible behavior is documented where appropriate

### Required Test Expectations

- Bug fixes should include a regression test where practical
- New features should include unit or integration coverage where practical
- If automated tests are not yet available for the area you touched, explain the manual validation you performed in the PR

### Local Test Commands

Run the following checks locally before submitting:

```bash
python -m pip check
python -m py_compile app.py
streamlit run app.py
```

If you add an automated test suite in a future contribution, document the exact command needed to run it, for example:

```bash
pytest
```

Do not submit a pull request that introduces failing tests or a broken local startup path without explicitly discussing it first.

## 9. Code Review Process

After a pull request is opened, maintainers will review it for correctness, scope, clarity, and maintainability.

### What Happens After Opening a PR

1. A maintainer or reviewer performs initial triage
2. The pull request is checked for scope, description quality, and reproducibility
3. Reviewers assess code quality, behavior changes, and test coverage
4. Feedback is requested if clarification or revisions are needed
5. Once approved and considered merge-ready, the pull request may be merged by a maintainer

### Who Reviews the Code

Code review is handled by repository maintainers and any contributors explicitly authorized to review changes.

Review focus typically includes:

- Functional correctness
- Clarity of implementation
- Impact on UX and exported results
- Regression risk
- Documentation completeness
- Adequacy of testing evidence

### Approval Expectations

Unless a maintainer states otherwise, contributors should assume:

- At least **one maintainer approval** is required before merge
- Larger or riskier changes may require additional review
- Documentation-only changes may move faster, but still require maintainer sign-off

### Addressing Review Feedback

When changes are requested:

- Respond to every substantive review comment
- Push focused follow-up commits instead of force-editing history repeatedly during review
- Summarize what changed after addressing feedback
- Request re-review once comments have been resolved

If you disagree with a review comment, discuss it respectfully and ground your response in reproducibility, maintainability, user impact, or project direction.

### Re-Review and Merge Readiness

A pull request is generally considered ready to merge when:

- Review feedback has been addressed
- Required approvals have been received
- Tests or validation checks are documented and acceptable
- The branch is up to date with the target branch, if requested
- Documentation is updated where needed

Maintainers may still choose to defer or close a pull request if it is out of scope, too large to review safely, insufficiently tested, or misaligned with the project roadmap.
