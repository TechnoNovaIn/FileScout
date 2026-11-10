# FileScout Auto-Start and Persistent Watchdog Service
# Runs NGINX on port 3001 and FastAPI on port 8000 inside WSL2.
# Keeps services alive 24/7 across reboots and desktop sessions.

param(
    [switch]$Foreground
)

$logFile = "C:\Filescout\startup.log"
function Log-Msg($msg) {
    $time = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $entry = "[$time] $msg"
    Add-Content -Path $logFile -Value $entry -ErrorAction SilentlyContinue
    if ($Foreground) {
        Write-Host $entry
    }
}

# 1. Mutex protection with graceful recovery from abandoned locks
$mutexName = "Local\FileScout_Watchdog_Mutex"
$mutex = $null
$hasLock = $false

try {
    $mutex = New-Object System.Threading.Mutex($true, $mutexName, [ref]$hasLock)
} catch [System.Threading.AbandonedMutexException] {
    # Lock was abandoned by an ungracefully terminated process: we acquire it
    $hasLock = $true
} catch {
    $hasLock = $false
}

if (-not $hasLock) {
    Log-Msg "Another watchdog instance appears to be running. Checking service health..."
    try {
        $res = Invoke-RestMethod -Uri "http://localhost:3001/api/health" -TimeoutSec 4 -ErrorAction Stop
        if ($res.status -eq "ok") {
            Log-Msg "FileScout is healthy and operational on port 3001 (via NGINX)."
            if ($Foreground) {
                Write-Host "FileScout is already running: http://localhost:3001"
            }
            exit 0
        }
    } catch {
        Log-Msg "Health check failed on existing instance: $_. Proceeding to take over..."
    }
}

Log-Msg "=== Starting FileScout Watchdog Service (PID $PID) ==="

# Helper to find LAN IPv4
function Get-LanIP {
    $ip = (Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias "Wi-Fi*", "Ethernet*", "vEthernet*" -ErrorAction SilentlyContinue |
        Where-Object { 
            $_.IPAddress -notlike "127.*" -and 
            $_.IPAddress -notlike "169.254.*" -and 
            $_.IPAddress -notlike "172.17.*" -and 
            $_.IPAddress -notlike "172.18.*" -and 
            $_.IPAddress -notlike "172.19.*" -and 
            $_.IPAddress -notlike "172.20.*" -and 
            $_.IPAddress -notlike "172.21.*" -and 
            $_.IPAddress -notlike "172.22.*" -and 
            $_.IPAddress -notlike "172.23.*" -and 
            $_.IPAddress -notlike "172.24.*" -and 
            $_.IPAddress -notlike "172.25.*" -and 
            $_.IPAddress -notlike "172.26.*" -and 
            $_.IPAddress -notlike "172.27.*" -and 
            $_.IPAddress -notlike "172.28.*" -and 
            $_.IPAddress -notlike "172.29.*" -and 
            $_.IPAddress -notlike "172.30.*" -and 
            $_.IPAddress -notlike "172.31.*" -and 
            $_.IPAddress -notlike "192.168.137.*" 
        } | Select-Object -First 1).IPAddress
    return $ip
}

# Helper to configure portproxy to WSL NGINX
function Update-PortProxy($targetWslIp) {
    if (-not $targetWslIp -or $targetWslIp -notmatch '^\d+\.\d+\.\d+\.\d+$') {
        return
    }

    Log-Msg "Configuring Windows portproxy 3001, 3003, 3004, 4002 -> ${targetWslIp} (WSL)..."

    foreach ($p in @(3001, 3003, 3004, 4002)) {
        try {
            netsh interface portproxy delete v4tov4 listenport=$p listenaddress=0.0.0.0 | Out-Null
            netsh interface portproxy add v4tov4 listenport=$p listenaddress=0.0.0.0 connectport=$p connectaddress=$targetWslIp | Out-Null
        } catch {}
    }

    $lanIp = Get-LanIP
    if ($lanIp) {
        foreach ($p in @(3001, 3003, 3004, 4002)) {
            try {
                netsh interface portproxy delete v4tov4 listenport=$p listenaddress=$lanIp | Out-Null
                netsh interface portproxy add v4tov4 listenport=$p listenaddress=$lanIp connectport=$p connectaddress=$targetWslIp | Out-Null
                Log-Msg "Configured LAN portproxy: ${lanIp}:$p -> ${targetWslIp}:$p"
            } catch {}
        }
    }
}

# Ensure Firewall rules are present for 3001, 3003, 3004, and 4002
try {
    $fw = Get-NetFirewallRule -DisplayName "FileScout Port 3001 (LAN)" -ErrorAction SilentlyContinue
    if (-not $fw) {
        New-NetFirewallRule -DisplayName "FileScout Port 3001 (LAN)" -Direction Inbound -LocalPort 3001 -Protocol TCP -Action Allow -ErrorAction SilentlyContinue | Out-Null
        Log-Msg "Created Windows Firewall rule for port 3001."
    }
    $fw3003 = Get-NetFirewallRule -DisplayName "Balaji Fleet Port 3003 (LAN)" -ErrorAction SilentlyContinue
    if (-not $fw3003) {
        New-NetFirewallRule -DisplayName "Balaji Fleet Port 3003 (LAN)" -Direction Inbound -LocalPort 3003 -Protocol TCP -Action Allow -ErrorAction SilentlyContinue | Out-Null
        Log-Msg "Created Windows Firewall rule for port 3003."
    }
    $fw3004 = Get-NetFirewallRule -DisplayName "SnapIT AI Port 3004 (LAN)" -ErrorAction SilentlyContinue
    if (-not $fw3004) {
        New-NetFirewallRule -DisplayName "SnapIT AI Port 3004 (LAN)" -Direction Inbound -LocalPort 3004 -Protocol TCP -Action Allow -ErrorAction SilentlyContinue | Out-Null
        Log-Msg "Created Windows Firewall rule for port 3004."
    }
    $fw4002 = Get-NetFirewallRule -DisplayName "BPCL Dashboard Port 4002 (LAN)" -ErrorAction SilentlyContinue
    if (-not $fw4002) {
        New-NetFirewallRule -DisplayName "BPCL Dashboard Port 4002 (LAN)" -Direction Inbound -LocalPort 4002 -Protocol TCP -Action Allow -ErrorAction SilentlyContinue | Out-Null
        Log-Msg "Created Windows Firewall rule for port 4002."
    }
} catch {}

