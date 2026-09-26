[CmdletBinding()]
param(
    [string]$Repo = "snehithgit/phonepe-analyser",
    [string]$Branch = "main",
    [string]$CommitMessage = "Update PhonePe Analyser source",
    [string]$InitialVersion = "0.1.0",
    [switch]$BuildLocal,
    [switch]$ForceGeneratedFiles
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$BackendImage = "ghcr.io/snehithgit/phonepe-analyser-backend"
$FrontendImage = "ghcr.io/snehithgit/phonepe-analyser-frontend"

function Assert-LastExitCode {
    param([string]$Message)

    if ($LASTEXITCODE -ne 0) {
        throw "$Message (exit code $LASTEXITCODE)"
    }
}

function Write-Utf8NoBomLf {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)][string]$Content,
        [switch]$Force
    )

    if ((Test-Path -LiteralPath $Path -PathType Leaf) -and (-not $Force)) {
        Write-Host "Keeping existing: $Path"
        return
    }

    $Parent = Split-Path -Parent $Path
    if ($Parent -and (-not (Test-Path -LiteralPath $Parent))) {
        New-Item -ItemType Directory -Path $Parent -Force | Out-Null
    }

    $Content = $Content -replace "`r`n", "`n"
    $Content = $Content -replace "`r", "`n"

    $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText(
        [System.IO.Path]::GetFullPath($Path),
        $Content,
        $Utf8NoBom
    )

    Write-Host "Wrote: $Path"
}

function Ensure-GitIgnoreLine {
    param([Parameter(Mandatory=$true)][string]$Line)

    $Path = ".\.gitignore"
    $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        [System.IO.File]::WriteAllText(
            [System.IO.Path]::GetFullPath($Path),
            "$Line`n",
            $Utf8NoBom
        )
        return
    }

    $Existing = @(Get-Content -LiteralPath $Path)
    if ($Existing -notcontains $Line) {
        $Text = [System.IO.File]::ReadAllText([System.IO.Path]::GetFullPath($Path))
        if ($Text.Length -gt 0 -and (-not $Text.EndsWith("`n"))) {
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
# 1. Verify PhonePe Analyser project root
# ---------------------------------------------------------------------------

$RequiredFiles = @(
    ".\backend\app\main.py",
    ".\backend\requirements.txt",
    ".\backend\Dockerfile",
    ".\frontend\package.json",
    ".\frontend\src\App.tsx",
    ".\frontend\Dockerfile",
    ".\docker-compose.yml"
)

$Missing = @(
    $RequiredFiles |
    Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) }
)

if ($Missing.Count -gt 0) {
    throw (
        "Run this script from the PhonePe Analyser project root.`n" +
        "Missing expected files:`n" +
        ($Missing -join "`n")
    )
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is not installed or not in PATH."
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI (gh) is not installed or not in PATH."
}

Write-Host ""
Write-Host "==> Checking GitHub authentication" -ForegroundColor Cyan

& gh auth status
if ($LASTEXITCODE -ne 0) {
    throw "GitHub CLI is not authenticated. Run: gh auth login"
}

& gh auth setup-git
Assert-LastExitCode "Failed to configure GitHub authentication for Git"

$Login = (& gh api user --jq ".login").Trim()
Assert-LastExitCode "Failed to read GitHub login"

$UserId = (& gh api user --jq ".id").Trim()
Assert-LastExitCode "Failed to read GitHub user ID"

Write-Host "GitHub user: $Login" -ForegroundColor Green
Write-Host "Repository : https://github.com/$Repo" -ForegroundColor Green

# Confirm the destination repository exists and is accessible.
& gh repo view $Repo --json nameWithOwner *> $null
Assert-LastExitCode "GitHub repository '$Repo' is not accessible"

# ---------------------------------------------------------------------------
# 2. Generate publish/deployment support files
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "==> Preparing GitHub Actions / GHCR deployment files" -ForegroundColor Cyan

$BackendDockerIgnore = @'
__pycache__
*.pyc
*.pyo
.pytest_cache
*.db
*.sqlite
*.sqlite3
.env
.env.*
tests/__pycache__
'@

$FrontendDockerIgnore = @'
node_modules
dist
.env
.env.*
npm-debug.log*
yarn-debug.log*
yarn-error.log*
'@

