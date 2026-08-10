<?php
header('Content-Type: application/json');
header('Cache-Control: no-store');

$baseUrl = 'http://192.168.2.191/speed-camera';
$pageUrl = $baseUrl . '/';
$maxVisibleSpeed = 80;

function respond($payload, $statusCode = 200)
{
    http_response_code($statusCode);
    echo json_encode($payload, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
    exit;
}

function cleanText($value)
{
    return trim(html_entity_decode(strip_tags($value), ENT_QUOTES | ENT_HTML5, 'UTF-8'));
}

function absoluteMediaUrl($baseUrl, $path)
{
    if (!$path) {
        return null;
    }

    if (preg_match('/^https?:\/\//i', $path)) {
        return $path;
    }

    $parts = parse_url($baseUrl);
    $origin = ($parts['scheme'] ?? 'http') . '://' . ($parts['host'] ?? '192.168.2.191');
    if (isset($parts['port'])) {
        $origin .= ':' . $parts['port'];
    }

    if (strpos($path, '/') === 0) {
        return $origin . $path;
    }

    return rtrim($baseUrl, '/') . '/' . ltrim($path, '/');
}

function speedFromText($value)
{
    if (!$value || !preg_match('/(\d+(?:\.\d+)?)/', $value, $match)) {
        return null;
    }

    return (float) $match[1];
}

$context = stream_context_create([
    'http' => [
        'timeout' => 6,
        'ignore_errors' => true,
    ],
]);

$html = @file_get_contents($pageUrl, false, $context);

if ($html === false || trim($html) === '') {
    respond([
        'ok' => false,
        'error' => 'Unable to read the live HomeServer speed-camera page.',
        'source' => $pageUrl,
    ], 502);
}

$heading = null;
if (preg_match('/<header>\s*<h1>(.*?)<\/h1>/is', $html, $match)) {
    $heading = cleanText($match[1]);
}

$details = [];
if (preg_match_all('/<dt>(.*?)<\/dt>\s*<dd>(.*?)<\/dd>/is', $html, $matches, PREG_SET_ORDER)) {
    foreach ($matches as $match) {
        $details[cleanText($match[1])] = cleanText($match[2]);
    }
}

$records = [];
if (preg_match_all('/<tr><td>(.*?)<\/td><td>(.*?)<\/td><td><a href=[\'"](.*?)[\'"]>(.*?)<\/a><\/td><td>(.*?)<\/td><\/tr>/is', $html, $rows, PREG_SET_ORDER)) {
    foreach ($rows as $index => $row) {
        $filename = cleanText($row[4]);
        $speed = null;
        $units = 'kph';

        if (preg_match('/speed-(\d+(?:\.\d+)?)-/i', basename($filename), $speedMatch)) {
            $speed = (float) $speedMatch[1];
        }

        if ($speed !== null && $speed > $maxVisibleSpeed) {
            continue;
        }

        $records[] = [
            'idx' => $filename ?: ('live-' . $index),
            'log_timestamp' => cleanText($row[1]),
            'camera' => $details['Camera'] ?? 'Tapo Camera',
            'ave_speed' => $speed,
            'speed_units' => $units,
            'image_path' => $filename,
            'image_url' => absoluteMediaUrl($baseUrl, $row[3]),
            'direction' => '',
            'status' => cleanText($row[5]),
            'cam_location' => $details['Host'] ?? '192.168.2.191',
            'offset' => count($records),
            'ok' => true,
        ];
    }
}

$latestAuto = $details['Latest auto'] ?? '';
$latestAutoSpeed = speedFromText($latestAuto);
if ($latestAutoSpeed !== null && $latestAutoSpeed > $maxVisibleSpeed) {
    unset($details['Latest auto'], $details['Auto image']);
    $latestAuto = '';
}

$latest = $records[0] ?? [
    'idx' => 'live-camera',
    'log_timestamp' => $latestAuto,
    'camera' => $details['Camera'] ?? 'Tapo Camera',
    'ave_speed' => null,
    'speed_units' => 'kph',
    'image_path' => '',
    'image_url' => null,
    'direction' => '',
    'status' => 'live feed',
    'cam_location' => $details['Host'] ?? '192.168.2.191',
    'offset' => 0,
    'ok' => true,
];

if ($heading && preg_match('/^(\d+(?:\.\d+)?)\s+([a-z]+)\s+-\s+(.+)$/i', $heading, $match)) {
    $headingSpeed = (float) $match[1];

    if ($headingSpeed <= $maxVisibleSpeed) {
        $latest['ave_speed'] = $headingSpeed;
        $latest['speed_units'] = strtolower($match[2]);
        $latest['log_timestamp'] = $match[3];
    }
}

$latest['details'] = $details;
$latest['records'] = $records;
$latest['live_image_url'] = $baseUrl . '/live.jpg';
$latest['source'] = $pageUrl;
$latest['ok'] = true;

respond($latest);
