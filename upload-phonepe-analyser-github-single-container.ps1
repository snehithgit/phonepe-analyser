[CmdletBinding()]
# PhonePe Analyser GitHub Publisher v1.2
# - Windows PowerShell native STDERR-safe
# - single Docker image/container for backend + frontend
param(
    [string]$Repo = "snehithgit/phonepe-analyser",
    [string]$Branch = "main",
    [string]$CommitMessage = "Update PhonePe Analyser",
    [switch]$BuildLocal
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Image = "ghcr.io/snehithgit/phonepe-analyser"

function Invoke-NativeSafe {
    param(
        [Parameter(Mandatory=$true)][scriptblock]$Command,
        [string]$FailureMessage = "Native command failed",
        [switch]$AllowFailure,
        [switch]$Quiet
    )

    $Previous = $ErrorActionPreference
    $ExitCode = 0
    try {
        # Git/gh/docker often write harmless progress to STDERR.
        # Windows PowerShell 5.1 can promote that to NativeCommandError
        # when ErrorActionPreference is Stop, so temporarily relax it.
        $ErrorActionPreference = "Continue"
        if ($Quiet) {
            & $Command *> $null
        } else {
            & $Command
        }
        $ExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $Previous
    }

    if (($ExitCode -ne 0) -and (-not $AllowFailure)) {
        throw "$FailureMessage (exit code $ExitCode)"
    }
    return $ExitCode
}

function Ensure-GitIgnoreLine {
    param([Parameter(Mandatory=$true)][string]$Line)

    $Path = ".\.gitignore"
    $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

    if (-not (Test-Path -LiteralPath $Path)) {
        [System.IO.File]::WriteAllText(
            [System.IO.Path]::GetFullPath($Path),
            "$Line`n",
            $Utf8NoBom
        )
        return
    }

    $Lines = @(Get-Content -LiteralPath $Path)
    if ($Lines -notcontains $Line) {
        $Text = [System.IO.File]::ReadAllText([System.IO.Path]::GetFullPath($Path))
        if ($Text.Length -gt 0 -and -not $Text.EndsWith("`n")) {
            $Text += "`n"
        }
        $Text += "$Line`n"
        [System.IO.File]::WriteAllText(
            [System.IO.Path]::GetFullPath($Path),
            ($Text -replace "`r`n", "`n" -replace "`r", "`n"),
            $Utf8NoBom
        )
    }
}

# ---------------------------------------------------------------------------
# Verify project root
# ---------------------------------------------------------------------------

$RequiredFiles = @(
    ".\Dockerfile",
    ".\docker-compose.yml",
    ".\backend\app\main.py",
    ".\backend\requirements.txt",
    ".\frontend\package.json",
    ".\frontend\src\App.tsx"
)

$Missing = @($RequiredFiles | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) })
if ($Missing.Count -gt 0) {
    throw ("Run this from the PhonePe Analyser project root.`nMissing:`n" + ($Missing -join "`n"))
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is not installed or not in PATH."
}
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI (gh) is not installed or not in PATH."
}

Write-Host ""
Write-Host "==> Checking GitHub authentication" -ForegroundColor Cyan

$AuthExit = Invoke-NativeSafe `
    -Command { gh auth status } `
    -FailureMessage "GitHub authentication check failed" `
    -AllowFailure

if ($AuthExit -ne 0) {
    throw "GitHub CLI is not authenticated. Run: gh auth login"
}

$null = Invoke-NativeSafe `
    -Command { gh auth setup-git } `
    -FailureMessage "Failed to configure GitHub authentication"

$Login = (& gh api user --jq ".login").Trim()
if ($LASTEXITCODE -ne 0) { throw "Could not read GitHub login." }

$UserId = (& gh api user --jq ".id").Trim()
if ($LASTEXITCODE -ne 0) { throw "Could not read GitHub user id." }

$null = Invoke-NativeSafe `
    -Command { gh repo view $Repo --json nameWithOwner } `
    -FailureMessage "Repository '$Repo' is not accessible" `
    -Quiet

Write-Host "GitHub user: $Login" -ForegroundColor Green
Write-Host "Repository : https://github.com/$Repo" -ForegroundColor Green
Write-Host "Image      : $Image`:latest" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Private/runtime files
# ---------------------------------------------------------------------------