$RegistryCompose = @'
services:
  backend:
    image: ghcr.io/snehithgit/phonepe-analyser-backend:latest
    container_name: phonepe-analyser-backend
    restart: unless-stopped
    environment:
      PHONEPE_DB_PATH: /data/phonepe.db
    volumes:
      - ./data:/data
    expose:
      - "8000"

  frontend:
    image: ghcr.io/snehithgit/phonepe-analyser-frontend:latest
    container_name: phonepe-analyser
    restart: unless-stopped
    depends_on:
      - backend
    ports:
      - "8088:80"
'@

$Workflow = @'
name: Test and publish Docker images

on:
  push:
    branches:
      - main
  workflow_dispatch:

permissions:
  contents: read
  packages: write

concurrency:
  group: phonepe-analyser-${{ github.ref }}
  cancel-in-progress: true

jobs:
  backend-tests:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout source
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: "pip"
          cache-dependency-path: backend/requirements.txt

      - name: Install backend dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r backend/requirements.txt

      - name: Run backend tests
        working-directory: backend
        env:
          PYTHONPATH: .
        run: |
          python -m unittest discover -s tests -v

  docker:
    needs:
      - backend-tests

    runs-on: ubuntu-latest

    strategy:
      fail-fast: false
      matrix:
        include:
          - component: backend
            image: ghcr.io/snehithgit/phonepe-analyser-backend
          - component: frontend
            image: ghcr.io/snehithgit/phonepe-analyser-frontend

    steps:
      - name: Checkout source
        uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Read version
        id: version
        shell: bash
        run: |
          if [ -f VERSION ]; then
            VERSION="$(tr -d '\r\n ' < VERSION)"
          else
            VERSION="dev"
          fi

          VERSION="${VERSION#v}"

          if [ -z "$VERSION" ]; then
            VERSION="dev"
          fi

          echo "value=$VERSION" >> "$GITHUB_OUTPUT"

      - name: Build and publish ${{ matrix.component }}
        uses: docker/build-push-action@v6
        with:
          context: ./${{ matrix.component }}
          file: ./${{ matrix.component }}/Dockerfile
          platforms: linux/amd64
          push: true
          tags: |
            ${{ matrix.image }}:latest
            ${{ matrix.image }}:v${{ steps.version.outputs.value }}
            ${{ matrix.image }}:sha-${{ github.sha }}
          cache-from: type=gha,scope=${{ matrix.component }}
          cache-to: type=gha,mode=max,scope=${{ matrix.component }}
          provenance: mode=max
          sbom: true
'@

Write-Utf8NoBomLf `
    -Path ".\backend\.dockerignore" `
    -Content $BackendDockerIgnore `
    -Force:$ForceGeneratedFiles

Write-Utf8NoBomLf `
    -Path ".\frontend\.dockerignore" `
    -Content $FrontendDockerIgnore `
    -Force:$ForceGeneratedFiles

Write-Utf8NoBomLf `
    -Path ".\docker-compose.ghcr.yml" `
    -Content $RegistryCompose `
    -Force:$ForceGeneratedFiles

Write-Utf8NoBomLf `
    -Path ".\.github\workflows\docker-publish.yml" `
    -Content $Workflow `
    -Force:$ForceGeneratedFiles

if (-not (Test-Path -LiteralPath ".\VERSION" -PathType Leaf)) {
    Write-Utf8NoBomLf `
        -Path ".\VERSION" `
        -Content "$InitialVersion`n"
}
else {
    Write-Host "Keeping existing: .\VERSION"
}

# Runtime/private files must never be committed.
Ensure-GitIgnoreLine "data/"
Ensure-GitIgnoreLine "*.db"
Ensure-GitIgnoreLine "*.sqlite"
Ensure-GitIgnoreLine "*.sqlite3"
Ensure-GitIgnoreLine ".env"
Ensure-GitIgnoreLine ".env.*"
Ensure-GitIgnoreLine "backend/__pycache__/"
Ensure-GitIgnoreLine "backend/app/__pycache__/"
Ensure-GitIgnoreLine "backend/tests/__pycache__/"
Ensure-GitIgnoreLine "backend/.pytest_cache/"
Ensure-GitIgnoreLine "frontend/node_modules/"
Ensure-GitIgnoreLine "frontend/dist/"
Ensure-GitIgnoreLine "PhonePe_Statement_*.csv"
Ensure-GitIgnoreLine "phonepe_statement_*.csv"
Ensure-GitIgnoreLine "*.zip"
Ensure-GitIgnoreLine "*.log"

# ---------------------------------------------------------------------------
# 3. Optional local validation/build
# ---------------------------------------------------------------------------

