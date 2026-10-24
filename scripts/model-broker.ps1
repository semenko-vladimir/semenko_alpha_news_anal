param(
    [ValidateSet('status', 'health', 'configure')][string]$Action = 'status',
    [switch]$Replace,
    [string]$BrokerHost = 'model-broker',
    [string]$BrokerScript = '/opt/alpha-broker/broker.py'
)
$ErrorActionPreference = 'Stop'
if ($Replace -and $Action -ne 'configure') { throw '-Replace is only for explicit interactive configure.' }
if ($BrokerHost -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,252}\z') { throw 'Invalid broker host.' }
if ($BrokerScript -cnotmatch '^/[A-Za-z0-9_./-]+\z' -or ($BrokerScript.Split('/') -contains '..')) { throw 'Invalid broker script path.' }
$brokerArgs = @($BrokerHost, 'python3', $BrokerScript, $Action)
if ($Action -eq 'configure') {
    $brokerArgs = @('-t') + $brokerArgs
    if ($Replace) { $brokerArgs += '--replace' }
} else {
    $brokerArgs = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10') + $brokerArgs
}
& ssh @brokerArgs
exit $LASTEXITCODE
