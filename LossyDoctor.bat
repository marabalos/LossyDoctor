@echo off
setlocal EnableExtensions
chcp 65001 >nul
"%~dp0LossyDoctorBootstrap.exe" %*
set "EXITCODE=%ERRORLEVEL%"
echo.
if not "%EXITCODE%"=="0" (
  echo LossyDoctor termino con codigo %EXITCODE%.
) else (
  echo LossyDoctor finalizo correctamente.
)
echo.
echo Presione una tecla para cerrar esta ventana...
pause >nul
exit /b %EXITCODE%
