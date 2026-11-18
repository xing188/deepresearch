$ErrorActionPreference = 'Stop'
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Please right-click allow-mobile.bat and choose Run as administrator.' }
$connection = Get-NetIPConfiguration | Where-Object { $_.NetAdapter.HardwareInterface -and $_.IPv4DefaultGateway } | Select-Object -First 1
if (-not $connection) { throw 'Connect Wi-Fi or Ethernet first.' }
$address = @($connection.IPv4Address.IPAddress | Where-Object { $_ -notlike '169.254.*' })[0]
if (-not $address) { throw 'No usable Wi-Fi address was found.' }
$name = 'DeepResearch-Phone-LocalSubnet'
$existing = Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
if ($existing) { $existing | Remove-NetFirewallRule }
New-NetFirewallRule -Name $name -DisplayName 'DeepResearch phone access (local subnet only)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 -LocalAddress $address -RemoteAddress LocalSubnet -InterfaceAlias $connection.InterfaceAlias -Profile Any | Out-Null
Write-Host "Phone access allowed on http://${address}:8000/ for this local subnet only."
Write-Host 'This rule does not change the network category or enable Internet port forwarding.'
