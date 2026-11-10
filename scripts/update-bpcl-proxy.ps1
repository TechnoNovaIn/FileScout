param(
    [string]$targetWslIp
)

if (-not $targetWslIp) {
    $rawIp = (wsl.exe -d Ubuntu hostname -I 2>$null)
    if ($rawIp) {
        $targetWslIp = ($rawIp.Trim().Split(' ')[0]).Trim()
    }
}

if ($targetWslIp -match '^\d+\.\d+\.\d+\.\d+$') {
    $lanIp = (Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias "Wi-Fi*", "Ethernet*" -ErrorAction SilentlyContinue |
        Where-Object { 
            $_.IPAddress -notlike "127.*" -and 
            $_.IPAddress -notlike "169.254.*" -and 
            $_.IPAddress -notlike "172.*"
        } | Select-Object -First 1).IPAddress

    netsh interface portproxy delete v4tov4 listenport=4002 listenaddress=0.0.0.0 | Out-Null
    netsh interface portproxy add v4tov4 listenport=4002 listenaddress=0.0.0.0 connectport=4002 connectaddress=$targetWslIp | Out-Null

    if ($lanIp) {
        netsh interface portproxy delete v4tov4 listenport=4002 listenaddress=$lanIp | Out-Null
        netsh interface portproxy add v4tov4 listenport=4002 listenaddress=$lanIp connectport=4002 connectaddress=$targetWslIp | Out-Null
    }

    $fw = Get-NetFirewallRule -DisplayName "BPCL Dashboard Port 4002 (LAN)" -ErrorAction SilentlyContinue
    if (-not $fw) {
        New-NetFirewallRule -DisplayName "BPCL Dashboard Port 4002 (LAN)" -Direction Inbound -LocalPort 4002 -Protocol TCP -Action Allow -ErrorAction SilentlyContinue | Out-Null
    }
}

