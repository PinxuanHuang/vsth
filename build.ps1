param([string]$OutputDirectory = 'dist')
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:PYINSTALLER_CONFIG_DIR = Join-Path $PSScriptRoot 'build/pyinstaller-cache'
if (-not (Test-Path '.venv/Scripts/python.exe')) {
    py -3.11 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Unable to create Python 3.11 environment.' }
}
& ./.venv/Scripts/python.exe -m pip install --no-cache-dir -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
# Stage Tcl/Tk locally so packaging also works in restricted build environments.
$pythonBase = & ./.venv/Scripts/python.exe -c 'import sys; print(sys.base_prefix)'
$tclStage = Join-Path $PSScriptRoot 'build/tcl-runtime'
New-Item -ItemType Directory -Force $tclStage | Out-Null
foreach ($library in @('tcl8.6', 'tk8.6')) {
    Copy-Item -LiteralPath (Join-Path $pythonBase "tcl/$library") -Destination $tclStage -Recurse -Force
}
$env:TCL_LIBRARY = Join-Path $tclStage 'tcl8.6'
$env:TK_LIBRARY = Join-Path $tclStage 'tk8.6'
& ./.venv/Scripts/python.exe -c 'import tkinter; root = tkinter.Tk(); root.withdraw(); root.destroy()'
if ($LASTEXITCODE -ne 0) { throw 'Tkinter check failed; repair the Python Tcl/Tk installation.' }
& ./.venv/Scripts/python.exe -m PyInstaller --noconfirm --clean --distpath $OutputDirectory --onefile --windowed --name MovieTicketAssistant --add-data 'demo.html;.' --add-data 'flow_fixture.html;.' --add-data 'session_fixture.html;.' --add-data 'booking_fixture.html;.' --add-data 'quantity_fixture.html;.' app.py
if ($LASTEXITCODE -ne 0) { throw 'Packaging failed.' }
Write-Host "Ready: $OutputDirectory/MovieTicketAssistant.exe (requires Microsoft Edge)"
