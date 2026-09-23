param(
    [string]$BoardAddress = "172.20.11.156",
    [string]$BoardUser = "sunrise",
    [int]$WebPort = 8765,
    [int]$BoardTunnelPort = 18765,
    [string]$IdentityFile = "$env:USERPROFILE\.ssh\tactile_rdk_x5_v2"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$outputDir = Join-Path $projectRoot "output"
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

$createdNew = $false
$mutex = [Threading.Mutex]::new($true, "Local\TactileVisionV4Stack", [ref]$createdNew)
if (-not $createdNew) {
    exit 0
}

function Test-LocalPort([int]$Port) {
    $client = [Net.Sockets.TcpClient]::new()
    try {
        $result = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $result.AsyncWaitHandle.WaitOne(750, $false)) {
            return $false
        }
        $client.EndConnect($result)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Close()
    }
}

function Test-ServerProcess([int]$Port) {
    $needle = "app/main.py --host 0.0.0.0 --port $Port"
    return [bool](Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$needle*" })
}

function Resolve-Python {
    $candidates = @(
        (Join-Path $projectRoot ".venv\Scripts\python.exe"),
        "C:\Users\29913\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    throw "Python runtime not found."
}

if (-not (Test-Path -LiteralPath $IdentityFile)) {
    throw "RDK SSH identity file not found: $IdentityFile"
}

$python = Resolve-Python
$ssh = (Get-Command ssh.exe -ErrorAction Stop).Source
$manageServer = $true
$server = $null
$tunnel = $null
$serverOut = Join-Path $outputDir "v4-server.stdout.log"
$serverErr = Join-Path $outputDir "v4-server.stderr.log"
$tunnelOut = Join-Path $outputDir "rdk-tunnel.stdout.log"
$tunnelErr = Join-Path $outputDir "rdk-tunnel.stderr.log"

try {
    while ($true) {
        if ($manageServer -and -not (Test-LocalPort $WebPort)) {
            # A just-restarted server can briefly refuse a probe while its
            # listener is binding. Do not create a second server in that race.
            Start-Sleep -Seconds 1
            if (Test-ServerProcess $WebPort -or (Test-LocalPort $WebPort)) {
                Start-Sleep -Seconds 2
                continue
            }
            $server = Start-Process -FilePath $python `
                -ArgumentList @("app/main.py", "--host", "0.0.0.0", "--port", "$WebPort") `
                -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput $serverOut -RedirectStandardError $serverErr
            Start-Sleep -Seconds 2
        }

        if ($null -eq $tunnel -or $tunnel.HasExited) {
            $tunnelArgs = @(
                "-N", "-T",
                "-i", $IdentityFile,
                "-o", "BatchMode=yes",
                "-o", "IdentitiesOnly=yes",
                "-o", "ExitOnForwardFailure=yes",
                "-o", "ServerAliveInterval=15",
                "-o", "ServerAliveCountMax=3",
                "-R", "${BoardTunnelPort}:127.0.0.1:${WebPort}",
                "${BoardUser}@${BoardAddress}"
            )
            $tunnel = Start-Process -FilePath $ssh -ArgumentList $tunnelArgs `
                -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput $tunnelOut -RedirectStandardError $tunnelErr
        }

        Start-Sleep -Seconds 5
    }
}
finally {
    if ($tunnel -and -not $tunnel.HasExited) {
        Stop-Process -Id $tunnel.Id -ErrorAction SilentlyContinue
    }
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
