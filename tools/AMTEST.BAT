@echo off
d:\KEYS %2
c:
AMENU %1
if errorlevel 9 goto e9
for %%i in (8 7 6 5 4 3 2 1 0) do if errorlevel %%i if not errorlevel 9 echo %%i>>d:\AMRES.TXT
goto end
:e9
echo 9+>>d:\AMRES.TXT
:end