if ($BuildLocal) {
    Write-Host ""
    Write-Host "==> Running local backend tests" -ForegroundColor Cyan

    $PythonCommand = $null

    if (Get-Command python -ErrorAction SilentlyContinue) {
        $PythonCommand = "python"
    }
    elseif (Get-Command py -ErrorAction SilentlyContinue) {
        $PythonCommand = "py"
    }

    if ($PythonCommand) {
        Push-Location ".\backend"
        try {
            if ($PythonCommand -eq "py") {
                & py -3 -m unittest discover -s tests -v
            }
            else {
                & python -m unittest discover -s tests -v
            }
            Assert-LastExitCode "Backend tests failed"
        }
        finally {
            Pop-Location
        }
    }
    else {
        Write-Warning "Python not found; skipping local backend tests."
    }

    Write-Host ""
    Write-Host "==> Building Docker Compose stack locally" -ForegroundColor Cyan

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker is not installed or not in PATH."
    }

    & docker version
    Assert-LastExitCode "Docker is not available"

    & docker compose build --pull
    Assert-LastExitCode "Local Docker Compose build failed"

    Write-Host "Local backend/frontend Docker images built successfully." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# 4. Prepare Git repository without rewriting remote history
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "==> Preparing Git repository" -ForegroundColor Cyan

if (-not (Test-Path -LiteralPath ".\.git" -PathType Container)) {
    & git init
    Assert-LastExitCode "git init failed"

    & git remote add origin "https://github.com/$Repo.git"
    Assert-LastExitCode "Failed to add GitHub remote"

    # Attach to existing history when the destination repository already has it.
    # For a new/empty repository this fetch fails harmlessly and main is created.
    & git fetch origin $Branch *> $null

    if ($LASTEXITCODE -eq 0) {
        & git update-ref "refs/heads/$Branch" FETCH_HEAD
        Assert-LastExitCode "Failed to attach local branch to existing GitHub history"

        & git symbolic-ref HEAD "refs/heads/$Branch"
        Assert-LastExitCode "Failed to select branch"

        & git reset --mixed "refs/heads/$Branch" *> $null
        Assert-LastExitCode "Failed to prepare existing GitHub history"
    }
    else {
        & git branch -M $Branch
        Assert-LastExitCode "Failed to create branch $Branch"
    }
}
else {
    $CurrentBranch = (& git branch --show-current).Trim()
    Assert-LastExitCode "Failed to read current Git branch"

    if ([string]::IsNullOrWhiteSpace($CurrentBranch)) {
        & git branch -M $Branch
        Assert-LastExitCode "Failed to select branch $Branch"
    }
    elseif ($CurrentBranch -ne $Branch) {
        throw "Current branch is '$CurrentBranch'. Checkout '$Branch' before publishing."
    }

    & git remote get-url origin *> $null

    if ($LASTEXITCODE -eq 0) {
        & git remote set-url origin "https://github.com/$Repo.git"
        Assert-LastExitCode "Failed to update GitHub remote"
    }
    else {
        & git remote add origin "https://github.com/$Repo.git"
        Assert-LastExitCode "Failed to add GitHub remote"
    }

    # Fetch before committing so an existing remote history is never silently overwritten.
    & git fetch origin $Branch *> $null

    if ($LASTEXITCODE -eq 0) {
        $LocalHead = (& git rev-parse HEAD 2>$null)
        $RemoteHead = (& git rev-parse "origin/$Branch" 2>$null)

        if ($LASTEXITCODE -eq 0 -and
            (-not [string]::IsNullOrWhiteSpace($LocalHead)) -and
            (-not [string]::IsNullOrWhiteSpace($RemoteHead))) {

            & git merge-base --is-ancestor "origin/$Branch" HEAD

            if ($LASTEXITCODE -ne 0) {
                throw (
                    "Remote '$Branch' contains history that is not in this local checkout. " +
                    "Pull/reconcile it first. This publisher will never force-push."
                )
            }
        }
    }
}

& git config user.name $Login
Assert-LastExitCode "Failed to set Git user name"

& git config user.email "$UserId+$Login@users.noreply.github.com"
Assert-LastExitCode "Failed to set Git user email"

# ---------------------------------------------------------------------------
# 5. Safety check: refuse financial/private/runtime data
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "==> Checking publish safety" -ForegroundColor Cyan

& git add -A
Assert-LastExitCode "git add failed"

# Deliberately allows the sanitized test fixture:
# backend/tests/fixtures/phonepe_sample.csv
#
# Refuses:
# - real PhonePe statement filenames
# - SQLite/runtime databases
# - local data directory
# - environment/secrets
# - frontend dependency/build output
$Forbidden = '(?i)(^|/)(\.env($|\.)|data/|frontend/node_modules/|frontend/dist/|PhonePe_Statement_[^/]*\.csv$|phonepe_statement_[^/]*\.csv$|[^/]+\.(db|sqlite|sqlite3)$)'

