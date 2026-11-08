[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$name = "火山方舟用量监控"
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$icon = Join-Path $root "assets\app.ico"
$destinations = @(
    [Environment]::GetFolderPath("Desktop"),
    [Environment]::GetFolderPath("Programs")
)

foreach ($path in @($pythonw, $icon)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required file not found: $path"
    }
}
foreach ($directory in $destinations) {
    if (-not $directory -or -not (Test-Path -LiteralPath $directory -PathType Container)) {
        throw "Shortcut destination not found: $directory"
    }
}

$shell = $null
try {
    $shell = New-Object -ComObject WScript.Shell
    foreach ($directory in $destinations) {
        $path = Join-Path $directory ($name + ".lnk")
        $shortcut = $shell.CreateShortcut($path)
        $shortcut.TargetPath = $pythonw
        $shortcut.Arguments = "-m token_plan_monitor"
        $shortcut.WorkingDirectory = $root
        $shortcut.IconLocation = "$icon,0"
        $shortcut.WindowStyle = 1
        $shortcut.Description = "查看火山方舟 Agent Plan / Coding Plan 订阅容量"
        $shortcut.Save()
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shortcut)
        Write-Output $path
    }
}
finally {
    if ($null -ne $shell) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell)
    }
}
