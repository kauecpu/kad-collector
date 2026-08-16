@echo off
setlocal
cd /d "%~dp0"

set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
  echo Preparando o ambiente local pela primeira vez...
  where py >nul 2>&1
  if not errorlevel 1 (
    py -3 -m venv .venv
  ) else (
    python -m venv .venv
  )
  if errorlevel 1 goto :setup_error
)

"%VENV_PYTHON%" -c "import kad_collector.guided_test" >nul 2>&1
if errorlevel 1 (
  echo Instalando as dependencias do KAD Collector...
  "%VENV_PYTHON%" -m pip install -e .
  if errorlevel 1 goto :setup_error
)

"%VENV_PYTHON%" -m kad_collector.cli testar %*
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" (
  echo.
  echo O teste terminou com erro. A mensagem acima indica o que precisa ser corrigido.
  pause
)
exit /b %RESULT%

:setup_error
echo.
echo Nao foi possivel preparar o ambiente. Confirme que o Python 3.11 ou superior esta instalado.
pause
exit /b 1
