<?php
// SQLite database file path
//$dbFile = '/home/admin/speed-camera/data/speed_cam.db';

// SQLite database file path (relative to the PHP script)
$dbDir = __DIR__ . '/data';
$dbFile = $dbDir . '/speed_cam.db';

try {
    // Connect to SQLite database
    $pdo = new PDO('sqlite:' . $dbFile);

    // Set the PDO error mode to exception
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);

    // Query to fetch speed from the database (assuming 'speed' is a column in a table)
    $stmt = $pdo->query('SELECT ave_speed FROM speed LIMIT 1'); // Adjust your_table_name as per your database structure

    // Fetch speed value (assuming there's only one row fetched)
    $speed = $stmt->fetchColumn();

    // Output the speed value
    echo $speed;

} catch (PDOException $e) {
    // Handle database connection or query errors
    echo 'Error fetching speed: ' . $e->getMessage();
}
