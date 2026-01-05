@echo off
setlocal

set msg=%*
set msg=%msg:"=%
if "%msg%"=="" (
  for /f "delims=" %%i in ('powershell -NoProfile -Command "Get-Date -Format \"yyyy-MM-dd HH:mm:ss\""' ) do set msg=sync %%i
)

git add -A
git diff --cached --quiet
if errorlevel 1 (
  git commit -m "%msg%"
) else (
  echo No changes to commit.
)

git push