# Start WSL, NGINX, filescout API, SnapIT, and BPCL Dashboard
Log-Msg "Activating WSL, NGINX, filescout API, SnapIT, and BPCL Dashboard..."
wsl.exe -d Ubuntu -u root systemctl start nginx filescout snapit bpcl-dashboard

$lastKnownWslIp = ""
$consecutiveFailures = 0

try {
    while ($true) {
        try {
            # 1. Verify NGINX and filescout.service
            $nginxStatus = (wsl.exe -d Ubuntu -u root systemctl is-active nginx 2>$null)
            if ($nginxStatus) { $nginxStatus = $nginxStatus.Trim() }

            if ($nginxStatus -ne "active") {
                Log-Msg "nginx.service is '$nginxStatus'. Starting nginx..."
                wsl.exe -d Ubuntu -u root systemctl start nginx
                Start-Sleep -Seconds 1
            }

            $apiStatus = (wsl.exe -d Ubuntu -u root systemctl is-active filescout 2>$null)
            if ($apiStatus) { $apiStatus = $apiStatus.Trim() }

            if ($apiStatus -ne "active") {
                Log-Msg "filescout.service is '$apiStatus'. Starting API..."
                wsl.exe -d Ubuntu -u root systemctl start filescout
                Start-Sleep -Seconds 1
            }

            $snapitStatus = (wsl.exe -d Ubuntu -u root systemctl is-active snapit 2>$null)
            if ($snapitStatus) { $snapitStatus = $snapitStatus.Trim() }

            if ($snapitStatus -ne "active") {
                Log-Msg "snapit.service is '$snapitStatus'. Starting SnapIT..."
                wsl.exe -d Ubuntu -u root systemctl start snapit
                Start-Sleep -Seconds 1
            }

            $bpclStatus = (wsl.exe -d Ubuntu -u root systemctl is-active bpcl-dashboard 2>$null)
            if ($bpclStatus) { $bpclStatus = $bpclStatus.Trim() }

            if ($bpclStatus -ne "active") {
                Log-Msg "bpcl-dashboard.service is '$bpclStatus'. Starting BPCL Dashboard..."
                wsl.exe -d Ubuntu -u root systemctl start bpcl-dashboard
                Start-Sleep -Seconds 1
            }

            # 2. Check WSL IP address
            $rawIp = (wsl.exe -d Ubuntu hostname -I 2>$null)
            $currentWslIp = ""
            if ($rawIp) {
                $currentWslIp = ($rawIp.Trim().Split(' ')[0]).Trim()
            }

            if ($currentWslIp -match '^\d+\.\d+\.\d+\.\d+$') {
                if ($currentWslIp -ne $lastKnownWslIp -or (Test-Path "C:\Filescout\trigger_proxy.txt")) {
                    Log-Msg "WSL IP detected or reload requested: $currentWslIp (old: '$lastKnownWslIp')"
                    Remove-Item "C:\Filescout\trigger_proxy.txt" -Force -ErrorAction SilentlyContinue
                    Update-PortProxy $currentWslIp
                    $lastKnownWslIp = $currentWslIp
                }
            }

            # 3. Health check against localhost:3001 (served by NGINX)
            try {
                $res = Invoke-RestMethod -Uri "http://localhost:3001/api/health" -TimeoutSec 3 -ErrorAction Stop
                if ($res.status -eq "ok") {
                    $consecutiveFailures = 0
                } else {
                    $consecutiveFailures++
                }
            } catch {
                $consecutiveFailures++
                Log-Msg "Health check failed (attempt $consecutiveFailures): $_"
            }

            # 4. Self-healing if health checks fail 3 consecutive times
            if ($consecutiveFailures -ge 3) {
                Log-Msg "WARNING: 3 consecutive health check failures. Performing self-healing restart..."
                wsl.exe -d Ubuntu -u root systemctl restart filescout nginx
                Restart-Service iphlpsvc -ErrorAction SilentlyContinue
                if ($lastKnownWslIp) {
                    Update-PortProxy $lastKnownWslIp
                }
                $consecutiveFailures = 0
            }
        } catch {
            Log-Msg "Watchdog loop exception: $_"
        }

        # Keep WSL alive: periodic query keeps WSL session active
        Start-Sleep -Seconds 10
    }
} finally {
    if ($mutex) {
        try {
            $mutex.ReleaseMutex()
            $mutex.Dispose()
        } catch {}
    }
    Log-Msg "=== FileScout Watchdog Service Stopped ==="
}