Ensure-GitIgnoreLine "data/"
Ensure-GitIgnoreLine "*.db"
Ensure-GitIgnoreLine "*.sqlite"
Ensure-GitIgnoreLine "*.sqlite3"
Ensure-GitIgnoreLine ".env"
Ensure-GitIgnoreLine ".env.*"
Ensure-GitIgnoreLine "frontend/node_modules/"
Ensure-GitIgnoreLine "frontend/dist/"
Ensure-GitIgnoreLine "backend/__pycache__/"
Ensure-GitIgnoreLine "backend/app/__pycache__/"
Ensure-GitIgnoreLine "backend/tests/__pycache__/"
Ensure-GitIgnoreLine "backend/.pytest_cache/"
Ensure-GitIgnoreLine "PhonePe_Statement_*.csv"
Ensure-GitIgnoreLine "phonepe_statement_*.csv"
Ensure-GitIgnoreLine "*.zip"
Ensure-GitIgnoreLine "*.log"

# ---------------------------------------------------------------------------
# Optional local validation
# ---------------------------------------------------------------------------

if ($BuildLocal) {
    Write-Host ""
    Write-Host "==> Running backend tests" -ForegroundColor Cyan

    if (Get-Command python -ErrorAction SilentlyContinue) {
        Push-Location ".\backend"
        try {
            $null = Invoke-NativeSafe `
                -Command { python -m unittest discover -s tests -v } `
                -FailureMessage "Backend tests failed"
        }
        finally {
            Pop-Location
        }
    } else {
        Write-Warning "Python not found; skipping local backend tests."
    }

    Write-Host ""
    Write-Host "==> Building single Docker image locally" -ForegroundColor Cyan

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker is not installed or not in PATH."
    }

    $null = Invoke-NativeSafe `
        -Command { docker compose build --pull } `
        -FailureMessage "Docker build failed"
}

# ---------------------------------------------------------------------------
# Git repository setup
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "==> Preparing Git repository" -ForegroundColor Cyan

if (-not (Test-Path -LiteralPath ".\.git" -PathType Container)) {
    $null = Invoke-NativeSafe -Command { git init } -FailureMessage "git init failed"
}

$RemoteExit = Invoke-NativeSafe `
    -Command { git remote get-url origin } `
    -AllowFailure `
    -Quiet

if ($RemoteExit -eq 0) {
    $null = Invoke-NativeSafe `
        -Command { git remote set-url origin "https://github.com/$Repo.git" } `
        -FailureMessage "Failed to update origin"
} else {
    $null = Invoke-NativeSafe `
        -Command { git remote add origin "https://github.com/$Repo.git" } `
        -FailureMessage "Failed to add origin"
}

$null = Invoke-NativeSafe `
    -Command { git branch -M $Branch } `
    -FailureMessage "Failed to select branch '$Branch'"

# If the remote branch already exists, fetch it and ensure we do not overwrite
# history. If the repository is still empty, ls-remote simply returns non-zero.
$RemoteBranchExit = Invoke-NativeSafe `
    -Command { git ls-remote --exit-code --heads origin $Branch } `
    -AllowFailure `
    -Quiet

if ($RemoteBranchExit -eq 0) {
    $null = Invoke-NativeSafe `
        -Command { git fetch --quiet origin $Branch } `
        -FailureMessage "Failed to fetch existing GitHub history" `
        -Quiet

    $HasLocalHead = Invoke-NativeSafe `
        -Command { git rev-parse --verify HEAD } `
        -AllowFailure `
        -Quiet

    if ($HasLocalHead -eq 0) {
        $AncestorExit = Invoke-NativeSafe `
            -Command { git merge-base --is-ancestor "origin/$Branch" HEAD } `
            -AllowFailure `
            -Quiet

        if ($AncestorExit -ne 0) {
            throw (
                "Remote '$Branch' contains history not present locally. " +
                "Pull/reconcile it first. This script will never force-push."
            )
        }
    } else {
        # Brand-new local repo, existing remote branch:
        # attach to that history before staging local files.
        $null = Invoke-NativeSafe `
            -Command { git reset --mixed "origin/$Branch" } `
            -FailureMessage "Failed to attach local checkout to existing remote history" `
            -Quiet
    }
}

$null = Invoke-NativeSafe `
    -Command { git config user.name $Login } `
    -FailureMessage "Failed to configure Git user.name"

