<?php
header('Content-Type: application/json');
header('Cache-Control: no-store');

$dbFile = __DIR__ . '/data/speed_cam.db';
$maxVisibleSpeed = 80;
$offset = filter_input(INPUT_GET, 'offset', FILTER_VALIDATE_INT);
$limit = filter_input(INPUT_GET, 'limit', FILTER_VALIDATE_INT);

if ($offset === false || $offset === null || $offset < 0) {
    $offset = 0;
}

if ($limit === false || $limit === null || $limit < 1) {
    $limit = 1;
}

$limit = min($limit, 50);

function respond($payload, $statusCode = 200)
{
    http_response_code($statusCode);
    echo json_encode($payload, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
    exit;
}

function webPath($path)
{
    if (!$path) {
        return null;
    }

    $path = str_replace('\\', '/', $path);
    $docRoot = isset($_SERVER['DOCUMENT_ROOT']) ? str_replace('\\', '/', realpath($_SERVER['DOCUMENT_ROOT'])) : null;
    $realPath = realpath(__DIR__ . '/' . $path);

    if ($docRoot && $realPath) {
        $realPath = str_replace('\\', '/', $realPath);
        if (strpos($realPath, $docRoot) === 0) {
            return '/' . ltrim(substr($realPath, strlen($docRoot)), '/');
        }
    }

    return ltrim($path, '/');
}

if (!file_exists($dbFile)) {
    respond([
        'ok' => false,
        'error' => 'Speed database has not been created yet. Start speed-cam.py on the Raspberry Pi to create data/speed_cam.db.',
        'db_path' => 'data/speed_cam.db',
    ], 404);
}

try {
    $pdo = new PDO('sqlite:' . $dbFile);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);

    $stmt = $pdo->prepare(
        'SELECT idx, log_timestamp, camera, ave_speed, speed_units, image_path, direction, status, cam_location
         FROM speed
         WHERE ave_speed IS NULL OR CAST(ave_speed AS REAL) <= :maxVisibleSpeed
         ORDER BY log_timestamp DESC, idx DESC
         LIMIT :limit OFFSET :offset'
    );
    $stmt->bindValue(':maxVisibleSpeed', $maxVisibleSpeed, PDO::PARAM_INT);
    $stmt->bindValue(':limit', $limit, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    $rows = $stmt->fetchAll(PDO::FETCH_ASSOC);

    if (!$rows) {
        respond([
            'ok' => false,
            'error' => 'No speed records found for this offset.',
            'offset' => $offset,
        ], 404);
    }

    foreach ($rows as $index => $row) {
        $rows[$index]['ave_speed'] = is_numeric($row['ave_speed']) ? (float) $row['ave_speed'] : null;
        $rows[$index]['image_url'] = webPath($row['image_path']);
        $rows[$index]['offset'] = $offset + $index;
        $rows[$index]['ok'] = true;
    }

    $row = $rows[0];

    if ($limit > 1) {
        $row['records'] = $rows;
    }

    respond($row);
} catch (PDOException $e) {
    respond([
        'ok' => false,
        'error' => 'Error fetching speed data: ' . $e->getMessage(),
    ], 500);
}
