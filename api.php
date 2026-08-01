<?php
// api.php - JSON API for controlling speed-cam.py from the web interface
// Expects: ?action=start|stop|restart|status

header('Content-Type: application/json');
header('Cache-Control: no-store');

function respond($data) {
    echo json_encode($data, JSON_PRETTY_PRINT);
    exit;
}

$action = isset($_GET['action']) ? strtolower(trim($_GET['action'])) : '';
$validActions = ['start', 'stop', 'restart', 'status'];

if (!in_array($action, $validActions)) {
    respond([
        'ok' => false,
        'error' => 'Invalid action. Use: start, stop, restart, or status.',
        'action' => $action,
    ]);
}

$scriptDir = __DIR__;
$ctlScript = $scriptDir . '/speed-camctl.sh';

// Make sure control script exists and is executable
if (!file_exists($ctlScript)) {
    respond([
        'ok' => false,
        'error' => 'Control script not found: speed-camctl.sh',
    ]);
}

if (!is_executable($ctlScript)) {
    @chmod($ctlScript, 0755);
}

$command = escapeshellcmd($ctlScript) . ' ' . escapeshellarg($action) . ' 2>&1';
$output = shell_exec($command);

if ($output === null) {
    respond([
        'ok' => false,
        'error' => 'Failed to execute control script. Check permissions.',
        'hint' => 'The web server user may need sudo privileges to manage processes.',
        'command' => $command,
    ]);
}

$output = trim($output);
$lines = array_filter(explode("\n", $output));
$lastLine = end($lines) ?: $output;

// Parse status line: "running|1234" or "stopped|0"
$isRunning = false;
$pid = null;
if (strpos($lastLine, '|') !== false) {
    list($status, $pidVal) = explode('|', $lastLine, 2);
    $isRunning = ($status === 'running');
    $pid = $pidVal;
} elseif (stripos($output, 'already running') !== false || stripos($output, 'running') !== false) {
    $isRunning = true;
}

respond([
    'ok' => true,
    'action' => $action,
    'running' => $isRunning,
    'pid' => $pid,
    'output' => $output,
]);