$null = Invoke-NativeSafe `
    -Command { git config user.email "$UserId+$Login@users.noreply.github.com" } `
    -FailureMessage "Failed to configure Git user.email"

# ---------------------------------------------------------------------------
# Stage and privacy checks
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "==> Checking publish safety" -ForegroundColor Cyan

$null = Invoke-NativeSafe -Command { git add -A } -FailureMessage "git add failed"

$Staged = @(& git diff --cached --name-only)
if ($LASTEXITCODE -ne 0) { throw "Unable to inspect staged files." }

$Forbidden = '(?i)(^|/)(\.env($|\.)|data/|frontend/node_modules/|frontend/dist/|PhonePe_Statement_[^/]*\.csv$|phonepe_statement_[^/]*\.csv$|[^/]+\.(db|sqlite|sqlite3)$)'

$Unsafe = @(
    $Staged |
    Where-Object {
        $_ -notmatch '(?i)(^|/)\.env\.example$' -and
        $_ -match $Forbidden
    }
)

if ($Unsafe.Count -gt 0) {
    throw ("Refusing to publish private/runtime files:`n" + ($Unsafe -join "`n"))
}

$SecretPatterns = @(
    'ghp_[A-Za-z0-9]{20,}',
    'github_pat_[A-Za-z0-9_]{20,}',
    'sk-[A-Za-z0-9_-]{20,}'
)

foreach ($File in $Staged) {
    if ($File -notmatch '(?i)\.(py|ps1|sh|ts|tsx|js|jsx|json|ya?ml|toml|ini|conf|md|txt|html|css|dockerfile)$') {
        continue
    }
    if (-not (Test-Path -LiteralPath $File -PathType Leaf)) {
        continue
    }

    $Content = [System.IO.File]::ReadAllText([System.IO.Path]::GetFullPath($File))
    foreach ($Pattern in $SecretPatterns) {
        if ($Content -match $Pattern) {
            throw "Possible secret/token detected in '$File'. Refusing to publish."
        }
    }
}

Write-Host ""
Write-Host "Files/changes to publish:" -ForegroundColor Cyan
$null = Invoke-NativeSafe -Command { git status --short } -FailureMessage "git status failed"

$DiffExit = Invoke-NativeSafe `
    -Command { git diff --cached --quiet } `
    -AllowFailure `
    -Quiet

if ($DiffExit -eq 0) {
    Write-Host "No source changes to publish." -ForegroundColor Yellow
} elseif ($DiffExit -eq 1) {
    $null = Invoke-NativeSafe `
        -Command { git commit -m $CommitMessage } `
        -FailureMessage "git commit failed"

    $null = Invoke-NativeSafe `
        -Command { git push -u origin $Branch } `
        -FailureMessage "GitHub push failed"

    Write-Host ""
    Write-Host "Source pushed successfully." -ForegroundColor Green
} else {
    throw "git diff returned unexpected exit code $DiffExit"
}

# ---------------------------------------------------------------------------
# Actions / GHCR
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "==> GitHub Actions" -ForegroundColor Cyan
Start-Sleep -Seconds 2

$RunExit = Invoke-NativeSafe `
    -Command { gh run list --repo $Repo --workflow docker-publish.yml --limit 5 } `
    -AllowFailure

if ($RunExit -ne 0) {
    Write-Warning "Workflow may not be visible yet. The Git push can still be successful."
}

Write-Host ""
Write-Host "Repository:" -ForegroundColor Green
Write-Host "  https://github.com/$Repo"

Write-Host ""
Write-Host "Single Docker image:" -ForegroundColor Green
Write-Host "  $Image`:latest"

if (Test-Path -LiteralPath ".\VERSION") {
    $Version = (Get-Content ".\VERSION" -Raw).Trim().TrimStart("v")
    if ($Version) {
        Write-Host "  $Image`:v$Version"
    }
}

Write-Host ""
Write-Host "Deploy from GHCR:" -ForegroundColor Cyan
Write-Host "  docker compose -f docker-compose.ghcr.yml pull"
Write-Host "  docker compose -f docker-compose.ghcr.yml up -d"

Write-Host ""
Write-Host "Open:" -ForegroundColor Cyan
Write-Host "  http://127.0.0.1:8088"
