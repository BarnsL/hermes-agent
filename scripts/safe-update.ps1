# =============================================================================
# safe-update.ps1 - THE ONLY APPROVED WAY TO UPDATE HERMES ON THIS MACHINE
# =============================================================================
# RECURRING ISSUE R-1 (see RECURRING-ISSUES-AND-GAMEPLAN-2026-07-09.md in the
# Hermes home dir): `hermes update --branch main` hard-resets the repo to
# upstream HEAD, silently destroying every local fix (Discord channel-lock,
# backend-ready regex, pkg_guard, fail-closed model fallback, ...). That reset
# is the single biggest cause of "the same bug keeps coming back."
#
# This script replaces it with a rebase flow that PRESERVES local hardening:
#   1. Refuses to run if the working tree is dirty (commit first).
#   2. Fetches upstream main.
#   3. Rebases purple/hardening onto origin/main.
#   4. Verifies the hardening markers survived (regex, channel-lock, guards).
#   5. Runs scripts/hermes_doctor.py preflight.
#
# On rebase conflict it stops and tells you exactly what to do; it never
# force-resolves. Run from any shell:
#   powershell -NoProfile -File scripts\safe-update.ps1
# =============================================================================

$ErrorActionPreference = 'Stop'
$repo = "C:\Users\Burgboy\AppData\Local\hermes\hermes-agent"
$branch = "purple/hardening"

function Fail($msg) { Write-Host "[FAIL] $msg" -ForegroundColor Red; exit 1 }
function Ok($msg)   { Write-Host "[OK]   $msg" -ForegroundColor Green }

# --- 0) Preconditions ---------------------------------------------------------
$dirty = git -C $repo status --porcelain
if ($dirty) { Fail "Working tree is dirty. Commit or stash first:`n$dirty" }

$cur = git -C $repo rev-parse --abbrev-ref HEAD
if ($cur -ne $branch) { Fail "On branch '$cur', expected '$branch'. Run: git switch $branch" }
Ok "clean tree on $branch"

# --- 1) Fetch + rebase --------------------------------------------------------
git -C $repo fetch origin main
if ($LASTEXITCODE -ne 0) { Fail "git fetch failed" }
$behind = git -C $repo rev-list --count "$branch..origin/main"
Write-Host "Upstream has $behind new commit(s)."

if ([int]$behind -gt 0) {
    git -C $repo rebase origin/main
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "REBASE CONFLICT. Resolve manually:" -ForegroundColor Yellow
        Write-Host "  git -C $repo status            # see conflicted files"
        Write-Host "  <edit files>; git add <files>; git rebase --continue"
        Write-Host "  or abort safely: git -C $repo rebase --abort"
        exit 1
    }
    Ok "rebased $branch onto origin/main"
} else {
    Ok "already up to date with origin/main"
}

# --- 2) Verify the hardening survived (the checks that used to regress) --------
$checks = @(
    @{ File = "apps\desktop\electron\backend-ready.cjs"; Pattern = 'BACKEND\|DASHBOARD';           Why = "ready-token regex (CRITICAL #6/#9)" },
    @{ File = "gateway\run.py";                           Pattern = 'SECURITY RESTRICTION';         Why = "Discord guild tool lockdown" },
    @{ File = "gateway\run.py";                           Pattern = 'RECURRING ISSUE R-6';          Why = "fail-closed model fallback (R-6)" },
    @{ File = "agent\pkg_guard.py";                       Pattern = 'verify_package_health';        Why = "Defender-gutting auto-restore" },
    @{ File = "plugins\memory\memory_tencentdb\__init__.py"; Pattern = 'RECURRING ISSUE R-3';       Why = "tencentdb Windows guard (R-3)" }
)
$failed = $false
foreach ($c in $checks) {
    $p = Join-Path $repo $c.File
    if ((Test-Path $p) -and (Select-String -Path $p -Pattern $c.Pattern -Quiet)) {
        Ok $c.Why
    } else {
        Write-Host "[LOST] $($c.Why)  ($($c.File) missing pattern '$($c.Pattern)')" -ForegroundColor Red
        $failed = $true
    }
}
if ($failed) { Fail "One or more hardening markers were lost in the rebase. Inspect before running Hermes." }

# --- 3) Doctor preflight --------------------------------------------------------
$py = "$repo\venv\Scripts\python.exe"
if (Test-Path "$repo\scripts\hermes_doctor.py") {
    & $py "$repo\scripts\hermes_doctor.py"
    if ($LASTEXITCODE -ne 0) { Fail "hermes_doctor reported problems (see output above)" }
    Ok "doctor preflight clean"
} else {
    Write-Host "[WARN] scripts\hermes_doctor.py not found - skipping preflight" -ForegroundColor Yellow
}

Write-Host ""
Ok "safe update complete. Restart Hermes to pick up changes."
