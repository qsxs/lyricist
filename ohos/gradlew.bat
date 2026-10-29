@echo off
setlocal
set "GRID_JAVA=java.exe"
if defined JAVA_HOME set "GRID_JAVA=%JAVA_HOME%\bin\java.exe"
"%GRID_JAVA%" -classpath "%~dp0gradle\wrapper\gradle-wrapper.jar" org.gradle.wrapper.GradleWrapperMain %*
set "GRID_EXIT=%ERRORLEVEL%"
endlocal & exit /b %GRID_EXIT%