$Tracked = @(& git ls-files)
Assert-LastExitCode "Failed to inspect tracked files"

$TrackedUnsafe = @(
    $Tracked |
    Where-Object {
        $_ -notmatch '(?i)(^|/)\.env\.example$' -and
        $_ -match $Forbidden
    }
)

if ($TrackedUnsafe.Count -gt 0) {
    Write-Error (
        "Refusing to publish because private/runtime files are already tracked:`n" +
        ($TrackedUnsafe -join "`n") +
        "`n`nRemove them from Git tracking before publishing."
    )
}

$Staged = @(& git diff --cached --name-only)
Assert-LastExitCode "Failed to inspect staged files"

$Unsafe = @(
    $Staged |
    Where-Object {
        $_ -notmatch '(?i)(^|/)\.env\.example$' -and
        $_ -match $Forbidden
    }
)

if ($Unsafe.Count -gt 0) {
    Write-Error (
        "Refusing to publish because private/runtime files are staged:`n" +
        ($Unsafe -join "`n")
    )
}

# Extra content scan for common accidental secrets in staged text files.
$SecretPatterns = @(
    'ghp_[A-Za-z0-9]{20,}',
    'github_pat_[A-Za-z0-9_]{20,}',
    'sk-[A-Za-z0-9_-]{20,}'
)

$StagedTextFiles = @(
    $Staged |
    Where-Object {
        $_ -match '(?i)\.(py|ps1|sh|ts|tsx|js|jsx|json|ya?ml|toml|ini|conf|md|txt|html|css)$'
    }
)

foreach ($File in $StagedTextFiles) {
    if (-not (Test-Path -LiteralPath $File -PathType Leaf)) {
        continue
    }

    $Text = [System.IO.File]::ReadAllText([System.IO.Path]::GetFullPath($File))

    foreach ($Pattern in $SecretPatterns) {
        if ($Text -match $Pattern) {
            throw "Refusing to publish: possible secret/token detected in '$File'."
        }
    }
}

# ---------------------------------------------------------------------------
# 6. Commit and push
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "Files/changes to publish:" -ForegroundColor Cyan

& git status --short
Assert-LastExitCode "git status failed"

& git diff --cached --quiet

if ($LASTEXITCODE -eq 0) {
    Write-Host "No source changes to publish." -ForegroundColor Yellow
}
else {
    & git commit -m $CommitMessage
    Assert-LastExitCode "git commit failed"

    # Never force-push.
    & git push -u origin $Branch
    Assert-LastExitCode "GitHub push failed; remote history was left unchanged"

    Write-Host ""
    Write-Host "PhonePe Analyser source uploaded without rewriting Git history." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# 7. Show GitHub Actions / GHCR status
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "==> GitHub Actions / Docker images" -ForegroundColor Cyan

Start-Sleep -Seconds 3

& gh run list --repo $Repo --workflow docker-publish.yml --limit 5

if ($LASTEXITCODE -ne 0) {
    Write-Warning "Could not list workflow runs yet. The Git push itself may still have succeeded."
}

Write-Host ""
Write-Host "Repository:" -ForegroundColor Green
Write-Host "  https://github.com/$Repo"

Write-Host ""
Write-Host "Docker images after the workflow completes:" -ForegroundColor Green
Write-Host "  $BackendImage`:latest"
Write-Host "  $FrontendImage`:latest"

if (Test-Path -LiteralPath ".\VERSION" -PathType Leaf) {
    $Version = (Get-Content -LiteralPath ".\VERSION" -Raw).Trim()

    if (-not [string]::IsNullOrWhiteSpace($Version)) {
        $Version = $Version.TrimStart("v")
        Write-Host "  $BackendImage`:v$Version"
        Write-Host "  $FrontendImage`:v$Version"
    }
}

Write-Host ""
Write-Host "To watch the newest workflow:" -ForegroundColor Cyan
Write-Host "  gh run watch --repo $Repo"

Write-Host ""
Write-Host "To run the published GHCR images:" -ForegroundColor Cyan
Write-Host "  docker compose -f docker-compose.ghcr.yml pull"
Write-Host "  docker compose -f docker-compose.ghcr.yml up -d"

Write-Host ""
Write-Host "Open:" -ForegroundColor Cyan
Write-Host "  http://127.0.0.1:8088"
